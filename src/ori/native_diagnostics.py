"""Continuously drained, byte-bounded private stderr for owned native children."""

from __future__ import annotations

import os
import select
import threading
import time

NATIVE_DIAGNOSTIC_MAX_BYTES = 1_048_576
_MARKER_RESERVE = 512


class BoundedNativeDiagnostics:
    """One native worker's collector; overflow is discarded, never backpressured.

    The SDK receives only the pipe writer, not the private file descriptor. A
    dedicated reader retains a bounded prefix while draining everything else.
    The caller closes this object only after its SDK process owner has exited.
    Descendants retaining stderr cannot prevent the final bounded drain deadline.
    The same lock/budget covers SDK parser diagnostics written by the parent.
    """

    def __init__(self, private_fd: int, *, max_bytes=NATIVE_DIAGNOSTIC_MAX_BYTES,
                 drain_timeout=1.0):
        if type(max_bytes) is not int or max_bytes <= _MARKER_RESERVE:
            raise ValueError("NATIVE_DIAGNOSTIC_BYTE_LIMIT_INVALID")
        if type(drain_timeout) not in {int, float} or not 0 < drain_timeout <= 10:
            raise ValueError("NATIVE_DIAGNOSTIC_DRAIN_DEADLINE_INVALID")
        if os.fstat(private_fd).st_size:
            raise ValueError("NATIVE_DIAGNOSTIC_DESTINATION_NOT_EMPTY")
        self.max_bytes = max_bytes
        self.drain_timeout = drain_timeout
        self.bytes_observed = 0
        self.bytes_retained = 0
        self.truncated = False
        self.drain_complete = False
        self.write_failed = False
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._closed = False
        self._destination = os.dup(private_fd)
        self._reader, self._writer = os.pipe()
        self._thread = threading.Thread(target=self._collect, name="ori-native-stderr", daemon=True)
        self._thread.start()

    def fileno(self):
        if self._closed:
            raise ValueError("native diagnostic pipe is closed")
        return self._writer

    @property
    def closed(self):
        return self._closed

    def write(self, value: str):
        if not isinstance(value, str):
            raise TypeError("native diagnostic text required")
        self._consume(value.encode("utf-8", errors="replace"))
        return len(value)

    def flush(self):
        # Each retained byte is written immediately with os.write.
        return None

    def _write_all(self, value):
        while value:
            size = os.write(self._destination, value)
            if size <= 0:
                raise OSError("native diagnostic write made no progress")
            value = value[size:]
            self.bytes_retained += size

    def _consume(self, value):
        with self._lock:
            if self._closed:
                raise ValueError("native diagnostic sink is closed")
            self.bytes_observed += len(value)
            capacity = max(0, self.max_bytes - _MARKER_RESERVE - self.bytes_retained)
            self.truncated |= len(value) > capacity
            if self.write_failed:
                return
            try:
                self._write_all(value[:capacity])
            except OSError:
                self.write_failed = True

    def _collect(self):
        deadline = None
        try:
            while True:
                if self._closing.is_set() and deadline is None:
                    deadline = time.monotonic() + self.drain_timeout
                if deadline is not None and time.monotonic() >= deadline:
                    break
                ready, _, _ = select.select([self._reader], [], [], 0.05)
                if not ready:
                    continue
                chunk = os.read(self._reader, 65_536)
                if not chunk:
                    self.drain_complete = True
                    break
                self._consume(chunk)
        except (OSError, ValueError):
            self.write_failed = True
        finally:
            os.close(self._reader)
            with self._lock:
                marker = (
                    "\nORI_NATIVE_DIAGNOSTICS "
                    f"observed_bytes={self.bytes_observed} retained_bytes={self.bytes_retained} "
                    f"truncated={str(self.truncated).lower()} "
                    f"drain_complete={str(self.drain_complete).lower()} "
                    f"write_failed={str(self.write_failed).lower()}\n"
                ).encode("ascii")
                try:
                    self._write_all(marker[:self.max_bytes - self.bytes_retained])
                    os.fsync(self._destination)
                except OSError:
                    self.write_failed = True
                finally:
                    os.close(self._destination)
                    self._closed = True

    def close(self):
        """Close our pipe writer and drain, even if a descendant retains its copy."""
        if not self._closing.is_set():
            self._closing.set()
            os.close(self._writer)
        self._thread.join(self.drain_timeout + 0.2)
        if self._thread.is_alive():
            # The daemon retains its destination ownership until its finalizer.
            # This is a hard failure, never evidence of completed collection.
            self.write_failed = True

    def require_complete(self):
        if self.truncated:
            raise ValueError("NATIVE_DIAGNOSTIC_LIMIT_EXCEEDED")
        if self.write_failed or not self.drain_complete or not self._closed:
            raise ValueError("NATIVE_DIAGNOSTIC_DRAIN_FAILED")
