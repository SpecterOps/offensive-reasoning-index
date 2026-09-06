from __future__ import annotations

import json
import subprocess
import sys
import traceback
from copy import deepcopy
from pathlib import Path

import pytest

from ori.eval.v2 import campaign_runner, model_card
from ori.eval.v2.fingerprint import canonical_sha256
from ori.eval.v2.model_card import ModelCardBuildError, build_model_card
from ori.eval.v2.schema import Track
from tests.support.v2_model_card import (
    _MODEL,
    _campaign,
    _catalog,
    _model_report,
    _state,
)


def test_build_model_card_is_deterministic_separate_and_public_safe(
    tmp_path: Path,
) -> None:
    campaign = _campaign(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    card = build_model_card(
        campaign,
        first,
        model=_MODEL,
        display_name="Poolside: Laguna S 2.1",
    )
    build_model_card(
        campaign,
        second,
        model=_MODEL,
        display_name="Poolside: Laguna S 2.1",
    )

    assert card["campaign_valid"] is True
    assert card["tracks"]["direct"]["scheduled"] == 2
    assert card["tracks"]["direct"]["model_failures"] == 2
    assert card["tracks"]["direct"]["effective_accuracy"] == 0.0
    assert card["tracks"]["direct"]["reasoning_accuracy"] is None
    assert card["tracks"]["direct"]["output_compliance_rate"] is None
    assert card["tracks"]["direct"]["catalog_fingerprint"] == _catalog(Track.DIRECT)
    assert card["tracks"]["mcp"]["proof_failures"] == 1
    assert card["tracks"]["mcp"]["effective_accuracy"] == 0.0
    assert card["tracks"]["mcp"]["catalog_fingerprint"] == _catalog(Track.MCP)
    assert card["operational_metrics"]["attempts_total"] == 4
    assert card["operational_metrics"]["tokens_input_total"] == 4
    assert card["tracks"]["direct"]["operational_metrics"]["attempts_total"] == 2
    assert card["tracks"]["direct"]["operational_metrics"]["resource_mode"] == ("not_applicable")
    assert card["tracks"]["mcp"]["operational_metrics"]["resource_mode"] == "off"
    assert card["tracks"]["mcp"]["operational_metrics"]["mcp_tool_calls_average_per_task"] == 0.0
    assert set(path.name for path in first.iterdir()) == {
        "v30-model-card.json",
        "v30-model-card.svg",
    }
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
        text = path.read_text()
        assert str(tmp_path) not in text
        assert "private-campaign-root" not in text
        assert "NOUS_API_KEY" not in text
    svg = (first / "v30-model-card.svg").read_text()
    assert 'width="1280" height="720"' in svg
    assert "No combined score is reported" in svg


def test_build_model_card_rejects_incomplete_lifecycle(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path, status="interrupted")

    with pytest.raises(ModelCardBuildError, match="not completed execution"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_invalid_track_completion(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path, valid=False)

    with pytest.raises(ModelCardBuildError, match="track completion is invalid"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_mismatched_graph(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path, mcp_graph="9" * 64)

    with pytest.raises(ModelCardBuildError, match="mcp graph fingerprint mismatch"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_mismatched_direct_mcp_models(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path, mcp_model="different/model")

    with pytest.raises(ModelCardBuildError, match="exactly one common"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_extra_public_report(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    source = next((campaign / "direct").glob("*/run-*/public-report-v2.json"))
    state_source = next((campaign / "direct").glob("*/run-*/run-state-v7.private.json"))
    extra = campaign / "direct" / "stale-local-name" / "run-001" / source.name
    extra_state = campaign / "direct" / "stale-local-name" / "run-001" / state_source.name
    extra.parent.mkdir(parents=True)
    extra.write_bytes(source.read_bytes())
    extra_state.write_bytes(state_source.read_bytes())

    with pytest.raises(ModelCardBuildError, match="duplicate direct public run identity"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


def test_build_model_card_rejects_local_path_in_public_label(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)

    with pytest.raises(ModelCardBuildError, match="must not contain a local path"):
        build_model_card(
            campaign,
            tmp_path / "output",
            model=_MODEL,
            display_name="/Users/operator/private-model",
        )


def test_build_model_card_rejects_summary_that_disagrees_with_rows(
    tmp_path: Path,
) -> None:
    campaign = _campaign(tmp_path, summary_mismatch=True)

    with pytest.raises(ModelCardBuildError, match="invalid direct public report"):
        build_model_card(campaign, tmp_path / "output", model=_MODEL)


@pytest.mark.parametrize(
    ("field_name", "field_value", "match"),
    (
        ("output_compliant", False, "output compliance flag disagrees"),
        ("output_normalized", True, "output normalization flag disagrees"),
    ),
)
def test_model_card_rejects_output_compliance_state_mismatch(
    field_name: str,
    field_value: bool,
    match: str,
) -> None:
    state = _state(Track.DIRECT)
    report = _model_report(Track.DIRECT)
    rows = list(report.report.rows)
    rows[0] = rows[0].model_copy(update={field_name: field_value})
    report = report.model_copy(
        update={"report": report.report.model_copy(update={"rows": tuple(rows)})}
    )

    with pytest.raises(ModelCardBuildError, match=match):
        model_card._assert_run_state_matches_report(report, state)


def test_build_model_card_json_contains_no_rows_or_private_payloads(
    tmp_path: Path,
) -> None:
    campaign = _campaign(tmp_path)
    output = tmp_path / "output"
    build_model_card(campaign, output, model=_MODEL)

    payload = json.loads((output / "v30-model-card.json").read_text())
    serialized = json.dumps(payload).casefold()
    for forbidden in (
        '"rows":',
        '"prompt":',
        '"response":',
        '"tool_body":',
        '"credential_source":',
        '"target_fingerprint":',
        "local-config-name",
    ):
        assert forbidden not in serialized


def _frozen_campaign_bytes(tmp_path: Path) -> tuple[Path, dict[Path, bytes]]:
    root = _campaign(tmp_path / "immutable-baseline")
    files = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.json")}
    assert len(files) == 10
    return root, files


def _copy_campaign_bytes(files: dict[Path, bytes], root: Path) -> Path:
    for relative, content in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return root


def _assert_frozen_campaign_bytes(root: Path, files: dict[Path, bytes]) -> None:
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*.json")} == files


def _run_file(root: Path, track: Track, filename: str) -> Path:
    return root / track.value / "local-config-name" / "run-001" / filename


def _rehash(payload: dict, field: str) -> None:
    payload[field] = canonical_sha256(payload, exclude_fields=(field,))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _validate_campaign_files(root: Path) -> None:
    schemas = {
        "campaign-provenance-v2.json": campaign_runner.ModelRunProvenanceV2,
        "run-state-v7.private.json": campaign_runner.PrivateRunStateV2,
        "public-report-v2.json": campaign_runner.ModelPublicReportV2,
        "track-completion-v2.private.json": campaign_runner.TrackCompletionV2,
        "v2-run-readiness.private.json": campaign_runner.CampaignReadinessV2,
        "campaign-lifecycle-v2.private.json": campaign_runner.CampaignLifecycleV2,
    }
    paths = tuple(root.rglob("*.json"))
    assert len(paths) == 10
    for path in paths:
        schemas[path.name].model_validate_json(path.read_bytes())


_BASE_FIELDS = (
    "product",
    "track",
    "public_artifact_fingerprint",
    "oracle_artifact_fingerprint",
    "catalog_fingerprint",
    "graph_fingerprint",
    "compiler_fingerprint",
    "comparator_fingerprint",
    "capability_profile_fingerprint",
)
_IDENTITY_FIELDS = ("provider", "model", "run_index", "target_fingerprint", "tool_loop")
_PROVIDER_FIELDS = (
    "requested_api_surface",
    "resolved_api_surface",
    "structured_output_mode",
    "endpoint_family",
    "credential_source",
)


def _mixed_evidence_cases() -> tuple[tuple[str, str], ...]:
    return (
        *((f"checkpoint.{field}", "checkpoint/provenance metadata") for field in _BASE_FIELDS),
        *((f"provenance.base.{field}", "checkpoint/provenance metadata") for field in _BASE_FIELDS),
        ("state.provenance_fingerprint", "state/provenance binding"),
        *((f"provenance.run_identity.{field}", "run identity") for field in _IDENTITY_FIELDS),
        ("provenance.release.candidate_release_fingerprint", "release provenance"),
        ("provenance.release.live_certification_fingerprint", "release provenance"),
        ("provenance.archive.source_manifest_sha256", "readiness provenance"),
        ("provenance.archive.archive_sha256", "readiness provenance"),
        *(
            (f"provenance.provider.{field}", "provider-readiness metadata")
            for field in _PROVIDER_FIELDS
        ),
        ("public_row.task_fingerprint", "row task binding"),
        ("public_row.product", "row task binding"),
        ("public_row.track", "row task binding"),
        ("readiness.task_count", "readiness provenance"),
        ("state.scheduler_phase", "incomplete scheduler"),
        ("provenance.unmatched_file", "state/provenance binding"),
        ("readiness.split_metadata", "provider-readiness metadata"),
        ("report_body.catalog", "report/checkpoint metadata"),
        ("report_body.product", "report/checkpoint metadata"),
        ("coherent_private.oracle", "readiness provenance"),
    )


def _mutate_run_evidence(root: Path, track: Track, case: str) -> None:
    state_path = _run_file(root, track, "run-state-v7.private.json")
    provenance_path = _run_file(root, track, "campaign-provenance-v2.json")
    report_path = _run_file(root, track, "public-report-v2.json")
    readiness_path = root / "v2-run-readiness.private.json"
    state = json.loads(state_path.read_bytes())
    provenance = json.loads(provenance_path.read_bytes())
    report = json.loads(report_path.read_bytes())
    readiness = json.loads(readiness_path.read_bytes())
    different_fp = "7" * 64
    other_track = Track.MCP.value if track is Track.DIRECT else Track.DIRECT.value

    def replace_field(payload: dict, field: str, value: object) -> None:
        assert payload[field] != value
        payload[field] = value

    def base_value(field: str) -> str:
        return (
            "other-product"
            if field == "product"
            else other_track
            if field == "track"
            else different_fp
        )

    if case.startswith("checkpoint."):
        field = case.removeprefix("checkpoint.")
        replace_field(state["checkpoint"], field, base_value(field))
    elif case.startswith("provenance.base."):
        field = case.removeprefix("provenance.base.")
        replace_field(provenance["base"], field, base_value(field))
    elif case == "state.provenance_fingerprint":
        replace_field(state, "provenance_fingerprint", different_fp)
    elif case.startswith("provenance.run_identity."):
        field = case.removeprefix("provenance.run_identity.")
        values = {
            "provider": "other-provider",
            "model": "other-model",
            "run_index": 2,
            "target_fingerprint": different_fp,
            "tool_loop": "native-openai-compatible" if track is Track.DIRECT else None,
        }
        replace_field(provenance["run_identity"], field, values[field])
    elif case.startswith("provenance.release."):
        field = case.removeprefix("provenance.release.")
        replace_field(
            provenance, field, "other-candidate" if field.startswith("candidate") else "other-live"
        )
    elif case.startswith("provenance.archive."):
        replace_field(provenance, case.removeprefix("provenance.archive."), different_fp)
    elif case.startswith("provenance.provider."):
        field = case.removeprefix("provenance.provider.")
        values = {
            "requested_api_surface": "responses",
            "resolved_api_surface": "responses",
            "structured_output_mode": "json_schema",
            "endpoint_family": "generic",
            "credential_source": None,
        }
        replace_field(provenance, field, values[field])
    elif case.startswith("public_row."):
        field = case.removeprefix("public_row.")
        replace_field(report["report"]["rows"][0], field, base_value(field))
    elif case == "readiness.task_count":
        selected = next(item for item in readiness["tracks"] if item["track"] == track.value)
        replace_field(selected, "task_count", 3)
    elif case == "state.scheduler_phase":
        replace_field(state["scheduler"], "phase", "primary")
    elif case == "provenance.unmatched_file":
        replace_field(provenance, "source_manifest_sha256", different_fp)
    elif case == "readiness.split_metadata":
        assert len(readiness["models"]) == 1
        first = deepcopy(readiness["models"][0])
        second = deepcopy(first)
        first.update(name="split-one", requested_api_surface="responses")
        second.update(name="split-two", endpoint_family="generic")
        readiness["models"] = [first, second]
        readiness["model_count"] = 2
    elif case == "report_body.catalog":
        replace_field(report["report"], "catalog_fingerprint", different_fp)
    elif case == "report_body.product":
        replace_field(report["report"], "product", "other-product")
        for row in report["report"]["rows"]:
            replace_field(row, "product", "other-product")
    elif case == "coherent_private.oracle":
        replace_field(state["checkpoint"], "oracle_artifact_fingerprint", different_fp)
        replace_field(provenance["base"], "oracle_artifact_fingerprint", different_fp)
    else:
        raise AssertionError(f"unknown mutation case: {case}")

    _rehash(provenance["base"], "provenance_fingerprint")
    _rehash(provenance, "provenance_fingerprint")
    if (
        case.startswith("provenance.") and case != "provenance.unmatched_file"
    ) or case == "coherent_private.oracle":
        state["provenance_fingerprint"] = provenance["provenance_fingerprint"]
    _rehash(state["checkpoint"], "checkpoint_fingerprint")
    _rehash(state, "state_fingerprint")
    _rehash(readiness, "readiness_fingerprint")
    if case.startswith(("public_row.", "report_body.")):
        _rehash(report["report"], "report_fingerprint")
        _rehash(report, "artifact_fingerprint")
        receipt_path = root / track.value / "track-completion-v2.private.json"
        receipt = json.loads(receipt_path.read_bytes())
        assert len(receipt["runs"]) == 1
        receipt["runs"][0]["public_report_fingerprint"] = report["artifact_fingerprint"]
        _rehash(receipt, "receipt_fingerprint")
        lifecycle_path = root / "campaign-lifecycle-v2.private.json"
        lifecycle = json.loads(lifecycle_path.read_bytes())
        completed = next(
            item for item in lifecycle["completed_tracks"] if item["track"] == track.value
        )
        completed["receipt_fingerprint"] = receipt["receipt_fingerprint"]
        _rehash(lifecycle, "lifecycle_fingerprint")
        _write_json(receipt_path, receipt)
        _write_json(lifecycle_path, lifecycle)
    for path, payload in (
        (state_path, state),
        (provenance_path, provenance),
        (report_path, report),
        (readiness_path, readiness),
    ):
        _write_json(path, payload)


@pytest.mark.parametrize("track", (Track.DIRECT, Track.MCP))
def test_model_card_rejects_missing_or_invalid_provenance(
    tmp_path: Path, subtests, track: Track
) -> None:
    baseline, files = _frozen_campaign_bytes(tmp_path)
    cases = (
        "missing",
        "malformed_json",
        "invalid_root",
        "unsupported_schema",
        "incorrect_self_hash",
    )
    for case in cases:
        with subtests.test(msg=case, track=track.value):
            path_sentinel = f"SYNTHETIC_INPUT_PATH_{track.value}_{case}"
            payload_sentinel = f"SYNTHETIC_PAYLOAD_{track.value}_{case}"
            root = _copy_campaign_bytes(files, tmp_path / path_sentinel)
            path = _run_file(root, track, "campaign-provenance-v2.json")
            if case == "missing":
                path.unlink()
            elif case == "malformed_json":
                path.write_text('{"sentinel":"' + payload_sentinel + '"')
            elif case == "invalid_root":
                path.write_text(json.dumps(payload_sentinel))
            else:
                payload = json.loads(path.read_bytes())
                if case == "unsupported_schema":
                    payload["schema_version"] = "unsupported"
                else:
                    assert payload["provenance_fingerprint"] != "7" * 64
                    payload["provenance_fingerprint"] = "7" * 64
                _write_json(path, payload)
            output = tmp_path / f"output-{case}"
            category = (
                "cannot read model-run provenance"
                if case == "missing"
                else "invalid model-run provenance"
            )
            with pytest.raises(ModelCardBuildError, match=category) as error:
                build_model_card(root, output, model=_MODEL)
            for rendered in (str(error.value), "".join(traceback.format_exception(error.value))):
                assert path_sentinel not in rendered
                assert payload_sentinel not in rendered
            assert not output.exists()
            if case in {"malformed_json", "invalid_root"}:
                cli_output = tmp_path / f"cli-output-{case}"
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "ori.eval.v2.model_card",
                        "--campaign-root",
                        str(root),
                        "--output-dir",
                        str(cli_output),
                        "--model",
                        _MODEL,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                assert completed.returncode != 0
                assert category in completed.stderr
                assert path_sentinel not in completed.stderr
                assert payload_sentinel not in completed.stderr
                assert path_sentinel not in completed.stdout
                assert payload_sentinel not in completed.stdout
                assert not cli_output.exists()
    _assert_frozen_campaign_bytes(baseline, files)


def _public_string_pattern_cases() -> tuple[tuple[str, str, str | None, str | None], ...]:
    groups = (
        ("empty", "must be non-empty", ("", "   ")),
        (
            "control",
            "contains control characters",
            (
                "safe\x00name",
                "safe\rname",
                "safe\nname",
                "\tsafe",
                "safe\x7f",
                "safe\u202ename",
                "safe\u2028name",
                "safe\ud800name",
            ),
        ),
        (
            "url",
            "must not contain a URL",
            (
                "https://private.example.invalid/service",
                "Model custom+scheme://host",
                "FILE:/private/model",
                "mailto:synthetic@example.invalid",
                "Model data:text/plain,synthetic",
            ),
        ),
        (
            "path",
            "must not contain a local path",
            (
                "/private/model",
                "../private/model",
                "./private/model",
                "~/private/model",
                "Model /private/model",
                "Model (/private/model)",
                "org/../private",
                "C:/private/model",
                "C:\\private\\model",
                "\\\\server\\share",
                "org\\model",
                "Model,/private/model",
                "Model;/private/model",
                "Model)/private/model",
                "Model]/private/model",
                "Model}/private/model",
                "Model|/private/model",
                "Model</private/model",
                "Model>/private/model",
            ),
        ),
        (
            "secret",
            "resembles secret material",
            (
                "api_key=synthetic",
                "Model password:synthetic",
                "sk-synthetic",
                "Model sk-synthetic",
            ),
        ),
    )
    accepted = (
        "poolside/laguna-s-2.1",
        "Qwen/Qwen3.8-27B",
        "org/model:latest",
        "org/team/model",
        "Poolside: Laguna S 2.1",
        "Modèle local",
        "ORI (local)",
        "  Model Name  ",
        "édata:model",
    )
    return (
        *(
            (f"{group}-{index}", value, category, None)
            for group, category, values in groups
            for index, value in enumerate(values)
        ),
        *(
            (f"accepted-{index}", value, None, value.strip())
            for index, value in enumerate(accepted)
        ),
    )


def _unsafe_public_build_cases() -> tuple[tuple[str, str, str], ...]:
    return (
        ("display-url", "https://private.example.invalid/service", "must not contain a URL"),
        ("display-relative", "../synthetic-private/run", "must not contain a local path"),
        (
            "display-embedded",
            "Model /private/synthetic-private/run",
            "must not contain a local path",
        ),
        ("raw-revision", "https://private.example.invalid/revision", "must not contain a URL"),
        ("nested-release", "../synthetic-private/release", "must not contain a local path"),
    )


def _replace_public_release(root: Path, value: str) -> None:
    readiness_path = root / "v2-run-readiness.private.json"
    lifecycle_path = root / "campaign-lifecycle-v2.private.json"
    readiness = json.loads(readiness_path.read_bytes())
    lifecycle = json.loads(lifecycle_path.read_bytes())
    for track in (Track.DIRECT, Track.MCP):
        provenance_path = _run_file(root, track, "campaign-provenance-v2.json")
        state_path = _run_file(root, track, "run-state-v7.private.json")
        report_path = _run_file(root, track, "public-report-v2.json")
        receipt_path = root / track.value / "track-completion-v2.private.json"
        provenance = json.loads(provenance_path.read_bytes())
        state = json.loads(state_path.read_bytes())
        report = json.loads(report_path.read_bytes())
        receipt = json.loads(receipt_path.read_bytes())
        provenance["candidate_release_fingerprint"] = value
        _rehash(provenance, "provenance_fingerprint")
        state["provenance_fingerprint"] = provenance["provenance_fingerprint"]
        _rehash(state, "state_fingerprint")
        report["candidate_release_fingerprint"] = value
        _rehash(report, "artifact_fingerprint")
        receipt["candidate_release_fingerprint"] = value
        assert len(receipt["runs"]) == 1
        receipt["runs"][0]["public_report_fingerprint"] = report["artifact_fingerprint"]
        _rehash(receipt, "receipt_fingerprint")
        track_readiness = next(item for item in readiness["tracks"] if item["track"] == track.value)
        track_readiness["candidate_release_fingerprint"] = value
        completed = next(
            item for item in lifecycle["completed_tracks"] if item["track"] == track.value
        )
        completed["receipt_fingerprint"] = receipt["receipt_fingerprint"]
        for path, payload in (
            (provenance_path, provenance),
            (state_path, state),
            (report_path, report),
            (receipt_path, receipt),
        ):
            _write_json(path, payload)
    _rehash(readiness, "readiness_fingerprint")
    _rehash(lifecycle, "lifecycle_fingerprint")
    _write_json(readiness_path, readiness)
    _write_json(lifecycle_path, lifecycle)


def test_public_string_admission_patterns(subtests) -> None:
    cases = _public_string_pattern_cases()
    assert len(cases) == 47
    for case, value, category, expected in cases:
        with subtests.test(msg=case):
            if category is None:
                actual = model_card._public_label(value, "synthetic label")
                assert actual == expected
                assert actual.encode("utf-8") == expected.encode("utf-8")
            else:
                with pytest.raises(ModelCardBuildError) as error:
                    model_card._public_label(value, "synthetic label")
                assert str(error.value) == f"synthetic label {category}"
                if value:
                    assert value not in str(error.value)


def test_model_card_rejects_unsafe_public_strings(tmp_path: Path, subtests) -> None:
    baseline, files = _frozen_campaign_bytes(tmp_path)
    cases = _unsafe_public_build_cases()
    assert len(cases) == 5
    for case, value, category in cases:
        with subtests.test(msg=case):
            root = _copy_campaign_bytes(files, tmp_path / case)
            display_name = value if case.startswith("display-") else "Safe display"
            if case == "raw-revision":
                path = root / "v2-run-readiness.private.json"
                readiness = json.loads(path.read_bytes())
                readiness["mcp_server_revision"] = value
                _rehash(readiness, "readiness_fingerprint")
                _write_json(path, readiness)
            elif case == "nested-release":
                _replace_public_release(root, value)
            _validate_campaign_files(root)
            input_bytes = {
                path.relative_to(root): path.read_bytes() for path in root.rglob("*.json")
            }
            output = tmp_path / f"output-{case}"
            with pytest.raises(ModelCardBuildError, match=category) as error:
                build_model_card(root, output, model=_MODEL, display_name=display_name)
            label = "display name" if case.startswith("display-") else "public card string"
            assert str(error.value) == f"{label} {category}"
            assert value not in str(error.value)
            assert value not in "".join(traceback.format_exception(error.value))
            assert not output.exists()
            if case == "display-url":
                cli_output = tmp_path / "cli-output"
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "ori.eval.v2.model_card",
                        "--campaign-root",
                        str(root),
                        "--output-dir",
                        str(cli_output),
                        "--model",
                        _MODEL,
                        "--display-name",
                        value,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                assert completed.returncode != 0
                assert category in completed.stderr
                assert value not in completed.stdout
                assert value not in completed.stderr
                assert not cli_output.exists()
            _assert_frozen_campaign_bytes(root, input_bytes)
    _assert_frozen_campaign_bytes(baseline, files)


def test_public_string_recursive_boundary(subtests) -> None:
    cases = (
        ("dict-value", {"value": "../private/model"}),
        ("list-item", {"values": ["safe", "../private/model"]}),
        ("tuple-item", {"values": ("safe", "../private/model")}),
        ("mapping-key", {"../private/model": "safe"}),
    )
    for case, payload in cases:
        with subtests.test(msg=case):
            before = deepcopy(payload)
            with pytest.raises(ModelCardBuildError) as error:
                model_card._assert_public_strings(payload)
            assert str(error.value) == "public card string must not contain a local path"
            assert "../private/model" not in str(error.value)
            assert payload == before
    with subtests.test(msg="accepted-unchanged"):
        payload = {
            "org/model:latest": ["Qwen/Qwen3.8-27B", ("Modèle local", "  Model Name  ")],
            "scalars": [True, None, 3, 0.5],
        }
        before = deepcopy(payload)
        serialized = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        model_card._assert_public_strings(payload)
        assert payload == before
        assert type(payload["org/model:latest"]) is list
        assert type(payload["org/model:latest"][1]) is tuple
        assert json.dumps(payload, ensure_ascii=False).encode("utf-8") == serialized


@pytest.mark.parametrize("track", (Track.DIRECT, Track.MCP))
def test_model_card_rejects_mixed_run_evidence(tmp_path: Path, subtests, track: Track) -> None:
    baseline, files = _frozen_campaign_bytes(tmp_path)
    cases = _mixed_evidence_cases()
    assert len(cases) == 43
    assert len({case for case, _ in cases}) == 43
    for case, category in cases:
        with subtests.test(msg=case, track=track.value):
            root = _copy_campaign_bytes(files, tmp_path / case)
            _mutate_run_evidence(root, track, case)
            _validate_campaign_files(root)
            output = tmp_path / f"output-{case}"
            with pytest.raises(ModelCardBuildError, match=category):
                build_model_card(root, output, model=_MODEL)
            assert not output.exists()
    _assert_frozen_campaign_bytes(baseline, files)


@pytest.mark.parametrize("track", (Track.DIRECT, Track.MCP))
def test_model_card_accepts_full_compiled_bindings_for_selected_subset(
    tmp_path: Path, subtests, track: Track
) -> None:
    baseline, files = _frozen_campaign_bytes(tmp_path)
    for add_result in (False, True):
        case = "foreign_result" if add_result else "unused_compiled_binding"
        with subtests.test(msg=case, track=track.value):
            root = _copy_campaign_bytes(files, tmp_path / case)
            path = _run_file(root, track, "run-state-v7.private.json")
            state = json.loads(path.read_bytes())
            binding = {
                "task_id": "unused-compiled-task",
                "task_fingerprint": "6" * 64,
                "oracle_fingerprint": "6" * 64,
                "bounds_fingerprint": "6" * 64,
            }
            state["checkpoint"]["task_bindings"].append(binding)
            if add_result:
                sample = deepcopy(state["checkpoint"]["results"][0])
                sample.update(
                    task_id=binding["task_id"],
                    task_fingerprint=binding["task_fingerprint"],
                    oracle_fingerprint=binding["oracle_fingerprint"],
                )
                state["checkpoint"]["results"].append(sample)
                attempt = deepcopy(state["attempts"][0])
                attempt.update(task_id=binding["task_id"], sample=sample, attempt=1)
                attempt["provider"].update(
                    task_id=binding["task_id"], task_fingerprint=binding["task_fingerprint"]
                )
                _rehash(attempt["provider"], "record_fingerprint")
                _rehash(attempt, "attempt_fingerprint")
                state["attempts"].append(attempt)
            _rehash(state["checkpoint"], "checkpoint_fingerprint")
            _rehash(state, "state_fingerprint")
            _write_json(path, state)
            _validate_campaign_files(root)
            output = tmp_path / f"output-{case}"
            if add_result:
                with pytest.raises(
                    ModelCardBuildError, match="private state result count disagrees"
                ):
                    build_model_card(root, output, model=_MODEL)
                assert not output.exists()
            else:
                card = build_model_card(root, output, model=_MODEL)
                assert card["tracks"][track.value]["scheduled"] == 2
                assert {item.name for item in output.iterdir()} == {
                    "v30-model-card.json",
                    "v30-model-card.svg",
                }
    _assert_frozen_campaign_bytes(baseline, files)
