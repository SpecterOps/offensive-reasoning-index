"""Offline native source inventories are not live qualification receipts."""

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from ori.eval.v2.native_mcp_profiles import (
    get_native_implementation,
    validate_native_discovery,
)


def _discovery(implementation_id):
    profile = get_native_implementation(implementation_id)
    return dict(
        tools=[
            {"name": name, "description": "native", "inputSchema": {"type": "object"}}
            for name in profile.native_tool_names
        ],
        prompts=[{"name": name} for name in profile.prompt_names],
        resources=[{"name": uri, "uri": uri} for uri in profile.resource_uris],
        resource_templates=[],
    )


def test_native_capability_binds_expectations_without_claiming_qualification(subtests):
    from ori.eval.v2.native_capability import (
        NativeCapabilityProfile,
        build_native_capability_profile,
        validate_native_capability_profile,
    )
    from ori.eval.v2.profiles import capability_profile_for_track
    from ori.eval.v2.schema import Track

    for implementation in ("mwnickerson", "mordavid", "armadin"):
        with subtests.test(implementation=implementation):
            source = _discovery(implementation)
            unchanged = deepcopy(source)
            profile = build_native_capability_profile(
                implementation, runtime_fingerprint="1" * 64,
                dependency_lock_fingerprint="2" * 64,
                backend_binding_fingerprint="3" * 64, **source,
                surface_availability={"tools": True, "prompts": True, "resources": True},
            )
            assert source == unchanged
            assert validate_native_capability_profile(profile) == profile
            assert NativeCapabilityProfile.model_validate_json(profile.model_dump_json()) == profile
            assert "bloodhound_ce_version" not in profile.model_dump()
            assert not any("verified" in key or "certified" in key for key in profile.model_dump())
            assert profile.backend == get_native_implementation(implementation).backend
            with pytest.raises(ValueError, match="fingerprint"):
                validate_native_capability_profile(profile.model_copy(update={
                    "runtime_fingerprint": "4" * 64,
                }))
    with pytest.raises(TypeError, match="NativeCapabilityProfile"):
        validate_native_capability_profile(capability_profile_for_track(Track.MCP))


def test_native_capability_rejects_stale_code_and_rehashed_backend_forgery(monkeypatch):
    from ori.eval.v2 import native_capability as native
    from ori.eval.v2.fingerprint import canonical_sha256

    profile = native.build_native_capability_profile(
        "mordavid", runtime_fingerprint="1" * 64,
        dependency_lock_fingerprint="2" * 64,
        backend_binding_fingerprint="3" * 64, **_discovery("mordavid"),
        surface_availability={"tools": True, "prompts": False, "resources": False},
    )
    payload = profile.model_dump(mode="python")
    payload["backend"] = "bhce"
    fingerprint = canonical_sha256(payload, exclude_fields=("profile_id", "profile_fingerprint"))
    payload.update(
        profile_fingerprint=fingerprint, profile_id=f"ori-native-mordavid-v1-{fingerprint}",
    )
    with pytest.raises(ValueError, match="source/backend"):
        native.NativeCapabilityProfile.model_validate(payload)
    monkeypatch.setattr(native, "native_implementation_fingerprint", lambda: "f" * 64)
    with pytest.raises(ValueError, match="stale"):
        native.validate_native_capability_profile(profile)


def test_native_registry_inventory_and_immutability(subtests):
    for name, count, backend in (
        ("mwnickerson", 13, "bhce"),
        ("mordavid", 75, "neo4j"),
        ("armadin", 95, "neo4j"),
    ):
        with subtests.test(implementation=name):
            profile = get_native_implementation(name)
            assert len(profile.native_tool_names) == count
            assert len(set(profile.native_tool_names)) == count
            assert profile.backend == backend
            assert len(profile.revision) == 40
            with pytest.raises(FrozenInstanceError):
                profile.revision = "changed"
    main = get_native_implementation("mwnickerson")
    assert "file_upload" in main.native_tool_names  # inventory is not read-only admission
    assert len(main.resource_uris) == 10
    assert "sp_app_role_grant" in get_native_implementation("mordavid").native_tool_names
    assert get_native_implementation("armadin").generic_query_tool is None
    with pytest.raises(ValueError, match="unknown"):
        get_native_implementation("unknown")


def test_native_discovery_preserves_and_fingerprints_full_descriptors(subtests):
    for name in ("mwnickerson", "mordavid", "armadin"):
        with subtests.test(implementation=name):
            discovery = _discovery(name)
            original = deepcopy(discovery)
            fingerprint = validate_native_discovery(name, **discovery)
            assert discovery == original
            assert len(fingerprint) == 64
            nullable = deepcopy(discovery)
            nullable["tools"][0]["outputSchema"] = None
            for prompt in nullable["prompts"]:
                prompt["arguments"] = None
            assert validate_native_discovery(name, **nullable)
            discovery["tools"].reverse()
            assert validate_native_discovery(name, **discovery) == fingerprint
            discovery["tools"][0]["description"] = "changed native description"
            assert validate_native_discovery(name, **discovery) != fingerprint
            discovery["tools"][0]["inputSchema"]["properties"] = {"query": {"type": "string"}}
            assert validate_native_discovery(name, **discovery) != fingerprint


def test_native_discovery_fails_closed_for_drift_and_malformed_descriptors(subtests):
    for name in ("mwnickerson", "mordavid", "armadin"):
        for case in (
            "missing", "extra", "duplicate", "schema", "output-schema", "prompt",
            "resource", "template", "nonfinite", "nonjson", "not-list",
        ):
            with subtests.test(implementation=name, case=case):
                discovery = _discovery(name)
                if case == "missing":
                    discovery["tools"].pop()
                elif case == "extra":
                    discovery["tools"].append(
                        {"name": "invented", "inputSchema": {"type": "object"}}
                    )
                elif case == "duplicate":
                    discovery["tools"].append(deepcopy(discovery["tools"][0]))
                elif case == "schema":
                    discovery["tools"][0]["inputSchema"] = {"type": "object", "properties": []}
                elif case == "output-schema":
                    discovery["tools"][0]["outputSchema"] = {"type": "array"}
                elif case == "prompt":
                    discovery["prompts"].append({"name": "invented"})
                elif case == "resource":
                    discovery["resources"].append({"name": "invented", "uri": "test://invented"})
                elif case == "template":
                    discovery["resource_templates"].append({"uriTemplate": "test://{id}"})
                elif case == "nonfinite":
                    discovery["tools"][0]["extra"] = float("nan")
                elif case == "nonjson":
                    discovery["tools"][0]["extra"] = object()
                elif case == "not-list":
                    discovery["tools"] = tuple(discovery["tools"])
                with pytest.raises(ValueError):
                    validate_native_discovery(name, **discovery)


def test_native_static_resources_and_prompt_arguments(subtests):
    for case in ("missing-resource", "duplicate-resource", "duplicate-prompt", "arguments"):
        with subtests.test(case=case):
            discovery = _discovery("mwnickerson")
            if case == "missing-resource":
                discovery["resources"].pop()
            elif case == "duplicate-resource":
                discovery["resources"].append(deepcopy(discovery["resources"][0]))
            elif case == "duplicate-prompt":
                discovery["prompts"].append(deepcopy(discovery["prompts"][0]))
            else:
                discovery["prompts"][0]["arguments"] = [{"name": "invented"}]
            with pytest.raises(ValueError):
                validate_native_discovery("mwnickerson", **discovery)


def test_native_schema_references_require_offline_admission(subtests):
    for field in ("inputSchema", "outputSchema"):
        for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
            for reference in (
                "https://schema.invalid/item", "file:///private/item", "#/$defs/item",
            ):
                with subtests.test(field=field, keyword=keyword, reference=reference):
                    discovery = _discovery("armadin")
                    discovery["tools"][0][field] = {
                        "type": "object",
                        "properties": {"query": {keyword: reference}},
                    }
                    original = deepcopy(discovery)
                    with pytest.raises(ValueError, match="references require"):
                        validate_native_discovery("armadin", **discovery)
                    assert discovery == original
    discovery = _discovery("armadin")
    schema = discovery["tools"][0]["inputSchema"]
    schema["properties"] = {"recursive": schema}
    with pytest.raises(ValueError, match="cyclic"):
        validate_native_discovery("armadin", **discovery)
