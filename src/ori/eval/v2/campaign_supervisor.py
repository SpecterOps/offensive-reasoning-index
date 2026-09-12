"""Bounded, external supervision for protocol V2 campaigns.

The supervisor deliberately treats :mod:`campaign_status` as its only campaign
state input.  It owns child-process persistence and operator budgets; ORI owns
all readiness, execution, checkpoint, retry, and completion decisions.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from .campaign_config import CampaignPurpose, ResolvedV2CampaignConfig, load_v2_campaign_config
from .campaign_status import (
    CAMPAIGN_STATUS_SCHEMA_VERSION,
    CampaignStatusV1,
    inspect_v2_campaign_status,
)
from .fingerprint import canonical_sha256
from .model_card import ModelCardBuildError, build_model_card
from .schema import Fingerprint, StrictModel

SUPERVISOR_STATE_SCHEMA_VERSION = "ori-v2-campaign-supervisor-state-v2"
_CHILD_TERMINATE_GRACE_SECONDS = 10.0
_CHILD_KILL_GRACE_SECONDS = 2.0


class CampaignSupervisorError(RuntimeError):
    """Raised when the supervisor must stop without changing campaign state."""


class SupervisorAlreadyRunningError(CampaignSupervisorError):
    """Raised when another supervisor owns the external supervisor-state lock."""


class SupervisorInterruptedError(CampaignSupervisorError):
    """A stop signal reached this supervisor, not an adopted campaign process."""


class SupervisorCampaignBindingV1(StrictModel):
    """The campaign-purpose identity pinned by an external supervisor.

    The V2 config bytes already bind the selected purpose.  A diagnostic
    canary additionally depends on a separate, mutable selection receipt, so
    its fingerprint and fixed suite belong in the durable state as well.
    """

    purpose: CampaignPurpose
    canary_suite: Literal["native-five-kind-v1"] | None = None
    canary_selection_fingerprint: Fingerprint | None = None

    @model_validator(mode="after")
    def canary_identity_matches_purpose(self) -> SupervisorCampaignBindingV1:
        if self.purpose == "official":
            if self.canary_suite is not None or self.canary_selection_fingerprint is not None:
                raise ValueError("official campaigns cannot carry a diagnostic-canary binding")
        elif self.canary_suite is None or self.canary_selection_fingerprint is None:
            raise ValueError("diagnostic canaries require a suite and selection fingerprint")
        return self


class SupervisorStateV1(StrictModel):
    schema_version: Literal["ori-v2-campaign-supervisor-state-v2"] = SUPERVISOR_STATE_SCHEMA_VERSION
    source_config_fingerprint: str
    config_sha256: str
    campaign: SupervisorCampaignBindingV1
    token_ceiling: int = Field(strict=True, gt=0)
    max_restarts: int = Field(strict=True, ge=0)
    restarts_used: int = Field(default=0, strict=True, ge=0)
    launches: int = Field(default=0, strict=True, ge=0)
    readiness_launches: int = Field(default=0, strict=True, ge=0)
    execution_launches: int = Field(default=0, strict=True, ge=0)
    total_tokens_observed: int = Field(default=0, strict=True, ge=0)
    budget_stop_requested: bool = False
    model_card_generated: bool = False
    created_at_utc: str
    updated_at_utc: str
    state_fingerprint: str


class SupervisorChildOwnershipV1(StrictModel):
    """A pre-spawn quarantine, not permission to adopt or signal a recorded PID."""

    schema_version: Literal["ori-v2-supervisor-child-ownership-v1"] = (
        "ori-v2-supervisor-child-ownership-v1"
    )
    source_config_fingerprint: str
    config_sha256: str
    launches: int
    cleanup_confirmed: Literal[False] = False


class SupervisorDecision(StrictModel):
    action: Literal[
        "run_readiness",
        "run_execution",
        "monitor",
        "await_execution_approval",
        "complete",
        "stop",
    ]
    reason: str
    recovery: bool = False
    campaign: SupervisorCampaignBindingV1 | None = None


class ChildProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def send_signal(self, signum: int) -> None: ...

    def kill(self) -> None: ...


class _OwnedSubprocess:
    """Retain the original Popen child; use kernel-pinned signaling on Linux.

    No status-reported PID or process-name lookup can acquire this authority.
    Other POSIX platforms use the retained direct-child Popen handle. Children
    have their own session so terminal signals reach them only via forwarding.
    """

    def __init__(self, command: Sequence[str]):
        self._process = subprocess.Popen(tuple(command), start_new_session=True)  # noqa: S603
        self.pid = self._process.pid
        self._pidfd: int | None = None
        self._lock = threading.RLock()
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                self._pidfd = os.pidfd_open(self.pid)
            except OSError:
                # Still the Popen instance just created here, never an adopted
                # identifier. A failed pin must not orphan a startup child.
                self._process.kill()
                self._process.wait(timeout=_CHILD_KILL_GRACE_SECONDS)
                raise

    def poll(self) -> int | None:
        with self._lock:
            result = self._process.poll()
            if result is not None and self._pidfd is not None:
                os.close(self._pidfd)
                self._pidfd = None
            return result

    def send_signal(self, signum: int) -> None:
        with self._lock:
            if self.poll() is not None:
                return
            try:
                if self._pidfd is not None:
                    signal.pidfd_send_signal(self._pidfd, signum)
                else:
                    self._process.send_signal(signum)
            except ProcessLookupError:
                pass

    def terminate(self) -> None:
        self.send_signal(signal.SIGTERM)

    def kill(self) -> None:
        self.send_signal(signal.SIGKILL)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _state_payload(state: SupervisorStateV1) -> dict[str, object]:
    return state.model_dump(mode="json", exclude={"state_fingerprint"})


def _with_fingerprint(payload: dict[str, object]) -> SupervisorStateV1:
    candidate = dict(payload)
    candidate["state_fingerprint"] = canonical_sha256(candidate)
    return SupervisorStateV1.model_validate(candidate)


def _validate_state_fingerprint(state: SupervisorStateV1) -> None:
    expected = canonical_sha256(_state_payload(state))
    if state.state_fingerprint != expected:
        raise CampaignSupervisorError("supervisor state fingerprint is invalid")


def _atomic_write_state(
    path: Path, state: SupervisorStateV1 | SupervisorChildOwnershipV1
) -> None:
    """Durably replace one private supervisor state file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (state.model_dump_json(indent=2) + "\n").encode()
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise CampaignSupervisorError(f"cannot persist supervisor state: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if temporary is not None:
                temporary.unlink()
        except FileNotFoundError:
            pass


def _read_file_no_follow(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise CampaignSupervisorError("invalid supervisor state: not a regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            payload = handle.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise CampaignSupervisorError("invalid supervisor state: file is too large")
            return payload
    except OSError as exc:
        raise CampaignSupervisorError(f"invalid supervisor state: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


class SupervisorStateStore(AbstractContextManager["SupervisorStateStore"]):
    """Exclusive, durable state storage outside the ORI campaign root."""

    def __init__(self, path: Path, campaign_root: Path):
        self.path = path.parent.resolve() / path.name
        self.campaign_root = campaign_root.resolve()
        if self.path == self.campaign_root or self.path.is_relative_to(self.campaign_root):
            raise CampaignSupervisorError(
                "supervisor state must be outside the campaign output directory"
            )
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")
        self._lock_descriptor: int | None = None

    def __enter__(self) -> SupervisorStateStore:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            self._lock_descriptor = os.open(self.lock_path, flags, 0o600)
        except OSError as exc:
            raise CampaignSupervisorError(f"cannot open supervisor-state lock: {exc}") from exc
        lock_stat = os.fstat(self._lock_descriptor)
        if not stat.S_ISREG(lock_stat.st_mode):
            os.close(self._lock_descriptor)
            self._lock_descriptor = None
            raise CampaignSupervisorError("cannot open supervisor-state lock: not a regular file")
        try:
            fcntl.flock(self._lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._lock_descriptor)
            self._lock_descriptor = None
            raise SupervisorAlreadyRunningError(
                "another campaign supervisor holds the supervisor-state lock"
            ) from exc
        return self

    def __exit__(self, *args: object) -> None:
        if self._lock_descriptor is not None:
            fcntl.flock(self._lock_descriptor, fcntl.LOCK_UN)
            os.close(self._lock_descriptor)
            self._lock_descriptor = None

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> SupervisorStateV1:
        try:
            state = SupervisorStateV1.model_validate_json(_read_file_no_follow(self.path))
        except (OSError, ValueError) as exc:
            raise CampaignSupervisorError(f"invalid supervisor state: {exc}") from exc
        _validate_state_fingerprint(state)
        return state

    def save(self, state: SupervisorStateV1) -> None:
        _validate_state_fingerprint(state)
        _atomic_write_state(self.path, state)


def _status_total_tokens(status: CampaignStatusV1) -> int:
    value = getattr(status.progress, "total_tokens", None)
    if type(value) is not int or value < 0:
        raise CampaignSupervisorError(
            "campaign status does not provide valid cumulative token accounting"
        )
    return value


def _campaign_binding(resolved: ResolvedV2CampaignConfig) -> SupervisorCampaignBindingV1:
    """Derive the no-secret identity that a supervisor must preserve on resume."""

    if resolved.config.purpose == "official":
        return SupervisorCampaignBindingV1(purpose="official")

    if resolved.canary_selection is None:
        raise CampaignSupervisorError("diagnostic canary selection is unavailable")
    try:
        from .diagnostic_selection import DiagnosticSelectionReceiptV1

        selection = DiagnosticSelectionReceiptV1.model_validate_json(
            _read_file_no_follow(resolved.canary_selection)
        )
    except (OSError, ValueError) as exc:
        raise CampaignSupervisorError("diagnostic canary selection is invalid") from exc
    return SupervisorCampaignBindingV1(
        purpose="diagnostic_canary",
        canary_suite=selection.suite,
        canary_selection_fingerprint=selection.selection_fingerprint,
    )


def decide_supervisor_action(
    status: CampaignStatusV1,
    *,
    execute_approved: bool,
    state_exists: bool,
) -> SupervisorDecision:
    """Apply the documented V2 status/action table exactly once."""

    if status.schema_version != CAMPAIGN_STATUS_SCHEMA_VERSION:
        return SupervisorDecision(action="stop", reason="unsupported status schema")
    observed = status.observed_state
    action = status.next_action
    if observed == "not_started":
        if action != "run_readiness":
            return SupervisorDecision(action="stop", reason="incoherent initial status")
        return SupervisorDecision(action="run_readiness", reason="readiness required")
    if observed == "readiness_complete":
        if not status.resume_allowed or action != "execute_campaign":
            return SupervisorDecision(action="stop", reason="incoherent readiness status")
        if not execute_approved:
            return SupervisorDecision(
                action="await_execution_approval",
                reason="paid execution requires explicit approval",
            )
        return SupervisorDecision(action="run_execution", reason="execution approved")
    if observed == "running":
        if action != "monitor" or status.resume_allowed:
            return SupervisorDecision(action="stop", reason="incoherent active status")
        return SupervisorDecision(action="monitor", reason="campaign lock is held")
    if observed in {"stale_running", "interrupted"}:
        if not status.resume_allowed:
            return SupervisorDecision(action="stop", reason="recovery is not allowed")
        if status.mode == "readiness" and action == "run_readiness":
            return SupervisorDecision(
                action="run_readiness", reason="resume interrupted readiness", recovery=True
            )
        if status.mode == "execution" and action == "resume_campaign":
            if not state_exists:
                return SupervisorDecision(
                    action="stop",
                    reason="execution recovery requires existing supervisor state",
                )
            if not execute_approved:
                return SupervisorDecision(
                    action="await_execution_approval",
                    reason="execution recovery requires explicit approval",
                )
            return SupervisorDecision(
                action="run_execution", reason="resume interrupted execution", recovery=True
            )
        return SupervisorDecision(action="stop", reason="incoherent recovery status")
    if observed == "failed" or action == "investigate_failure":
        return SupervisorDecision(action="stop", reason="campaign requires investigation")
    if observed == "completed":
        if action == "campaign_complete" and all(
            track.completion_present and track.campaign_valid is True for track in status.tracks
        ):
            return SupervisorDecision(action="complete", reason="valid completion")
        return SupervisorDecision(action="stop", reason="campaign completion is invalid")
    return SupervisorDecision(action="stop", reason="unsupported campaign state")


def _new_state(
    *,
    status: CampaignStatusV1,
    config_sha256: str,
    campaign: SupervisorCampaignBindingV1,
    token_ceiling: int,
    max_restarts: int,
    now: str,
) -> SupervisorStateV1:
    return _with_fingerprint(
        {
            "schema_version": SUPERVISOR_STATE_SCHEMA_VERSION,
            "source_config_fingerprint": status.source_config_fingerprint,
            "config_sha256": config_sha256,
            "campaign": campaign,
            "token_ceiling": token_ceiling,
            "max_restarts": max_restarts,
            "restarts_used": 0,
            "launches": 0,
            "readiness_launches": 0,
            "execution_launches": 0,
            "total_tokens_observed": _status_total_tokens(status),
            "budget_stop_requested": False,
            "model_card_generated": False,
            "created_at_utc": now,
            "updated_at_utc": now,
        }
    )


def _updated_state(
    state: SupervisorStateV1,
    *,
    now: str,
    restarts_used: int | None = None,
    launches: int | None = None,
    readiness_launches: int | None = None,
    execution_launches: int | None = None,
    total_tokens_observed: int | None = None,
    budget_stop_requested: bool | None = None,
    model_card_generated: bool | None = None,
) -> SupervisorStateV1:
    payload = _state_payload(state)
    payload.update(
        {
            "restarts_used": (state.restarts_used if restarts_used is None else restarts_used),
            "launches": state.launches if launches is None else launches,
            "readiness_launches": (
                state.readiness_launches if readiness_launches is None else readiness_launches
            ),
            "execution_launches": (
                state.execution_launches if execution_launches is None else execution_launches
            ),
            "total_tokens_observed": (
                state.total_tokens_observed
                if total_tokens_observed is None
                else total_tokens_observed
            ),
            "budget_stop_requested": (
                state.budget_stop_requested
                if budget_stop_requested is None
                else budget_stop_requested
            ),
            "model_card_generated": (
                state.model_card_generated if model_card_generated is None else model_card_generated
            ),
            "updated_at_utc": now,
        }
    )
    return _with_fingerprint(payload)


class V2CampaignSupervisor:
    """Run and monitor one exact campaign under external restart/spend bounds."""

    def __init__(
        self,
        *,
        config_path: Path,
        state_path: Path,
        token_ceiling: int,
        max_restarts: int,
        execute_approved: bool,
        ori_executable: str,
        poll_interval_seconds: float = 15.0,
        model_card_output: Path | None = None,
        model: str | None = None,
        display_name: str | None = None,
        inspector: Callable[[Path], CampaignStatusV1] = inspect_v2_campaign_status,
        launcher: Callable[[Sequence[str]], ChildProcess] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], str] = _utc_now,
    ):
        if token_ceiling <= 0:
            raise CampaignSupervisorError("token ceiling must be positive")
        if max_restarts < 0:
            raise CampaignSupervisorError("maximum restarts must be non-negative")
        if poll_interval_seconds <= 0:
            raise CampaignSupervisorError("poll interval must be positive")
        self.config_path = config_path.resolve()
        self.token_ceiling = token_ceiling
        self.max_restarts = max_restarts
        self.execute_approved = execute_approved
        self.poll_interval_seconds = poll_interval_seconds
        executable_path = Path(ori_executable)
        if not executable_path.is_absolute():
            raise CampaignSupervisorError("ORI executable must be an absolute path")
        if launcher is None:
            try:
                executable_path = executable_path.resolve(strict=True)
            except OSError as exc:
                raise CampaignSupervisorError(f"ORI executable is unavailable: {exc}") from exc
            if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
                raise CampaignSupervisorError("ORI executable is not an executable file")
        self.ori_executable = str(executable_path)
        self.model_card_output = model_card_output
        self.model = model
        self.display_name = display_name
        self.inspector = inspector
        self.launcher = launcher or self._launch
        self.sleeper = sleeper
        self.now = now
        try:
            self.config_sha256 = hashlib.sha256(self.config_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise CampaignSupervisorError(f"cannot read campaign config: {exc}") from exc
        try:
            self.resolved_config = load_v2_campaign_config(self.config_path)
            self.campaign = _campaign_binding(self.resolved_config)
        except (OSError, ValueError) as exc:
            raise CampaignSupervisorError("campaign configuration is invalid") from exc
        self.campaign_root = self.resolved_config.output_dir
        self.store = SupervisorStateStore(state_path, self.campaign_root)
        self._ownership_path = self.store.path.with_name(
            f"{self.store.path.name}.owned-child.private.json"
        )
        self._owned_child: ChildProcess | None = None
        self._launching = False
        self._stop_signal: int | None = None
        self._cleanup_lock = threading.Lock()
        self._cleanup_error: BaseException | None = None

    @staticmethod
    def _launch(command: Sequence[str]) -> ChildProcess:
        return _OwnedSubprocess(command)

    def _handle_stop_signal(self, signum: int, _frame: object) -> None:
        if self._stop_signal is None:
            self._stop_signal = signum
        # Never throw asynchronously: even the first instruction of an except
        # or finally block can be interrupted before a shielding flag is set.
        # The cancellation watcher forwards to the exact captured child; main
        # execution raises only at controlled checkpoints. No lock or Event
        # operation belongs in this handler (either can be interrupted itself).

    def _raise_if_cancelled(self) -> None:
        if self._stop_signal is not None:
            raise SupervisorInterruptedError("supervisor interrupted by an operator signal")

    def _cleanup_owned_child(self) -> None:
        with self._cleanup_lock:
            if self._cleanup_error is not None:
                raise self._cleanup_error
            try:
                self._cleanup_owned_child_locked()
            except BaseException as exc:
                self._cleanup_error = exc
                raise

    def _clear_owned_marker(self) -> None:
        try:
            self._ownership_path.unlink()
        except FileNotFoundError:
            return
        descriptor = os.open(self._ownership_path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _cleanup_owned_child_locked(self) -> None:
        child = self._owned_child
        if child is None:
            return
        if child.poll() is not None:
            self._clear_owned_marker()
            return
        child.send_signal(self._stop_signal or signal.SIGTERM)
        deadline = time.monotonic() + _CHILD_TERMINATE_GRACE_SECONDS
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if child.poll() is None:
            child.kill()
            deadline = time.monotonic() + _CHILD_KILL_GRACE_SECONDS
            while child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
        if child.poll() is None:
            raise CampaignSupervisorError("owned ORI child cleanup remains unconfirmed")
        self._clear_owned_marker()

    def _wait_for_next_poll(self) -> None:
        remaining = self.poll_interval_seconds
        while remaining > 0:
            self._raise_if_cancelled()
            interval = min(0.1, remaining)
            self.sleeper(interval)
            remaining -= interval
        self._raise_if_cancelled()

    @contextmanager
    def _cancellation_boundary(self) -> Iterator[None]:
        previous: dict[int, object] = {}
        finished = threading.Event()

        def cancellation_watch() -> None:
            while not finished.wait(0.05):
                if (
                    self._stop_signal is not None
                    and not self._launching
                    and self._owned_child is not None
                ):
                    try:
                        self._cleanup_owned_child()
                    except BaseException:
                        # The same stored error is raised by the main owner;
                        # an uncertain cleanup never clears the durable marker.
                        pass
                    return

        watcher = threading.Thread(
            target=cancellation_watch, name="ori-supervisor-cancellation", daemon=True
        )
        try:
            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    previous[signum] = signal.signal(signum, self._handle_stop_signal)
            self._raise_if_cancelled()
            watcher.start()
            yield
        except BaseException:
            self._cleanup_owned_child()
            raise
        finally:
            finished.set()
            if watcher.ident is not None:
                watcher.join(
                    timeout=_CHILD_TERMINATE_GRACE_SECONDS + _CHILD_KILL_GRACE_SECONDS + 0.5
                )
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def _command(self, execute: bool) -> tuple[str, ...]:
        command_name = "run-canary-v2" if self.campaign.purpose == "diagnostic_canary" else "run-v2"
        command = [self.ori_executable, command_name, "--config", str(self.config_path)]
        if execute:
            command.append("--execute")
        return tuple(command)

    def _inspect(self) -> CampaignStatusV1:
        self._raise_if_cancelled()
        try:
            status = self.inspector(self.config_path)
        except ValueError as exc:
            raise CampaignSupervisorError(f"campaign status failed closed: {exc}") from exc
        self._raise_if_cancelled()
        return status

    def _load_or_create_state(self, status: CampaignStatusV1) -> SupervisorStateV1:
        if self.store.exists:
            state = self.store.load()
            if state.source_config_fingerprint != status.source_config_fingerprint:
                raise CampaignSupervisorError("supervisor state belongs to another config")
            if state.config_sha256 != self.config_sha256:
                raise CampaignSupervisorError("campaign config bytes changed")
            if state.campaign != self.campaign:
                raise CampaignSupervisorError(
                    "campaign purpose or diagnostic-canary binding changed"
                )
            if state.token_ceiling != self.token_ceiling or state.max_restarts != self.max_restarts:
                raise CampaignSupervisorError("supervisor policy changed across invocations")
            return state
        if status.mode == "execution" and status.observed_state in {
            "stale_running",
            "interrupted",
        }:
            raise CampaignSupervisorError(
                "refusing to adopt already-interrupted execution without supervisor state"
            )
        state = _new_state(
            status=status,
            config_sha256=self.config_sha256,
            campaign=self.campaign,
            token_ceiling=self.token_ceiling,
            max_restarts=self.max_restarts,
            now=self.now(),
        )
        self.store.save(state)
        return state

    def _with_campaign(self, decision: SupervisorDecision) -> SupervisorDecision:
        """Expose the immutable purpose binding with every supervisor result."""

        self._raise_if_cancelled()
        return decision.model_copy(update={"campaign": self.campaign})

    def _observe_tokens(
        self, state: SupervisorStateV1, status: CampaignStatusV1
    ) -> SupervisorStateV1:
        total_tokens = _status_total_tokens(status)
        if total_tokens < state.total_tokens_observed:
            raise CampaignSupervisorError("cumulative token accounting decreased")
        if total_tokens != state.total_tokens_observed:
            state = _updated_state(state, now=self.now(), total_tokens_observed=total_tokens)
            self.store.save(state)
        return state

    def _request_budget_stop(self, state: SupervisorStateV1) -> SupervisorStateV1:
        if self._owned_child is None or self._owned_child.poll() is not None:
            raise CampaignSupervisorError(
                "token ceiling reached while no owned child can be terminated"
            )
        if not state.budget_stop_requested:
            state = _updated_state(state, now=self.now(), budget_stop_requested=True)
            self.store.save(state)
            self._owned_child.terminate()
        return state

    def _launch_for_decision(
        self, state: SupervisorStateV1, decision: SupervisorDecision
    ) -> SupervisorStateV1:
        if self._owned_child is not None and self._owned_child.poll() is None:
            raise CampaignSupervisorError("refusing to launch a second owned child")
        execute = decision.action == "run_execution"
        prior_phase_launches = state.execution_launches if execute else state.readiness_launches
        restarts = state.restarts_used
        if decision.recovery or prior_phase_launches > 0:
            if restarts >= state.max_restarts:
                raise CampaignSupervisorError("external restart limit reached")
            restarts += 1
        state = _updated_state(
            state,
            now=self.now(),
            restarts_used=restarts,
            launches=state.launches + 1,
            readiness_launches=(state.readiness_launches + (0 if execute else 1)),
            execution_launches=(state.execution_launches + (1 if execute else 0)),
        )
        # Consume the launch/restart allowance before spawning.  A failed spawn
        # must not reset the external recovery budget.
        self.store.save(state)
        self._raise_if_cancelled()
        with self._cleanup_lock:
            if self._cleanup_error is not None:
                raise self._cleanup_error
            self._cleanup_owned_child_locked()
            # Retire the old launch before creating a new quarantine. An
            # exception during the next Popen must never let an old, exited
            # handle clear the new launch's unknown-cleanup marker.
            self._owned_child = None
        if self._ownership_path.exists() or self._ownership_path.is_symlink():
            raise CampaignSupervisorError("prior owned ORI child cleanup remains unconfirmed")
        _atomic_write_state(
            self._ownership_path,
            SupervisorChildOwnershipV1(
                source_config_fingerprint=state.source_config_fingerprint,
                config_sha256=self.config_sha256,
                launches=state.launches,
            ),
        )
        self._raise_if_cancelled()
        self._launching = True
        try:
            self._owned_child = self.launcher(self._command(execute))
        except OSError as exc:
            raise CampaignSupervisorError(f"cannot launch ORI child: {exc}") from exc
        finally:
            self._launching = False
        self._raise_if_cancelled()
        return state

    def _build_model_card(self, state: SupervisorStateV1) -> SupervisorStateV1:
        if self.model_card_output is None or state.model_card_generated:
            return state
        try:
            build_model_card(
                self.campaign_root,
                self.model_card_output,
                model=self.model,
                display_name=self.display_name,
            )
        except (OSError, ModelCardBuildError) as exc:
            raise CampaignSupervisorError(f"model-card validation failed: {exc}") from exc
        state = _updated_state(state, now=self.now(), model_card_generated=True)
        self.store.save(state)
        return state

    def run(self, *, max_polls: int | None = None) -> SupervisorDecision:
        """Supervise until a terminal/approval state (or a bounded test poll)."""

        return self._run_loop(max_polls=max_polls)

    def _run_loop(self, *, max_polls: int | None) -> SupervisorDecision:

        # Keep the external ownership lock until exceptional cleanup finishes.
        with self.store, self._cancellation_boundary():
            polls = 0
            status = self._inspect()
            state = self._load_or_create_state(status)
            if self._ownership_path.exists() or self._ownership_path.is_symlink():
                if self._owned_child is None:
                    raise CampaignSupervisorError(
                        "prior owned ORI child cleanup remains unconfirmed; monitor only"
                    )
                if self._owned_child.poll() is not None:
                    self._cleanup_owned_child()
            while True:
                self._raise_if_cancelled()
                if state.budget_stop_requested:
                    raise CampaignSupervisorError("prior token-budget stop is terminal")
                if state.source_config_fingerprint != status.source_config_fingerprint:
                    raise CampaignSupervisorError("status source fingerprint changed")
                state = self._observe_tokens(state, status)
                at_budget = state.total_tokens_observed >= state.token_ceiling
                decision = decide_supervisor_action(
                    status,
                    execute_approved=self.execute_approved,
                    state_exists=True,
                )
                child_running = self._owned_child is not None and self._owned_child.poll() is None
                if self._owned_child is not None and not child_running:
                    self._cleanup_owned_child()
                if at_budget and decision.action != "complete":
                    if child_running:
                        state = self._request_budget_stop(state)
                    elif decision.action == "monitor":
                        raise CampaignSupervisorError(
                            "token ceiling reached while no owned child can be terminated"
                        )
                    raise CampaignSupervisorError("cumulative token ceiling reached")
                if child_running:
                    polls += 1
                    if max_polls is not None and polls >= max_polls:
                        return self._with_campaign(
                            SupervisorDecision(
                                action="monitor",
                                reason="bounded poll limit reached",
                            )
                        )
                    self._wait_for_next_poll()
                    status = self._inspect()
                    continue
                if decision.action in {"stop", "await_execution_approval"}:
                    return self._with_campaign(decision)
                if decision.action == "complete":
                    self._cleanup_owned_child()
                    self._build_model_card(state)
                    return self._with_campaign(decision)
                if decision.action in {"run_readiness", "run_execution"}:
                    state = self._launch_for_decision(state, decision)
                polls += 1
                if max_polls is not None and polls >= max_polls:
                    return self._with_campaign(
                        SupervisorDecision(action="monitor", reason="bounded poll limit reached")
                    )
                self._wait_for_next_poll()
                status = self._inspect()


def supervise_v2_campaign(
    *,
    config_path: Path,
    state_path: Path,
    token_ceiling: int,
    max_restarts: int,
    execute_approved: bool,
    ori_executable: str,
    poll_interval_seconds: float = 15.0,
    model_card_output: Path | None = None,
    model: str | None = None,
    display_name: str | None = None,
) -> SupervisorDecision:
    supervisor = V2CampaignSupervisor(
        config_path=config_path,
        state_path=state_path,
        token_ceiling=token_ceiling,
        max_restarts=max_restarts,
        execute_approved=execute_approved,
        poll_interval_seconds=poll_interval_seconds,
        ori_executable=ori_executable,
        model_card_output=model_card_output,
        model=model,
        display_name=display_name,
    )
    return supervisor.run()
