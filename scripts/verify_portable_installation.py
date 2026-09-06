#!/usr/bin/env python3
"""Private, model-free acceptance of an already installed ORI wheel.

The Python audit hook observes ordinary Python operations; it is not an OS
sandbox. Dependency integrity and source-to-wheel binding are outer gates.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import re
import subprocess
import time
import zipfile
from pathlib import Path, PurePosixPath

GUARD_PREFIX = "ORI_PORTABLE_AUDIT_V1:"
BOOTSTRAP = r"""
import sys, json, os, runpy, socket
events = []
blocked_ipv6_capability_probes = []
def audit(event, args):
    if (event.startswith("socket.") and event not in {"socket.__new__"}) or event in {
        "subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork", "os.forkpty"
    }:
        events.append(event)
        if event == "socket.bind":
            caller = sys._getframe(1)
            address = args[1]
            if (
                isinstance(args[0], socket.socket)
                and args[0].family == socket.AF_INET6
                and type(address) is tuple and len(address) == 2
                and type(address[0]) is str and address[0] == "::1"
                and type(address[1]) is int and address[1] == 0
                and caller.f_globals.get("__name__") == "urllib3.util.connection"
                and caller.f_code.co_name == "_has_ipv6"
            ):
                blocked_ipv6_capability_probes.append(len(events) - 1)
        raise RuntimeError("portable probe denied operation")
sys.addaudithook(audit)
try:
    mode = sys.argv[1]
    if mode == "self-test":
        import socket, subprocess
        for operation in (
            lambda: socket.socket().connect(("127.0.0.1", 9)),
            lambda: subprocess.run([sys.executable, "-c", "pass"]),
        ):
            try:
                operation()
            except RuntimeError:
                pass
            else:
                raise AssertionError("audit operation was not denied")
    elif mode == "inspect":
        import hashlib, importlib.metadata, sysconfig
        from pathlib import Path
        import ori, ori.cli
        dist = importlib.metadata.distribution("offensive-reasoning-index")
        prefix = Path(sys.prefix).resolve()
        cfg = (prefix / "pyvenv.cfg").read_text()
        settings = dict(line.split("=", 1) for line in cfg.splitlines() if "=" in line)
        settings = {k.strip().lower(): v.strip().lower() for k, v in settings.items()}
        package = Path(ori.__file__).resolve().parent
        members = {}
        for path in package.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                key = "ori/" + path.relative_to(package).as_posix()
                members[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        points = [p.value for p in dist.entry_points
                  if p.group == "console_scripts" and p.name == "ori"]
        print(json.dumps({
            "prefix": str(prefix), "base_prefix": str(Path(sys.base_prefix).resolve()),
            "system_site_packages": settings.get("include-system-site-packages") != "false",
            "package": str(package), "cli": str(Path(ori.cli.__file__).resolve()),
            "console_script": str(prefix / "bin" / "ori"),
            "site_packages": sorted({str(Path(sysconfig.get_path(k)).resolve())
                                     for k in ("purelib", "platlib")}),
            "members": members, "metadata": dist.read_text("METADATA"),
            "entry_points": dist.read_text("entry_points.txt"),
            "entry_point": points[0] if len(points) == 1 else None,
            "version": dist.version, "python": sys.version,
            "platform": " ".join(os.uname()[:3]), "architecture": os.uname().machine,
        }, sort_keys=True))
    elif mode == "console":
        script = sys.argv[2]
        sys.argv = [script] + sys.argv[3:]
        runpy.run_path(script, run_name="__main__")
    else:
        raise ValueError("unknown bootstrap mode")
finally:
    print("ORI_PORTABLE_AUDIT_V1:" + json.dumps({
        "events": events,
        "blocked_ipv6_capability_probes": blocked_ipv6_capability_probes,
    }), file=sys.stderr)
"""


class PortableFailure(Exception):
    def __init__(self, kind: str, message: str, probe: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.probe = probe


def _require(condition: bool, kind: str, message: str) -> None:
    if not condition:
        raise PortableFailure(kind, message)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_arguments(python: Path, wheel: Path, output_dir: Path) -> tuple[Path, Path, Path]:
    _require(os.name != "nt", "arguments", "Windows is not supported by this slice")
    _require(
        all(p.is_absolute() for p in (python, wheel, output_dir)),
        "arguments",
        "all paths must be absolute",
    )
    _require(
        python.is_file() and os.access(python, os.X_OK), "arguments", "Python must be executable"
    )
    _require(wheel.is_file(), "arguments", "wheel must be a regular file")
    _require(not os.path.lexists(output_dir), "arguments", "output entry already exists")
    parent = output_dir.parent.resolve(strict=True)
    _require(parent.is_dir(), "arguments", "output parent must be a directory")
    resolved_output = parent / output_dir.name
    _require(
        not any(os.path.lexists(ancestor / ".git") for ancestor in (parent, *parent.parents)),
        "arguments",
        "output must be outside Git worktrees",
    )
    _require(
        not resolved_output.is_relative_to(python.parent.parent.resolve()),
        "arguments",
        "output must be outside the target venv",
    )
    return python, wheel.resolve(strict=True), resolved_output


def child_environment(python: Path, root: Path) -> dict[str, str]:
    return {
        "PATH": str(python.parent),
        "HOME": str(root / "empty home"),
        "XDG_CONFIG_HOME": str(root / "empty home" / "config"),
        "TMPDIR": str(root / "temporary files"),
        "TMP": str(root / "temporary files"),
        "TEMP": str(root / "temporary files"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "PYTHON_DOTENV_DISABLED": "1",
    }


def wheel_inventory(wheel: Path) -> dict:
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            _require(len(names) == len(set(names)), "wheel", "duplicate wheel member")
            for name in names:
                parts = PurePosixPath(name).parts
                _require(
                    bool(parts)
                    and not name.startswith("/")
                    and ".." not in parts
                    and "\\" not in name
                    and ":" not in parts[0],
                    "wheel",
                    "unsafe wheel member",
                )
            metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
            _require(len(metadata) == 1, "wheel", "wheel must contain one METADATA")
            text = archive.read(metadata[0]).decode("utf-8")
            parsed = email.parser.Parser().parsestr(text)
            _require(
                parsed.get("Name") == "offensive-reasoning-index" and bool(parsed.get("Version")),
                "wheel",
                "wrong wheel distribution",
            )
            entry_name = metadata[0].removesuffix("METADATA") + "entry_points.txt"
            members = {
                n: _digest(archive.read(n))
                for n in names
                if n.startswith("ori/") and not n.endswith("/")
            }
            _require(bool(members), "wheel", "wheel contains no ORI package")
            return {
                "sha256": _digest(wheel.read_bytes()),
                "members": members,
                "metadata": text,
                "entry_points": archive.read(entry_name).decode("utf-8"),
                "version": parsed["Version"],
            }
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise PortableFailure("wheel", "invalid wheel archive") from exc


def verify_installation(wheel_info: dict, observed: dict) -> None:
    try:
        prefix = Path(observed["prefix"]).resolve()
        package = Path(observed["package"]).resolve()
        cli = Path(observed["cli"]).resolve()
        script = Path(observed["console_script"]).resolve()
        _require(
            prefix != Path(observed["base_prefix"]).resolve()
            and observed["system_site_packages"] is False,
            "installation",
            "isolated venv required",
        )
        sites = [Path(p).resolve() for p in observed["site_packages"]]
        _require(
            any(p.is_relative_to(prefix) and package.is_relative_to(p) for p in sites),
            "installation",
            "package is outside venv site-packages",
        )
        _require(
            cli.is_relative_to(package) and cli.is_file(),
            "installation",
            "loaded CLI is outside package",
        )
        _require(
            script.is_relative_to(prefix) and script.is_file() and os.access(script, os.X_OK),
            "installation",
            "console script is not executable inside venv",
        )
        _require(
            observed["entry_point"] == "ori.cli:main",
            "installation",
            "unexpected console entry point",
        )
        for key in ("members", "metadata", "entry_points", "version"):
            _require(
                observed[key] == wheel_info[key],
                "installation",
                f"installed {key} does not match wheel",
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise PortableFailure("installation", "malformed installation observation") from exc


def parse_guard(stderr: str) -> dict:
    lines = [
        line[len(GUARD_PREFIX) :] for line in stderr.splitlines() if line.startswith(GUARD_PREFIX)
    ]
    _require(len(lines) == 1, "guard", "expected exactly one audit summary")
    try:
        result = json.loads(lines[0])
        _require(
            isinstance(result, dict)
            and set(result) == {"events", "blocked_ipv6_capability_probes"}
            and isinstance(result["events"], list)
            and all(isinstance(item, str) for item in result["events"]),
            "guard",
            "invalid audit summary",
        )
        indexes = result["blocked_ipv6_capability_probes"]
        _require(
            isinstance(indexes, list)
            and all(type(index) is int for index in indexes)
            and indexes == sorted(set(indexes))
            and all(
                0 <= index < len(result["events"]) and result["events"][index] == "socket.bind"
                for index in indexes
            ),
            "guard",
            "invalid blocked IPv6 capability classification",
        )
        return result
    except ValueError as exc:
        raise PortableFailure("guard", "invalid audit JSON") from exc


def run_probe(
    probe_id: str,
    command: list[str],
    environment: dict[str, str],
    cwd: Path,
    expected_exit: int,
    timeout: float,
    logs_dir: Path,
) -> dict:
    result = {"id": probe_id, "command": command, "expected_exit": expected_exit}
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command, cwd=cwd, env=environment, capture_output=True, timeout=timeout, check=False
        )
        stdout, stderr = completed.stdout, completed.stderr
        result["actual_exit"] = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
        result["actual_exit"] = None
        timed_out = True
    except OSError as exc:
        raise PortableFailure("launch", "could not launch probe", result) from exc
    result["duration_seconds"] = time.monotonic() - started
    for label, data in (("stdout", stdout), ("stderr", stderr)):
        path = logs_dir / f"{probe_id}.{label}.private.log"
        path.write_bytes(data)
        result[f"{label}_path"] = str(path)
        result[f"{label}_sha256"] = _digest(data)
    try:
        _require(not timed_out, "timeout", "probe exceeded deadline")
        result["guard"] = parse_guard(stderr.decode("utf-8"))
        events = result["guard"]["events"]
        blocked_probes = result["guard"]["blocked_ipv6_capability_probes"]
        if probe_id == "guard-self-test":
            _require(
                events == ["socket.connect", "subprocess.Popen"] and not blocked_probes,
                "guard",
                "audit self-test failed",
            )
        elif probe_id in {"status-relative", "status-poison"}:
            _require(
                (not events and not blocked_probes)
                or (events == ["socket.bind"] and blocked_probes == [0]),
                "guard",
                "forbidden operation attempted outside blocked IPv6 capability allowance",
            )
        else:
            _require(not events and not blocked_probes, "guard", "forbidden operation attempted")
        _require(result["actual_exit"] == expected_exit, "exit", "unexpected probe exit")
    except (PortableFailure, UnicodeError) as exc:
        failure = (
            exc
            if isinstance(exc, PortableFailure)
            else PortableFailure("output", "invalid UTF-8 log")
        )
        failure.probe = result
        raise failure
    return result


def validate_status(payload: dict) -> None:
    _require(isinstance(payload, dict), "status", "status must be an object")
    for key, value in (
        ("schema_version", "ori-v2-campaign-status-v2"),
        ("protocol_version", "ori-eval-protocol-v2"),
        ("lifecycle_state", "not_started"),
        ("observed_state", "not_started"),
        ("next_action", "run_readiness"),
        ("resume_allowed", False),
    ):
        _require(
            payload.get(key) == value and type(payload.get(key)) is type(value),
            "status",
            f"wrong {key}",
        )
    progress = payload.get("progress", {})
    _require(
        isinstance(progress, dict)
        and type(progress.get("expected_runs")) is int
        and progress["expected_runs"] == 1,
        "status",
        "expected exactly one run",
    )
    for collection in (payload.get("runs"), payload.get("tracks")):
        _require(
            isinstance(collection, list)
            and len(collection) == 1
            and isinstance(collection[0], dict)
            and collection[0].get("track") == "direct",
            "status",
            "wrong track/run inventory",
        )
    for record, expected in (
        (
            payload["runs"][0],
            {
                "model": "portable-placeholder",
                "run_index": 1,
                "started": False,
                "report_present": False,
            },
        ),
        (payload["tracks"][0], {"expected_runs": 1, "completion_present": False}),
    ):
        for key, value in expected.items():
            _require(
                type(record.get(key)) is type(value) and record[key] == value,
                "status",
                f"wrong {key}",
            )
    for record in [progress, *payload["runs"], *payload["tracks"]]:
        for key in (
            "checkpointed_results",
            "provider_attempts",
            "tokens_input",
            "tokens_output",
            "total_tokens",
        ):
            _require(
                type(record.get(key)) is int and record[key] == 0,
                "status",
                f"nonzero or missing {key}",
            )
    for key in ("runs_started", "runs_reported", "completed_results"):
        _require(type(progress.get(key)) is int and progress[key] == 0, "status", f"wrong {key}")


def validate_generation(root: Path, product: str, seed: int) -> dict:
    try:
        return _validate_generation(root, product, seed)
    except (OSError, ValueError, TypeError, AttributeError, zipfile.BadZipFile) as exc:
        raise PortableFailure("generation", "malformed generated artifacts") from exc


def _validate_generation(root: Path, product: str, seed: int) -> dict:
    stem = f"{product}-v1-seed-{seed}"
    names = {f"{stem}.zip", f"{stem}_manifest.json"}
    _require(
        {p.name for p in root.iterdir()} == names,
        "generation",
        "unexpected generated artifact inventory",
    )
    manifest = json.loads((root / f"{stem}_manifest.json").read_text())
    metadata = manifest.get("metadata", {})
    _require(
        metadata.get("seed") == seed
        and metadata.get("benchmark") == product
        and metadata.get("benchmark_version") == "v1",
        "generation",
        "incorrect generated identity/seed",
    )
    identity = metadata.get("identity", {})
    _require(
        all(isinstance(identity.get(k), str) and identity[k] for k in ("company_name", "domain")),
        "generation",
        "missing benchmark identity",
    )
    with zipfile.ZipFile(root / f"{stem}.zip") as archive:
        _require(archive.testzip() is None, "generation", "generated ZIP CRC failure")
    return {name: _digest((root / name).read_bytes()) for name in sorted(names)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("python", "wheel", "output-dir"):
        parser.add_argument(f"--{option}", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        python, wheel, root = validate_arguments(args.python, args.wheel, args.output_dir)
    except (PortableFailure, OSError) as exc:
        parser.error(str(exc))
    root.mkdir(mode=0o700)
    receipt = {
        "schema_version": "ori-portable-installation-v1",
        "state": "failed",
        "probes": [],
        "scope": "ordinary Python audit operations; not an OS sandbox",
        "fixtures": "inert/non-certified; never use for readiness",
        "artifacts": {},
    }
    receipt_path = root / "qualification.private.json"
    try:
        for child in (
            "outside checkout",
            "config files",
            "generated artifacts",
            "empty home",
            "temporary files",
            "logs",
        ):
            (root / child).mkdir(mode=0o700)
        environment = child_environment(python, root)
        cwd = root / "outside checkout"

        def probe(
            identifier: str, mode_args: list[str], expected: int = 0, timeout: float = 30
        ) -> str:
            result = run_probe(
                identifier,
                [str(python), "-I", "-c", BOOTSTRAP, *mode_args],
                environment,
                cwd,
                expected,
                timeout,
                root / "logs",
            )
            receipt["probes"].append(result)
            print(identifier)
            return Path(result["stdout_path"]).read_text(encoding="utf-8")

        probe("guard-self-test", ["self-test"])
        wheel_info = wheel_inventory(wheel)
        receipt["wheel_sha256"] = wheel_info["sha256"]
        observed = json.loads(probe("installation", ["inspect"]))
        verify_installation(wheel_info, observed)
        receipt["environment"] = observed
        receipt["package_inventory"] = observed["members"]
        console = ["console", observed["console_script"]]
        help_text = probe("help", [*console, "--help"])
        _require(
            all(
                re.search(rf"^\s+{re.escape(name)}(?:\s|$)", help_text, re.M)
                for name in ("generate", "run-v2", "campaign-status")
            ),
            "output",
            "missing help commands",
        )
        listing = probe("products", [*console, "benchmark", "list"])
        _require(
            all(re.search(rf"^\s*{name}(?:\s|$)", listing, re.M) for name in ("simple", "complex")),
            "output",
            "missing product identifiers",
        )
        for product, seed in (("simple", 1234), ("complex", 4401)):
            destination = root / "generated artifacts" / f"{product} output"
            probe(
                f"generate-{product}",
                [*console, "generate", product, "--seed", str(seed), "--output", str(destination)],
                timeout=180,
            )
            receipt["artifacts"][product] = validate_generation(destination, product, seed)
        config_root = root / "config files"
        for name in (
            "manifest",
            "archive",
            "public",
            "oracles",
            "candidates",
            "live_certification",
        ):
            (config_root / name).write_bytes(b"")
        config = {
            "version": 2,
            "protocol": "ori-eval-protocol-v2",
            "source": {"manifest": "manifest", "archive": "archive"},
            "tracks": {
                "direct": {k: k for k in ("public", "oracles", "candidates", "live_certification")}
            },
            "modes": ["direct"],
            "output_dir": "campaign output",
            "defaults": {"runs_per_model": 1, "concurrency": 1},
            "models": [
                {
                    "name": "portable-placeholder",
                    "provider": "openai-compat",
                    "model": "placeholder",
                    "model_base_url": "http://127.0.0.1:9/v1",
                }
            ],
        }
        config_path = config_root / "status-config.yaml"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        poison = cwd / "campaign output"
        poison.write_bytes(b"cwd poison: do not follow\n")
        poison_digest = _digest(poison.read_bytes())
        status_args = [*console, "campaign-status", "--config", str(config_path), "--json"]
        validate_status(json.loads(probe("status-relative", status_args)))
        correct_output = config_root / "campaign output"
        _require(
            not os.path.lexists(correct_output) and _digest(poison.read_bytes()) == poison_digest,
            "status",
            "config-relative output or cwd poison changed",
        )
        correct_output.write_bytes(b"config-relative poison\n")
        probe("status-poison", status_args, expected=1)
        stderr = Path(receipt["probes"][-1]["stderr_path"]).read_text()
        _require(
            "campaign output path is not a directory" in stderr,
            "output",
            "missing status path error",
        )
        for identifier, extra, phrase in (
            ("missing-config-option", [], "Missing option"),
            (
                "missing-config-file",
                ["--config", str(config_root / "does not exist.yaml")],
                "does not exist",
            ),
        ):
            probe(identifier, [*console, "run-v2", *extra], expected=2)
            error = Path(receipt["probes"][-1]["stderr_path"]).read_text()
            _require(
                phrase in error and ("--config" in error or "does not exist.yaml" in error),
                "output",
                "missing actionable config error",
            )
        receipt["state"] = "pass"
    except Exception as exc:
        if isinstance(exc, PortableFailure) and exc.probe is not None:
            receipt["probes"].append(exc.probe)
        receipt["failure"] = {
            "kind": exc.kind if isinstance(exc, PortableFailure) else "driver",
            "message": str(exc),
        }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{receipt['state']}: {receipt_path}")
    return 0 if receipt["state"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
