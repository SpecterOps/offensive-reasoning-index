"""Pinned native source inventories, not read-only or live qualification.

Discovery descriptors remain native. A matching inventory and fingerprint do
not certify backend parity, tool safety, result semantics, or provider readiness.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .fingerprint import canonical_sha256


@dataclass(frozen=True, slots=True)
class NativeImplementation:
    implementation_id: str
    repository: str
    revision: str
    backend: Literal["bhce", "neo4j"]
    entrypoint: str
    credential_env_names: tuple[str, ...]
    native_tool_names: tuple[str, ...]
    prompt_names: tuple[str, ...]
    resources_expected: bool
    generic_query_tool: str | None
    resource_uris: tuple[str, ...] = ()


_IMPLEMENTATIONS = MappingProxyType({
    "mwnickerson": NativeImplementation(
        implementation_id="mwnickerson",
        repository="https://github.com/mwnickerson/bloodhound_mcp",
        revision="92a37dd481ce675fe552f14c9957a31dbbcd212e",
        backend="bhce", entrypoint="main.py",
        credential_env_names=("BLOODHOUND_TOKEN_ID", "BLOODHOUND_TOKEN_KEY"),
        native_tool_names=(
        "domain_info",
        "user_info",
        "group_info",
        "computer_info",
        "ou_info",
        "gpo_info",
        "graph_analysis",
        "adcs_info",
        "cypher_query",
        "data_quality",
        "custom_nodes",
        "asset_groups",
        "file_upload",
    ),
        prompt_names=("bloodhound_assistant",), resources_expected=True,
        generic_query_tool="cypher_query",
        resource_uris=(
        "bloodhound://cypher/reference",
        "bloodhound://guides/ad",
        "bloodhound://guides/azure",
        "bloodhound://guides/adcs",
        "bloodhound://guides/ad-methodology",
        "bloodhound://guides/azure-methodology",
        "bloodhound://guides/adcs-methodology",
        "bloodhound://opengraph/guide",
        "bloodhound://opengraph/examples",
        "bloodhound://cypher/offensive-queries",
    ),
    ),
    "mordavid": NativeImplementation(
        implementation_id="mordavid",
        repository="https://github.com/MorDavid/BloodHound-MCP-AI",
        revision="1eb21b01da14fd2eda941234e3a545e876bef296",
        backend="neo4j", entrypoint="BloodHound-MCP.py",
        credential_env_names=("BLOODHOUND_USERNAME", "BLOODHOUND_PASSWORD"),
        native_tool_names=(
        "query_bloodhound",
        "find_all_domain_admins",
        "map_domain_trusts",
        "find_tier_zero_locations",
        "map_ou_structure",
        "find_dcsync_privileges",
        "find_foreign_group_memberships",
        "find_domain_users_local_admins",
        "find_domain_users_laps_readers",
        "find_domain_users_high_value_paths",
        "find_domain_users_workstation_rdp",
        "find_domain_users_server_rdp",
        "find_domain_users_privileges",
        "find_domain_admin_non_dc_logons",
        "find_kerberoastable_tier_zero",
        "find_all_kerberoastable_users",
        "find_kerberoastable_most_admin",
        "find_asreproast_users",
        "find_shortest_paths_unconstrained_delegation",
        "find_paths_from_kerberoastable_to_da",
        "find_shortest_paths_to_tier_zero",
        "find_paths_from_domain_users_to_tier_zero",
        "find_shortest_paths_to_domain_admins",
        "find_paths_from_owned_objects",
        "find_pki_hierarchy",
        "find_public_key_services",
        "find_certificate_enrollment_rights",
        "find_esc1_vulnerable_templates",
        "find_esc2_vulnerable_templates",
        "find_enrollment_agent_templates",
        "find_dcs_weak_certificate_binding",
        "find_inactive_tier_zero_principals",
        "find_tier_zero_without_smartcard",
        "find_domains_with_machine_quota",
        "find_smartcard_dont_expire_domains",
        "find_two_way_forest_trust_delegation",
        "find_unsupported_operating_systems",
        "find_users_with_no_password_required",
        "find_users_password_not_rotated",
        "find_nested_tier_zero_groups",
        "find_disabled_tier_zero_principals",
        "find_principals_reversible_encryption",
        "find_principals_des_only_kerberos",
        "find_principals_weak_kerberos_encryption",
        "find_tier_zero_non_expiring_passwords",
        "find_ntlm_relay_edges",
        "find_esc8_vulnerable_cas",
        "find_computers_outbound_ntlm_deny",
        "find_computers_in_protected_users",
        "find_dcs_vulnerable_ntlm_relay",
        "find_computers_webclient_running",
        "find_computers_no_smb_signing",
        "find_global_administrators",
        "find_high_privileged_role_members",
        "find_paths_from_entra_to_tier_zero",
        "find_paths_to_privileged_roles",
        "find_paths_from_azure_apps_to_tier_zero",
        "find_paths_to_azure_subscriptions",
        "sp_app_role_grant",
        "find_sp_graph_assignments",
        "find_foreign_tier_zero_principals",
        "find_synced_tier_zero_principals",
        "find_external_tier_zero_users",
        "find_disabled_azure_tier_zero_principals",
        "find_devices_unsupported_os",
        "find_entra_users_in_domain_admins",
        "find_onprem_users_owning_entra_objects",
        "find_onprem_users_in_entra_groups",
        "templates_no_security_ext",
        "templates_with_user_san",
        "find_ca_administrators",
        "onprem_users_direct_entra_roles",
        "onprem_users_group_entra_roles",
        "onprem_users_direct_azure_roles",
        "onprem_users_group_azure_roles",
    ),
        prompt_names=(), resources_expected=False, generic_query_tool="query_bloodhound",
    ),
    "armadin": NativeImplementation(
        implementation_id="armadin",
        repository="https://github.com/armadin-public/bloodhound-mcp-server",
        revision="6ad4a4703d1117c3019400539911ca689a537197",
        backend="neo4j", entrypoint="main.py",
        credential_env_names=("NEO4J_USERNAME", "NEO4J_PASSWORD"),
        native_tool_names=(
        "find_generic_all_principals",
        "find_write_dacl_principals",
        "find_write_owner_principals",
        "find_shadow_credentials_targets",
        "find_force_change_password_principals",
        "find_esc1_vulnerable_templates",
        "find_esc2_vulnerable_templates",
        "find_esc3_enrollment_agent_abuse",
        "find_esc4_template_modification",
        "find_esc6_sanitization_bypass",
        "find_esc7_vulnerable_cas",
        "find_esc8_ntlm_relay_to_adcs",
        "find_exploit_chains",
        "find_gpo_abuse_paths",
        "find_laps_password_readers",
        "find_gmsa_password_readers",
        "find_writespn_abuse",
        "find_computer_takeover_paths",
        "find_ou_control_abuse",
        "find_krbtgt_access_paths",
        "find_dns_admin_escalation",
        "find_addself_to_group",
        "find_sql_admin_escalation",
        "find_adminsdhoulder_abuse",
        "find_shortest_path",
        "find_paths_to_domain_admin",
        "analyze_attack_surface",
        "find_kerberoastable_paths",
        "find_asreproastable_users",
        "find_attack_paths_to_target",
        "analyze_computer",
        "find_file_share_servers",
        "find_infrastructure_servers",
        "find_jump_servers",
        "search_computers_by_os",
        "find_passwords_in_attributes",
        "find_passwords_in_attribute_by_type",
        "find_passwords_in_custom_attribute",
        "find_dcsync_rights",
        "find_unconstrained_delegation",
        "find_constrained_delegation_abuse",
        "find_resource_based_constrained_delegation",
        "find_domains",
        "find_domain_admins",
        "find_domain_controllers",
        "analyze_domain_trusts",
        "find_tier_zero_assets",
        "analyze_group",
        "find_privileged_groups",
        "list_all_groups",
        "search_groups",
        "find_groups_by_member",
        "analyze_group_nesting",
        "find_empty_groups",
        "find_groups_with_foreign_members",
        "find_high_value_targets",
        "find_oversized_groups",
        "analyze_group_permissions",
        "search_ad_by_description",
        "analyze_user",
        "get_user_sid",
        "dop2mop_list_artifacts",
        "dop2mop_find_container_images",
        "dop2mop_find_datasets",
        "dop2mop_find_storage_buckets",
        "dop2mop_find_scenario1_ado_to_azureml",
        "dop2mop_find_scenario2_container_poisoning",
        "dop2mop_find_scenario3_oidc_abuse",
        "dop2mop_find_scenario4_dataset_poisoning",
        "dop2mop_find_all_attack_paths",
        "dop2mop_find_paths_to_ml_compute",
        "dop2mop_find_devops_to_mlops_connections",
        "dop2mop_list_devops_nodes",
        "dop2mop_find_github_repositories",
        "dop2mop_find_github_workflows",
        "dop2mop_find_ado_pipelines",
        "dop2mop_find_ado_service_connections",
        "dop2mop_find_ado_agent_pools",
        "dop2mop_find_secrets_and_variables",
        "dop2mop_list_identities",
        "dop2mop_find_oidc_trusts",
        "dop2mop_find_iam_roles",
        "dop2mop_find_service_principals",
        "dop2mop_find_credential_exposure",
        "dop2mop_list_mlops_nodes",
        "dop2mop_find_azure_ml_workspaces",
        "dop2mop_find_ml_compute_resources",
        "dop2mop_find_sagemaker_resources",
        "dop2mop_find_ml_models_and_endpoints",
        "dop2mop_analyze_tb1_code_to_pipeline",
        "dop2mop_analyze_tb2_service_principal_auth",
        "dop2mop_analyze_tb3_container_trust",
        "dop2mop_analyze_tb4_job_execution",
        "dop2mop_analyze_tb5_deserialization",
        "dop2mop_trust_boundary_summary",
    ),
        prompt_names=("bloodhound_prompt",), resources_expected=False, generic_query_tool=None,
    ),
})


def get_native_implementation(implementation_id: str) -> NativeImplementation:
    """Return immutable source metadata; unknown implementations fail closed."""
    try:
        return _IMPLEMENTATIONS[implementation_id]
    except (KeyError, TypeError) as exc:
        raise ValueError("unknown native MCP implementation") from exc


def _descriptors(values: list[dict], key: str, label: str) -> dict[str, dict]:
    if not isinstance(values, list):
        raise ValueError(f"{label} discovery must be a list")
    result = {}
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(f"{label} descriptor must be an object")
        name = value.get(key)
        if not isinstance(name, str) or not name or name in result:
            raise ValueError(f"{label} contains missing or duplicate {key}")
        result[name] = value
    return result


def _validate_reference_policy(schema: dict) -> None:
    """Reject references rather than permit implicit resolver I/O or recursion.

    The pinned native signatures use primitive arguments, not referenced models.
    Future reference-bearing schemas require explicit offline resolver admission.
    Walk every nested value conservatively without rewriting native descriptors.
    """
    pending: list[object] = [schema]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, (dict, list)):
            if id(value) in seen:
                raise ValueError("native schemas must not contain cyclic or shared containers")
            seen.add(id(value))
        if isinstance(value, dict):
            if any(key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")):
                raise ValueError("native schema references require explicit offline admission")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def validate_native_discovery(
    implementation_id: str,
    tools: list[dict],
    prompts: list[dict],
    resources: list[dict],
    resource_templates: list[dict],
) -> str:
    """Validate complete native discovery and hash descriptors without modifying them.

    Schema validity is checked, not equivalence to an unpinned SDK-generated
    schema. The resulting fingerprint must be bound to subsequent qualification.
    These pins declare static resources only; resource templates are rejected.
    """
    implementation = get_native_implementation(implementation_id)
    tool_map = _descriptors(tools, "name", "tool")
    prompt_map = _descriptors(prompts, "name", "prompt")
    resource_map = _descriptors(resources, "uri", "resource")
    templates = _descriptors(resource_templates, "uriTemplate", "resource template")
    if set(tool_map) != set(implementation.native_tool_names):
        raise ValueError("native tool inventory differs from pinned source")
    if set(prompt_map) != set(implementation.prompt_names):
        raise ValueError("native prompt inventory differs from pinned source")
    if set(resource_map) != set(implementation.resource_uris) or templates:
        raise ValueError("native resource inventory differs from pinned source")
    for descriptor in tool_map.values():
        schema = descriptor.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("native tool requires an object inputSchema")
        _validate_reference_policy(schema)
        try:
            Draft202012Validator.check_schema(schema)
            if descriptor.get("outputSchema") is not None:
                output = descriptor["outputSchema"]
                if not isinstance(output, dict) or output.get("type") != "object":
                    raise ValueError("native tool requires an object outputSchema")
                _validate_reference_policy(output)
                Draft202012Validator.check_schema(output)
        except SchemaError as exc:
            raise ValueError("invalid native tool schema") from exc
    for descriptor in prompt_map.values():
        arguments = _descriptors(
            descriptor.get("arguments") if descriptor.get("arguments") is not None else [],
            "name", "prompt argument",
        )
        if arguments:
            raise ValueError("pinned native prompts do not accept arguments")
    payload = {
        "schema_version": "ori-native-mcp-discovery-v1",
        "implementation_id": implementation.implementation_id,
        "revision": implementation.revision,
        "tools": [tool_map[key] for key in sorted(tool_map)],
        "prompts": [prompt_map[key] for key in sorted(prompt_map)],
        "resources": [resource_map[key] for key in sorted(resource_map)],
        "resource_templates": [],
    }
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("native discovery must contain finite JSON values") from exc
    return canonical_sha256(payload)
