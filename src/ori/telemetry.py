"""Best-effort, cross-platform telemetry capture for ORI runs."""

from __future__ import annotations

import csv
import json
import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("._-") or "run"


def ollama_model_name(model: str) -> str:
    return model.split("/", 1)[1] if model.startswith("ollama/") else model


def normalize_ollama_base_url(base_url: str | None = None) -> str:
    resolved = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
    if resolved.endswith("/v1"):
        resolved = resolved[:-3].rstrip("/")
    if resolved.endswith("/api"):
        resolved = resolved[:-4].rstrip("/")
    return resolved


def _metric_unavailable(source: str, reason: str, status: str = "unavailable") -> dict[str, Any]:
    return {"status": status, "source": source, "reason": reason}


def _namedtuple_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _bytes_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key if key.endswith("_bytes") or key == "percent" else f"{key}_bytes": value
        for key, value in data.items()
        if isinstance(value, int | float)
    }


def collect_psutil_snapshot() -> dict[str, Any]:
    """Collect portable memory, swap, and Ollama process memory metrics."""

    try:
        import psutil
    except Exception as exc:  # pragma: no cover - exercised only without optional dependency.
        reason = f"{exc.__class__.__name__}: {exc}"
        return {
            "memory": _metric_unavailable("psutil.virtual_memory", reason),
            "swap": _metric_unavailable("psutil.swap_memory", reason),
            "ollama_processes": _metric_unavailable("psutil.process_iter", reason),
        }

    snapshot: dict[str, Any] = {"timestamp": utc_now_iso()}
    try:
        memory = _namedtuple_dict(psutil.virtual_memory())
        snapshot["memory"] = {
            "status": "ok",
            "source": "psutil.virtual_memory",
            **_bytes_fields(memory),
        }
    except Exception as exc:
        snapshot["memory"] = _metric_unavailable(
            "psutil.virtual_memory", f"{exc.__class__.__name__}: {exc}", "collector_error"
        )

    try:
        swap = _namedtuple_dict(psutil.swap_memory())
        snapshot["swap"] = {
            "status": "ok",
            "source": "psutil.swap_memory",
            **_bytes_fields(swap),
        }
    except Exception as exc:
        snapshot["swap"] = _metric_unavailable(
            "psutil.swap_memory", f"{exc.__class__.__name__}: {exc}", "collector_error"
        )

    processes: list[dict[str, Any]] = []
    rss_total = 0
    vms_total = 0
    uss_total = 0
    try:
        for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            info = getattr(proc, "info", {}) or {}
            name = str(info.get("name") or "").lower()
            cmdline = " ".join(info.get("cmdline") or []).lower()
            if "ollama" not in name and "ollama" not in cmdline:
                continue
            record: dict[str, Any] = {
                "pid": info.get("pid"),
                "name": info.get("name"),
                "cmdline": info.get("cmdline") or [],
            }
            try:
                mem = proc.memory_info()
                record["rss_bytes"] = int(getattr(mem, "rss", 0) or 0)
                record["vms_bytes"] = int(getattr(mem, "vms", 0) or 0)
                rss_total += record["rss_bytes"]
                vms_total += record["vms_bytes"]
            except Exception as exc:
                record["memory_info_status"] = "collector_error"
                record["memory_info_error"] = f"{exc.__class__.__name__}: {exc}"
            try:
                full_mem = proc.memory_full_info()
                record["uss_bytes"] = int(getattr(full_mem, "uss", 0) or 0)
                uss_total += record["uss_bytes"]
            except Exception:
                record["uss_bytes"] = None
            processes.append(record)
        snapshot["ollama_processes"] = {
            "status": "ok",
            "source": "psutil.process_iter",
            "process_count": len(processes),
            "rss_bytes": rss_total,
            "vms_bytes": vms_total,
            "uss_bytes": uss_total if uss_total else None,
            "processes": processes,
        }
    except Exception as exc:
        snapshot["ollama_processes"] = _metric_unavailable(
            "psutil.process_iter", f"{exc.__class__.__name__}: {exc}", "collector_error"
        )

    return snapshot


def collect_native_memory_pressure() -> dict[str, Any]:
    system = platform.system().lower()
    if system == "darwin":
        return _collect_macos_memory_pressure()
    if system == "linux":
        return _collect_linux_memory_pressure()
    return _metric_unavailable("native_memory_pressure", platform.system(), "unsupported_platform")


def _run_command(args: list[str], *, timeout: float = 5.0) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "status": "ok" if proc.returncode == 0 else "collector_error",
            "source": " ".join(args),
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except FileNotFoundError:
        return _metric_unavailable(" ".join(args), "command not found")
    except Exception as exc:
        return _metric_unavailable(
            " ".join(args),
            f"{exc.__class__.__name__}: {exc}",
            "collector_error",
        )


def _collect_macos_memory_pressure() -> dict[str, Any]:
    return {
        "status": "ok",
        "source": "macos_native_commands",
        "memory_pressure": _run_command(["memory_pressure"], timeout=5.0),
        "vm_stat": _run_command(["vm_stat"], timeout=5.0),
        "swapusage": _run_command(["sysctl", "vm.swapusage"], timeout=5.0),
    }


def _collect_linux_memory_pressure() -> dict[str, Any]:
    raw: dict[str, Any] = {"status": "ok", "source": "linux_procfs"}
    for name, path in {
        "pressure_memory": Path("/proc/pressure/memory"),
        "meminfo": Path("/proc/meminfo"),
    }.items():
        try:
            raw[name] = {"status": "ok", "source": str(path), "text": path.read_text()}
        except FileNotFoundError:
            raw[name] = _metric_unavailable(str(path), "not found", "unsupported_platform")
        except Exception as exc:
            raw[name] = _metric_unavailable(
                str(path),
                f"{exc.__class__.__name__}: {exc}",
                "collector_error",
            )
    return raw


def collect_run_environment(*, include_native: bool = True) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": utc_now_iso(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": sys.version,
            "python_executable": sys.executable,
        },
        "process": {
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
        },
        "system_snapshot": collect_psutil_snapshot(),
        "native_memory_pressure": (
            collect_native_memory_pressure()
            if include_native
            else _metric_unavailable(
                "native_memory_pressure",
                "disabled for mock/non-runtime telemetry",
                "not_applicable",
            )
        ),
        "environment": {
            "OLLAMA_BASE_URL_set": bool(os.getenv("OLLAMA_BASE_URL")),
            "OLLAMA_HOST_set": bool(os.getenv("OLLAMA_HOST")),
        },
    }


def _http_get_json(
    client: httpx.Client,
    url: str,
) -> tuple[str, dict[str, Any] | list[Any] | None, str]:
    try:
        response = client.get(url)
        response.raise_for_status()
        return "ok", response.json(), ""
    except Exception as exc:
        return "collector_error", None, f"{exc.__class__.__name__}: {exc}"


def _http_post_json(
    client: httpx.Client, url: str, payload: dict[str, Any]
) -> tuple[str, dict[str, Any] | list[Any] | None, str]:
    try:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return "ok", response.json(), ""
    except Exception as exc:
        return "collector_error", None, f"{exc.__class__.__name__}: {exc}"


def collect_ollama_model_metadata(
    *,
    model: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    if not model.startswith("ollama/"):
        return {
            "status": "not_applicable",
            "reason": "model provider is not ollama",
            "model": model,
        }

    native_base = normalize_ollama_base_url(base_url)
    name = ollama_model_name(model)
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": utc_now_iso(),
        "status": "ok",
        "provider": "ollama",
        "requested_model": model,
        "ollama_model": name,
        "ollama_base_url": native_base,
        "version": None,
        "digest": None,
        "details": {},
        "model_info": {},
        "parameters": "",
        "loaded_model": {},
        "raw": {},
    }

    timeout = httpx.Timeout(connect=1.0, read=2.0, write=2.0, pool=1.0)
    with httpx.Client(timeout=timeout) as client:
        status, payload, error = _http_get_json(client, f"{native_base}/api/version")
        metadata["raw"]["version"] = {"status": status, "payload": payload, "error": error}
        if isinstance(payload, dict):
            metadata["version"] = payload.get("version")

        status, payload, error = _http_post_json(
            client, f"{native_base}/api/show", {"model": name, "verbose": False}
        )
        metadata["raw"]["show"] = {"status": status, "payload": payload, "error": error}
        if isinstance(payload, dict):
            metadata["details"] = payload.get("details") or {}
            metadata["model_info"] = payload.get("model_info") or {}
            metadata["parameters"] = payload.get("parameters") or ""
            metadata["capabilities"] = payload.get("capabilities") or []

        status, payload, error = _http_get_json(client, f"{native_base}/api/tags")
        metadata["raw"]["tags"] = {"status": status, "payload": payload, "error": error}
        if isinstance(payload, dict):
            for item in payload.get("models") or []:
                if item.get("model") == name or item.get("name") == name:
                    metadata["digest"] = item.get("digest")
                    metadata["tagged_model"] = item
                    break

        status, payload, error = _http_get_json(client, f"{native_base}/api/ps")
        metadata["raw"]["ps"] = {"status": status, "payload": payload, "error": error}
        if isinstance(payload, dict):
            for item in payload.get("models") or []:
                if item.get("model") == name or item.get("name") == name:
                    metadata["loaded_model"] = item
                    metadata["digest"] = metadata["digest"] or item.get("digest")
                    break

    metadata["model_found"] = bool(metadata["details"] or metadata["digest"])
    if not metadata["version"] and not metadata["details"] and not metadata["digest"]:
        metadata["status"] = "collector_error"
    return metadata


def capture_modelfile(
    *,
    model: str,
    output_path: Path,
    base_url: str | None = None,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not model.startswith("ollama/"):
        text = "# status: not_applicable\n# reason: model provider is not ollama\n"
        output_path.write_text(text, encoding="utf-8")
        return {"status": "not_applicable", "path": str(output_path)}

    name = ollama_model_name(model)
    env = dict(os.environ)
    if base_url:
        env["OLLAMA_HOST"] = normalize_ollama_base_url(base_url)

    try:
        proc = subprocess.run(
            ["ollama", "show", "--modelfile", name],
            text=True,
            capture_output=True,
            timeout=5.0,
            check=False,
            env=env,
        )
        if proc.returncode == 0:
            output_path.write_text(proc.stdout, encoding="utf-8")
            return {"status": "ok", "path": str(output_path), "source": "ollama show --modelfile"}
        text = (
            "# status: collector_error\n"
            f"# source: ollama show --modelfile {name}\n"
            f"# returncode: {proc.returncode}\n"
            f"# stderr: {proc.stderr.strip()}\n"
        )
        output_path.write_text(text, encoding="utf-8")
        return {"status": "collector_error", "path": str(output_path), "error": proc.stderr.strip()}
    except FileNotFoundError:
        output_path.write_text(
            "# status: unavailable\n# reason: ollama CLI not found\n",
            encoding="utf-8",
        )
        return {"status": "unavailable", "path": str(output_path), "reason": "ollama CLI not found"}
    except Exception as exc:
        output_path.write_text(
            f"# status: collector_error\n# reason: {exc.__class__.__name__}: {exc}\n",
            encoding="utf-8",
        )
        return {"status": "collector_error", "path": str(output_path), "reason": str(exc)}


def _nested_get(data: dict[str, Any], path: list[str], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def _derive_tokens_per_second(result: Any) -> tuple[float | None, str]:
    output_tokens = int(getattr(result.model_response, "tokens_output", 0) or 0)
    metrics = dict(getattr(result.model_response, "provider_metrics", {}) or {})
    eval_duration_ns = int(metrics.get("eval_duration_ns") or 0)
    if output_tokens > 0 and eval_duration_ns > 0:
        return output_tokens / (eval_duration_ns / 1_000_000_000), "ollama_eval_duration"
    elapsed = float(getattr(result.model_response, "elapsed_seconds", 0.0) or 0.0)
    if output_tokens > 0 and elapsed > 0:
        return output_tokens / elapsed, "model_elapsed_seconds"
    return None, "unavailable"


def record_eval_telemetry(
    results: list[Any],
    *,
    output_path: Path,
    model: str,
    run_name: str | None,
    requested_model: str | None,
    run_config: dict[str, Any] | None,
    model_base_url: str | None,
    enabled: bool = True,
) -> None:
    """Write telemetry artifacts and attach per-row telemetry summaries to EvalResult objects."""

    if not results:
        return

    if not enabled:
        for result in results:
            result.telemetry = {"enabled": False}
        return

    run = run_name or requested_model or model
    run_slug = slugify(run)
    root = output_path.parent / "telemetry"
    model_dir = root / "models" / run_slug
    sample_path = root / "samples" / f"{run_slug}.jsonl"
    root.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    sample_path.parent.mkdir(parents=True, exist_ok=True)

    run_env = collect_run_environment(include_native=not model.startswith("mock/"))
    (root / "run_environment.json").write_text(
        json.dumps(run_env, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    model_metadata = collect_ollama_model_metadata(model=model, base_url=model_base_url)
    (model_dir / "model_metadata.json").write_text(
        json.dumps(model_metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if model.startswith("ollama/") and (
        model_metadata.get("status") == "collector_error" or not model_metadata.get("model_found")
    ):
        modelfile_path = model_dir / "modelfile.txt"
        modelfile_path.write_text(
            "# status: unavailable\n"
            "# reason: skipped because Ollama API metadata/model lookup was unavailable\n",
            encoding="utf-8",
        )
        modelfile_status = {
            "status": "unavailable",
            "path": str(modelfile_path),
            "reason": "skipped because Ollama API metadata/model lookup was unavailable",
        }
    else:
        modelfile_status = capture_modelfile(
            model=model,
            output_path=model_dir / "modelfile.txt",
            base_url=model_base_url,
        )

    version = model_metadata.get("version") or ""
    digest = model_metadata.get("digest") or ""
    details = model_metadata.get("details") or {}
    loaded_model = model_metadata.get("loaded_model") or {}
    quantization = details.get("quantization_level") or ""
    context_length = (
        loaded_model.get("context_length")
        or _nested_get(
            model_metadata,
            ["model_info", f"{details.get('family', '')}.context_length"],
        )
        or _nested_get(model_metadata, ["model_info", "llama.context_length"])
        or _nested_get(model_metadata, ["model_info", "gemma3.context_length"])
    )
    size_bytes = loaded_model.get("size") or _nested_get(model_metadata, ["tagged_model", "size"])
    size_vram_bytes = loaded_model.get("size_vram")

    total_tokens = 0
    tps_values: list[float] = []
    with sample_path.open("w", encoding="utf-8") as handle:
        for line_no, result in enumerate(results, 1):
            tps, tps_source = _derive_tokens_per_second(result)
            if tps is not None:
                tps_values.append(tps)
            total_tokens += int(getattr(result.model_response, "tokens_output", 0) or 0)
            task_wall_seconds = getattr(result, "task_wall_seconds", None)
            if task_wall_seconds is None:
                task_wall_seconds = getattr(result.model_response, "elapsed_seconds", None)
            system_snapshot = collect_psutil_snapshot()
            record = {
                "schema_version": SCHEMA_VERSION,
                "captured_at": utc_now_iso(),
                "run_name": run,
                "requested_model": requested_model or model,
                "resolved_model": result.model_response.model,
                "task_id": result.task.id,
                "outcome": result.grade.outcome,
                "score": result.grade.score,
                "task_wall_seconds": task_wall_seconds,
                "model_elapsed_seconds": result.model_response.elapsed_seconds,
                "tokens_input": result.model_response.tokens_input,
                "tokens_output": result.model_response.tokens_output,
                "output_tokens_per_second": tps,
                "tokens_per_second_source": tps_source,
                "provider_metrics": dict(
                    getattr(result.model_response, "provider_metrics", {}) or {}
                ),
                "direct_query": {
                    "failure_type": getattr(result.model_result, "failure_type", None),
                    "failure_subtype": getattr(result.model_result, "failure_subtype", ""),
                    "query_executed": getattr(result.model_result, "query_executed", False),
                    "execution_attempts": getattr(
                        result.model_result, "execution_attempts", 0
                    ),
                    "query_fingerprint": getattr(
                        result.model_result, "query_fingerprint", ""
                    ),
                    "safety_policy_version": getattr(
                        result.model_result, "safety_policy_version", ""
                    ),
                    "safety_rule": getattr(result.model_result, "safety_rule", ""),
                    "bhce_health_after": getattr(
                        result.model_result, "bhce_health_after", ""
                    ),
                    "circuit_state": getattr(result.model_result, "circuit_state", ""),
                },
                "run_config": run_config or {},
                "ollama": {
                    "version": version,
                    "model_digest": digest,
                    "model_context_length": context_length,
                    "model_size_bytes": size_bytes,
                    "model_size_vram_bytes": size_vram_bytes,
                    "quantization_level": quantization,
                    "modelfile_status": modelfile_status,
                },
                "system_snapshot": system_snapshot,
            }
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            result.telemetry = {
                "enabled": True,
                "sample_ref": f"{sample_path}:L{line_no}",
                "task_wall_seconds": task_wall_seconds,
                "output_tokens_per_second": tps,
                "tokens_per_second_source": tps_source,
                "ollama_version": version,
                "ollama_model_digest": digest,
                "ollama_model_context_length": context_length,
                "ollama_model_size_bytes": size_bytes,
                "ollama_model_size_vram_bytes": size_vram_bytes,
                "model_quantization_level": quantization,
            }

    summary_path = root / "telemetry_summary.csv"
    summary_exists = summary_path.exists()
    fieldnames = [
        "run_name",
        "requested_model",
        "resolved_model",
        "sample_count",
        "total_task_wall_seconds",
        "max_task_wall_seconds",
        "total_output_tokens",
        "avg_output_tokens_per_second",
        "tokens_per_second_source",
        "ollama_version",
        "ollama_model_digest",
        "ollama_model_context_length",
        "ollama_model_size_bytes",
        "ollama_model_size_vram_bytes",
        "model_quantization_level",
        "model_metadata_status",
        "modelfile_status",
        "telemetry_samples",
    ]
    task_walls = [
        float(
            getattr(result, "task_wall_seconds", None)
            or result.model_response.elapsed_seconds
            or 0.0
        )
        for result in results
    ]
    tps_sources = {r.telemetry.get("tokens_per_second_source") for r in results if r.telemetry}
    tps_source_summary = (
        "mixed"
        if len(tps_sources) > 1
        else (results[0].telemetry or {}).get("tokens_per_second_source", "")
    )
    with summary_path.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if not summary_exists:
            writer.writeheader()
        writer.writerow(
            {
                "run_name": run,
                "requested_model": requested_model or model,
                "resolved_model": results[0].model_response.model,
                "sample_count": len(results),
                "total_task_wall_seconds": round(sum(task_walls), 6),
                "max_task_wall_seconds": round(max(task_walls) if task_walls else 0.0, 6),
                "total_output_tokens": total_tokens,
                "avg_output_tokens_per_second": (
                    round(sum(tps_values) / len(tps_values), 6) if tps_values else ""
                ),
                "tokens_per_second_source": tps_source_summary,
                "ollama_version": version,
                "ollama_model_digest": digest,
                "ollama_model_context_length": context_length or "",
                "ollama_model_size_bytes": size_bytes or "",
                "ollama_model_size_vram_bytes": size_vram_bytes or "",
                "model_quantization_level": quantization,
                "model_metadata_status": model_metadata.get("status", ""),
                "modelfile_status": modelfile_status.get("status", ""),
                "telemetry_samples": str(sample_path),
            }
        )
