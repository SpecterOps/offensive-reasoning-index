"""Real subprocess stderr bursts cannot exceed a private native worker log cap."""

import os
import subprocess
import sys
import time

import pytest

from ori.native_diagnostics import BoundedNativeDiagnostics


def _sink(tmp_path, **kwargs):
    path = tmp_path / "stderr.private.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        return path, BoundedNativeDiagnostics(fd, **kwargs)
    finally:
        os.close(fd)


def test_native_stderr_burst_drains_and_caps_file(tmp_path):
    path, sink = _sink(tmp_path, max_bytes=4096)
    process = subprocess.Popen(
        [sys.executable, "-c", "import os; os.write(2, b'x' * 2000000)"],
        stderr=sink, stdout=subprocess.DEVNULL,
    )
    assert process.wait(timeout=10) == 0
    sink.write("parent parser diagnostic\n")
    sink.close()
    assert sink.bytes_observed == 2_000_000 + len("parent parser diagnostic\n")
    assert path.stat().st_size <= 4096
    assert sink.drain_complete and sink.truncated
    assert b"truncated=true" in path.read_bytes()
    with pytest.raises(ValueError, match="NATIVE_DIAGNOSTIC_LIMIT_EXCEEDED"):
        sink.require_complete()


def test_native_stderr_retained_fd_has_bounded_drain_and_failure(tmp_path):
    path, sink = _sink(tmp_path, max_bytes=4096, drain_timeout=0.1)
    retained = os.dup(sink.fileno())
    try:
        start = time.monotonic()
        sink.close()
        assert time.monotonic() - start < 1.0
        assert not sink.drain_complete
        assert b"drain_complete=false" in path.read_bytes()
        with pytest.raises(ValueError, match="NATIVE_DIAGNOSTIC_DRAIN_FAILED"):
            sink.require_complete()
    finally:
        os.close(retained)


def test_native_stderr_clean_child_and_parent_share_budget(tmp_path):
    path, sink = _sink(tmp_path, max_bytes=4096)
    subprocess.run([sys.executable, "-c", "import os; os.write(2, b'child\\n')"],
                   stderr=sink, check=True, timeout=10)
    sink.write("parent\n")
    sink.close()
    sink.close()
    sink.require_complete()
    body = path.read_bytes()
    assert b"child\n" in body and b"parent\n" in body and b"truncated=false" in body
    assert sink.bytes_observed == 13
    assert path.stat().st_mode & 0o777 == 0o600
