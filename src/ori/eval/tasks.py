"""Task schema and auto-generation from manifest."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from ori.relationships import canonical_relationship_kind, live_relationship_kinds


@dataclass
class Task:
    id: str
    template_id: str
    tier: int
    category: str  # path_finding | enumeration | cypher_generation
    question: str
    reference_cypher: str
    grade_mode: str  # path_exists | no_path | node_set | row_count
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# Per-template question definitions.
# Each entry is a tuple of:
#   (question, category, grade_mode, tags, reference_cypher_template | None)
# reference_cypher_template overrides the planted path's verification_cypher for this task.
# None means use the planted path's verification_cypher unchanged (correct for path_finding tasks).
# Placeholders: {source_name}, {target_name}, {domain}
_TEMPLATE_QUESTIONS: dict[str, list[tuple[str, str, str, list[str], str | None]]] = {
    "t1_admin_to": [
        (
            "What is the attack path from {source_name} to {target_name}? Show the full path.",
            "path_finding",
            "path_exists",
            ["admin_to", "member_of"],
            None,  # use verification_cypher from manifest
        ),
    ],
    "t1_has_session": [
        (
            "Which computers have active sessions from members of the Domain Admins group "
            "or groups nested within it?",
            "enumeration",
            "node_set",
            ["has_session", "lateral_movement"],
            # Returns the computers themselves, not the full path
            "MATCH (c:Computer)-[:HasSession]->(u:User)-[:MemberOf*1..]->(g:Group"
            " {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN c",
        ),
        (
            "Show the active-session relationship between {target_name} and "
            "{source_name}.",
            "path_finding",
            "path_exists",
            ["has_session"],
            None,
        ),
    ],
    "t1_group_membership": [
        (
            "Show the membership path from {source_name} to {target_name}.",
            "path_finding",
            "path_exists",
            ["member_of", "privilege_escalation"],
            None,
        ),
        (
            "Find all users who are direct members of the Domain Admins group "
            "(not through nested group membership).",
            "enumeration",
            "node_set",
            ["member_of", "domain_admins"],
            # Direct MemberOf only — not transitive
            "MATCH (u:User)-[:MemberOf]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN u",
        ),
    ],
    "t2_kerberoast_chain": [
        (
            "Show the attack path from Kerberoastable service account {source_name} "
            "to server {target_name}.",
            "path_finding",
            "path_exists",
            ["kerberoasting", "service_accounts"],
            None,
        ),
        (
            "List all user accounts that have a Service Principal Name (SPN) set, "
            "making them vulnerable to Kerberoasting.",
            "enumeration",
            "node_set",
            ["kerberoasting", "spn"],
            "MATCH (u:User {{hasspn: true}}) RETURN u",
        ),
    ],
    "t2_acl_chain": [
        (
            "Show the GenericAll-to-AdminTo abuse path from {source_name} to "
            "{target_name}.",
            "path_finding",
            "path_exists",
            ["acl", "generic_all", "admin_to"],
            None,
        ),
        (
            "Find the ACL abuse path from {source_name} to admin access on {target_name} "
            "through group rights abuse. Include the regular user, abused group, and "
            "target server.",
            "path_finding",
            "path_exists",
            ["acl", "privilege_escalation"],
            None,
        ),
    ],
    "t2_nested_groups": [
        (
            "Find the full attack path from {source_name} to {target_name}, "
            "including all nested group membership hops.",
            "path_finding",
            "path_exists",
            ["nested_groups", "member_of"],
            None,
        ),
        (
            "Show the complete group nesting chain that connects INFRA-TEAM@{domain} "
            "to Server-Admins.",
            "path_finding",
            "path_exists",
            ["nested_groups"],
            "MATCH p=(g:Group {{name: 'INFRA-TEAM@{domain}'}})-[:MemberOf*1..]->"
            "(sa:Group {{name: 'SERVER-ADMINS@{domain}'}}) RETURN p",
        ),
    ],
    "t3_unconstrained_delegation": [
        (
            "Show the active-session relationship proving that {source_name} has a "
            "session on non-domain-controller {target_name}, which has unconstrained "
            "Kerberos delegation enabled.",
            "path_finding",
            "path_exists",
            ["unconstrained_delegation", "kerberos"],
            None,
        ),
        (
            "Which computers have unconstrained delegation configured? Exclude domain controllers.",
            "enumeration",
            "node_set",
            ["unconstrained_delegation"],
            "MATCH (c:Computer {{unconstraineddelegation: true}}) WHERE NOT c.isdc = true RETURN c",
        ),
    ],
    "t3_constrained_delegation": [
        (
            "Show the constrained-delegation path from service account {source_name} "
            "to domain controller {target_name}.",
            "path_finding",
            "path_exists",
            ["constrained_delegation", "s4u2proxy"],
            None,
        ),
        (
            "List all accounts with the 'Trust this computer for delegation to specified "
            "services only' (trustedtoauth) flag set.",
            "enumeration",
            "node_set",
            ["constrained_delegation"],
            "MATCH (u:User {{trustedtoauth: true}}) RETURN u",
        ),
    ],
    "t4_adcs_esc1": [
        (
            "Identify the ESC1-style certificate abuse path from {source_name} to "
            "{target_name}. "
            "Include the enrollee, enrollment group, certificate template, issuing CA, "
            "root CA, NTAuth trust object, and the privileged target.",
            "path_finding",
            "path_exists",
            ["phase4", "adcs", "esc1", "certificate_abuse"],
            None,
        ),
    ],
    "t4_rbcd_delegation": [
        (
            "Identify the advanced delegation abuse path from {source_name} to "
            "{target_name}. Include the principal, delegation edge, and target.",
            "path_finding",
            "path_exists",
            ["phase4", "delegation", "rbcd"],
            None,
        ),
    ],
    "t5_adcs_to_delegation_composite": [
        (
            "Find the composite Phase 4 path where {source_name} chains ESC1 certificate "
            "abuse with GenericWrite over SVC_PHASE4_BRIDGE@{domain} and delegation to "
            "{target_name}. Include all critical ADCS, service, delegation, and "
            "target nodes.",
            "path_finding",
            "path_exists",
            ["phase4", "adcs", "delegation", "composite"],
            None,
        ),
    ],
}

_TIER6_COMPLEX_QUESTIONS: list[tuple[str, str, str, list[str], str | None]] = [
    (
        "Starting from {source_name}, determine the viable multi-hop route to {target_name}. "
        "Use multiple graph lookups: identify the host/user/group sequence, the mechanism at "
        "each hop, and the terminal Tier 0 condition. Reject any attractive dead ends.",
        "decision_complexity",
        "path_exists",
        ["tier6", "complex", "multi_hop", "operator_reasoning"],
        None,
    ),
]

_TIER6_NEGATIVE_CONTROL_QUESTIONS: list[tuple[str, str, str, list[str], str | None]] = [
    (
        "Starting from {source_name}, evaluate whether the apparent certificate route to "
        "{target_name} is actually viable. If it is not viable, explain the missing mechanism "
        "or invalid edge instead of forcing a path.",
        "negative_control",
        "no_path",
        ["tier6", "complex", "negative_control", "decoy_rejection"],
        None,
    ),
]


def _return_projection_aliases(projection: str) -> list[str]:
    """Return stable aliases for a Cypher projection used across a WITH boundary."""
    items: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(projection):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote != "`":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(projection[start:index].strip())
            start = index + 1
    items.append(projection[start:].strip())

    aliases: list[str] = []
    for item in items:
        alias_match = re.search(
            r"\s+AS\s+(`?[A-Za-z_][A-Za-z0-9_]*`?)\s*$",
            item,
            re.IGNORECASE,
        )
        if alias_match:
            aliases.append(alias_match.group(1))
        elif re.fullmatch(r"`?[A-Za-z_][A-Za-z0-9_]*`?", item):
            aliases.append(item)
        else:
            raise ValueError(
                "Supporting-edge reference computed RETURN expressions must declare an alias: "
                f"{item}"
            )
    return aliases


def _reference_cypher_with_supporting_edges(path: dict[str, Any]) -> str:
    """Return reference Cypher that also proves declared contextual relationships."""
    reference = (
        _exact_edge_reference_cypher(path)
        if path.get("tier") == 6
        and not path.get("negative_control")
        and path.get("path_edges")
        else str(path["verification_cypher"])
    )
    supporting_edges = [edge for edge in path.get("supporting_edges", []) if isinstance(edge, dict)]
    if not supporting_edges:
        return reference

    body, separator, returns = reference.rpartition(" RETURN ")
    if not separator:
        raise ValueError(
            f"Supporting-edge reference for {path.get('template_id', 'unknown')} has no RETURN"
        )
    clauses: list[str] = []
    projection_aliases = _return_projection_aliases(returns)
    return_items = [*projection_aliases]
    for index, edge in enumerate(supporting_edges, start=1):
        source = str(edge["source"]).replace("'", "\\'")
        target = str(edge["target"]).replace("'", "\\'")
        edge_kind = "|".join(live_relationship_kinds(str(edge["edge"])))
        source_alias = f"ctx{index}s"
        target_alias = f"ctx{index}t"
        clauses.append(
            f"MATCH ({source_alias})-[:{edge_kind}]->({target_alias}) "
            f"WHERE coalesce({source_alias}.objectid, {source_alias}.objectId) = '{source}' "
            f"AND coalesce({target_alias}.objectid, {target_alias}.objectId) = '{target}'"
        )
        return_items.extend((source_alias, target_alias))
    supporting_return_items = return_items[len(projection_aliases) :]
    grouped_values = [returns, *supporting_return_items]
    return (
        f"{body} {' '.join(clauses)} "
        f"WITH {', '.join(grouped_values)}, count(*) AS ori_evidence_count "
        f"WHERE ori_evidence_count = 1 RETURN {', '.join(return_items)}"
    )


def _exact_edge_reference_cypher(path: dict[str, Any]) -> str:
    """Materialize every planted Tier-6 edge without relying on shortestPath."""

    clauses: list[str] = []
    return_items: list[str] = []
    for index, edge in enumerate(path.get("path_edges", []), start=1):
        source = str(edge["source"]).replace("'", "\\'")
        target = str(edge["target"]).replace("'", "\\'")
        edge_kind = "|".join(live_relationship_kinds(str(edge["edge"])))
        source_alias = f"edge{index}s"
        target_alias = f"edge{index}t"
        clauses.append(
            f"MATCH ({source_alias})-[:{edge_kind}]->({target_alias}) "
            f"WHERE coalesce({source_alias}.objectid, {source_alias}.objectId) = "
            f"'{source}' AND coalesce({target_alias}.objectid, "
            f"{target_alias}.objectId) = '{target}'"
        )
        return_items.extend((source_alias, target_alias))
    if not return_items:
        raise ValueError(
            f"Tier-6 reference for {path.get('template_id', 'unknown')} has no path edges"
        )
    return (
        f"{' '.join(clauses)} WITH {', '.join(return_items)}, "
        "count(*) AS ori_evidence_count WHERE ori_evidence_count = 1 "
        f"RETURN {', '.join(return_items)}"
    )


def _canonical_edge(edge: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(edge)
    artifact_kind = str(edge["edge"])
    canonical_kind = canonical_relationship_kind(artifact_kind)
    if artifact_kind != canonical_kind:
        normalized["artifact_edge"] = artifact_kind
    normalized["edge"] = canonical_kind
    return normalized


def _questions_for_template(
    template_id: str,
) -> list[tuple[str, str, str, list[str], str | None]]:
    if template_id == "t6_negative_control_invalid_cert":
        return _TIER6_NEGATIVE_CONTROL_QUESTIONS
    if template_id.startswith("t6_"):
        return _TIER6_COMPLEX_QUESTIONS
    return _TEMPLATE_QUESTIONS.get(template_id, [])


_GLOBAL_TASKS: list[tuple[str, str, str, str, list[str]]] = [
    # (id_suffix, question, category, grade_mode, tags)
    (
        "global-kerberoastable",
        "List all user accounts with a Service Principal Name (SPN) set (hasspn = true).",
        "enumeration",
        "node_set",
        ["kerberoasting"],
    ),
    (
        "global-admin-to",
        "Find all computers where the Domain Admins group has AdminTo (local admin) access.",
        "enumeration",
        "node_set",
        ["admin_to", "domain_admins"],
    ),
    (
        "global-da-members",
        "List all users who are direct or transitive members of the Domain Admins group.",
        "enumeration",
        "node_set",
        ["domain_admins", "member_of"],
    ),
    (
        "global-unconstrained",
        "Find all computers with unconstrained Kerberos delegation enabled, "
        "excluding domain controllers.",
        "enumeration",
        "node_set",
        ["unconstrained_delegation"],
    ),
    (
        "global-privileged-sessions",
        "Find all computers that have active sessions from members of the Domain Admins group.",
        "enumeration",
        "node_set",
        ["has_session", "lateral_movement"],
    ),
]


def _official_internal_grade_mode(task: dict[str, Any]) -> str:
    """Map explicit Phase 4B/v2 official tasks onto existing grader modes."""
    grade_mode = str(task.get("grade_mode", "")).strip()
    if grade_mode and grade_mode != "mechanical_binary":
        return grade_mode
    category = str(task.get("category", "")).strip()
    if category in {"enumeration", "startup_smoke"}:
        return "node_set"
    return "path_exists"


def _phase4b_smoke_reference_cypher(task: dict[str, Any]) -> str:
    task_id = str(task.get("id", ""))
    reference_raw = task.get("reference")
    reference: dict[str, Any] = reference_raw if isinstance(reference_raw, dict) else {}
    domains = [str(domain).upper() for domain in reference.get("domains", [])]
    parent_domain = domains[0] if domains else "FOREST.EXAMPLE"
    child_domain = domains[1] if len(domains) > 1 else f"CHILD.{parent_domain}"

    if task_id.endswith("startup-domain-count"):
        return "MATCH (d:Domain) RETURN d"
    if task_id.endswith("startup-parent-da"):
        return f"MATCH (g:Group {{name: 'DOMAIN ADMINS@{parent_domain}'}}) RETURN g"
    if task_id.endswith("startup-child-da"):
        return f"MATCH (g:Group {{name: 'DOMAIN ADMINS@{child_domain}'}}) RETURN g"
    if task_id.endswith("startup-ca-presence"):
        return (
            "MATCH (n) WHERE any(label IN labels(n) "
            "WHERE toLower(label) CONTAINS 'cert' OR toLower(label) CONTAINS 'ca') RETURN n"
        )
    return "MATCH (n) RETURN n LIMIT 1"


def _official_reference_cypher(task: dict[str, Any]) -> str:
    reference_cypher = str(task.get("reference_cypher") or "").strip()
    if reference_cypher:
        return reference_cypher
    if bool(task.get("smoke_task")) or str(task.get("phase")) == "startup_smoke":
        return _phase4b_smoke_reference_cypher(task)
    return ""


def _generate_official_tasks(manifest: dict[str, Any]) -> list[Task]:
    """Load explicit official Phase 4B/v2 tasks when the manifest provides them."""
    raw_tasks = manifest.get("tasks_official")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        return []

    expected = manifest.get("official_count") or manifest.get("raw_score_denominator")
    if expected is not None and len(raw_tasks) != int(expected):
        raise ValueError(
            f"tasks_official contains {len(raw_tasks)} task(s), expected {int(expected)}"
        )

    tasks: list[Task] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_tasks, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"tasks_official[{index}] is not an object")
        task_id = str(raw.get("id") or f"official-{index:03d}").strip()
        if task_id in seen_ids:
            raise ValueError(f"Duplicate official task id: {task_id}")
        seen_ids.add(task_id)
        reference_cypher = _official_reference_cypher(raw)
        if not reference_cypher:
            raise ValueError(f"Official task {task_id} has no reference_cypher")

        raw_mitre = raw.get("mitre", [])
        tasks.append(
            Task(
                id=task_id,
                template_id=str(raw.get("template_id") or raw.get("scenario_family") or "official"),
                tier=int(raw.get("technical_difficulty") or 1),
                category=str(raw.get("category") or "official"),
                question=str(raw.get("question") or ""),
                reference_cypher=reference_cypher,
                grade_mode=_official_internal_grade_mode(raw),
                tags=[str(value) for value in raw_mitre] if isinstance(raw_mitre, list) else [],
                metadata={
                    "phase": raw.get("phase", ""),
                    "track": raw.get("track", ""),
                    "scenario_family": raw.get("scenario_family", ""),
                    "domain_role": raw.get("domain_role", ""),
                    "smoke_task": bool(raw.get("smoke_task", False)),
                    "official_grade_mode": raw.get("grade_mode", ""),
                    "benchmark_weight": raw.get("benchmark_weight", ""),
                    "scoring": raw.get("scoring", {}),
                    "template_instance_id": raw.get("template_instance_id", ""),
                    "template_version": raw.get("template_version", ""),
                    "technical_difficulty": raw.get("technical_difficulty", ""),
                    "reasoning_difficulty": raw.get("reasoning_difficulty", ""),
                    "mitre": raw_mitre,
                    "graph_diagnostics": raw.get("graph_diagnostics", {}),
                    "official_index": index,
                    "official_count": len(raw_tasks),
                },
            )
        )
    return tasks


def generate_tasks(manifest: dict) -> list[Task]:
    """Generate evaluation tasks from a manifest dict."""
    official_tasks = _generate_official_tasks(manifest)
    if official_tasks:
        return official_tasks

    domain = manifest.get("domain", "CORP.LOCAL")
    tasks: list[Task] = []

    for path in manifest.get("planted_paths", []):
        tid = path["template_id"]
        source_name = path.get("source_name", path.get("source_node", ""))
        target_name = path.get("target_name", path.get("target_node", ""))

        for i, (q_tmpl, category, grade_mode, tags, ref_cypher_tmpl) in enumerate(
            _questions_for_template(tid), start=1
        ):
            question = q_tmpl.format(
                source_name=source_name,
                target_name=target_name,
                domain=domain,
            )
            diagnostic_nodes = path.get("diagnostic_nodes") or path.get("metadata", {}).get(
                "diagnostic_nodes", []
            )
            if (
                tid == "t4_adcs_esc1"
                and diagnostic_nodes
                and set(map(str, path.get("critical_nodes", []))).isdisjoint(
                    map(str, diagnostic_nodes)
                )
            ):
                question = question.replace(
                    ", root CA, NTAuth trust object, and ",
                    " and ",
                )
            # Use per-task reference Cypher if defined, otherwise fall back to the
            # planted path's verification_cypher (correct for path_finding tasks).
            if ref_cypher_tmpl is not None:
                reference_cypher = ref_cypher_tmpl.format(
                    source_name=source_name,
                    target_name=target_name,
                    domain=domain,
                )
            else:
                reference_cypher = _reference_cypher_with_supporting_edges(path)
            supporting_edges = [
                _canonical_edge(edge)
                for edge in path.get("supporting_edges", [])
                if isinstance(edge, dict)
            ]
            path_edges = [
                _canonical_edge(edge)
                for edge in path.get("path_edges", [])
                if isinstance(edge, dict)
            ]
            if supporting_edges:
                contextual_relationships = sorted(
                    f"{edge.get('source_name', edge['source'])} "
                    f"-[{edge['edge']}]-> {edge.get('target_name', edge['target'])}"
                    for edge in supporting_edges
                )
                question += (
                    " Explicitly include these contextual relationships: "
                    + ", ".join(contextual_relationships)
                    + "."
                )
            tasks.append(
                Task(
                    id=f"{tid}-{i:02d}",
                    template_id=tid,
                    tier=path["tier"],
                    category=category,
                    question=question,
                    reference_cypher=reference_cypher,
                    grade_mode=grade_mode,
                    tags=tags,
                    metadata={
                        "source_name": source_name,
                        "target_name": target_name,
                        "reference_scope": (
                            "reference_defined"
                            if ref_cypher_tmpl is not None
                            else "anchored"
                        ),
                        "domain": domain,
                        "description": path["description"],
                        "mitre": path.get("mitre", []),
                        "scenario_family": path.get("scenario_family", ""),
                        "critical_nodes": path.get("critical_nodes", []),
                        "required_capabilities": path.get("required_capabilities", []),
                        "required_mechanisms": path.get("required_mechanisms", []),
                        "required_sequence": path.get("required_sequence", []),
                        "supporting_edges": supporting_edges,
                        "reference_evidence": (
                            {
                                "required_edges": (
                                    [*path_edges, *supporting_edges]
                                    if path.get("tier") == 6
                                    and grade_mode == "path_exists"
                                    else supporting_edges
                                ),
                                "expected_cardinality": 1,
                                "endpoint_anchored": True,
                            }
                            if (
                                supporting_edges
                                or (
                                    path.get("tier") == 6
                                    and grade_mode == "path_exists"
                                )
                            )
                            else None
                        ),
                        "answer_contract": (
                            {
                                "required_nodes": (
                                    path.get("critical_nodes", [])
                                    if path.get("tier") == 6
                                    and grade_mode == "path_exists"
                                    else []
                                ),
                                "required_edges": supporting_edges,
                                "grade_mode": grade_mode,
                                "oracle": "materialized_reference",
                                "set_semantics": (
                                    "exact" if grade_mode == "node_set" else ""
                                ),
                                "notes": (
                                    "The live reference result defines the exact node set."
                                    if grade_mode == "node_set"
                                    else "Planted critical nodes and contextual relationships "
                                    "must be reported."
                                ),
                            }
                            if (
                                supporting_edges
                                or grade_mode == "node_set"
                                or (
                                    path.get("tier") == 6
                                    and grade_mode == "path_exists"
                                )
                            )
                            else None
                        ),
                        "terminal_escalation_type": path.get("terminal_escalation_type", ""),
                        "tool_effort": path.get("tool_effort", {}),
                        "negative_control": path.get("negative_control", False),
                        "expected_rejection_reasons": path.get("expected_rejection_reasons", []),
                        "decoy_edges": path.get("decoy_edges", []),
                        "invalidated_edges": path.get("invalidated_edges", []),
                        "template_version": path.get("template_version", ""),
                    },
                )
            )

    # Global tasks use a placeholder reference Cypher (will be graded path_exists / node_set
    # against the actual BH CE results — reference Cypher runs live)
    _GLOBAL_REFERENCE_CYPHER = {
        "global-kerberoastable": "MATCH (u:User {hasspn: true}) RETURN u",
        "global-admin-to": f"MATCH (g:Group {{name: 'DOMAIN ADMINS@{domain}'}})-[:AdminTo]->(c:Computer) RETURN c",  # noqa: E501
        "global-da-members": f"MATCH (u:User)-[:MemberOf*1..]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN u",  # noqa: E501
        "global-unconstrained": "MATCH (c:Computer {unconstraineddelegation: true}) WHERE NOT c.isdc = true RETURN c",  # noqa: E501
        "global-privileged-sessions": f"MATCH (c:Computer)-[:HasSession]->(u:User)-[:MemberOf*1..]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN c",  # noqa: E501
    }

    for id_suffix, question, category, grade_mode, tags in _GLOBAL_TASKS:
        tasks.append(
            Task(
                id=id_suffix,
                template_id="global",
                tier=1,
                category=category,
                question=question.format(domain=domain),
                reference_cypher=_GLOBAL_REFERENCE_CYPHER[id_suffix],
                grade_mode=grade_mode,
                tags=tags,
                metadata={
                    "domain": domain,
                    "reference_scope": "reference_defined",
                    "answer_contract": {
                        "grade_mode": grade_mode,
                        "oracle": "materialized_reference",
                        "set_semantics": "exact",
                        "notes": "The live reference result defines the exact node set.",
                    },
                },
            )
        )

    return tasks


def generate_mcp_tasks(manifest: dict) -> list[Task]:
    """Generate the Phase 3B MCP task corpus.

    This currently includes:
    - the existing direct-Cypher benchmark tasks, relabeled as MCP-assisted tasks
    - a small MCP-native starter corpus that emphasizes higher-level BloodHound tools
    """
    official_tasks = _generate_official_tasks(manifest)
    if official_tasks:
        return [
            replace(
                task,
                metadata={
                    **task.metadata,
                    "mcp_track": task.metadata.get("track", "official"),
                    "preferred_tool_family": (
                        "non_cypher" if task.metadata.get("track") == "mcp" else "cypher_or_mixed"
                    ),
                },
            )
            for task in official_tasks
        ]

    tasks: list[Task] = []
    for task in generate_tasks(manifest):
        metadata = dict(task.metadata)
        metadata.update(
            {
                "mcp_track": "mcp_cypher_assisted",
                "preferred_tool_family": "cypher_or_mixed",
            }
        )
        tasks.append(replace(task, metadata=metadata))

    tasks.extend(_generate_mcp_native_tasks(manifest))
    return tasks


def _global_reference_cypher_map(domain: str) -> dict[str, str]:
    return {
        "global-kerberoastable": "MATCH (u:User {hasspn: true}) RETURN u",
        "global-admin-to": f"MATCH (g:Group {{name: 'DOMAIN ADMINS@{domain}'}})-[:AdminTo]->(c:Computer) RETURN c",  # noqa: E501
        "global-da-members": f"MATCH (u:User)-[:MemberOf*1..]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN u",  # noqa: E501
        "global-unconstrained": "MATCH (c:Computer {unconstraineddelegation: true}) WHERE NOT c.isdc = true RETURN c",  # noqa: E501
        "global-privileged-sessions": f"MATCH (c:Computer)-[:HasSession]->(u:User)-[:MemberOf*1..]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN c",  # noqa: E501
    }


def _generate_mcp_native_tasks(manifest: dict) -> list[Task]:
    domain = manifest.get("domain", "CORP.LOCAL")
    reference_globals = _global_reference_cypher_map(domain)
    planted_by_template = {path["template_id"]: path for path in manifest.get("planted_paths", [])}
    tasks: list[Task] = []

    def add_task(
        *,
        id: str,
        template_id: str,
        tier: int,
        question: str,
        reference_cypher: str,
        grade_mode: str,
        tags: list[str],
        metadata: dict | None = None,
    ) -> None:
        task_metadata = {
            "domain": domain,
            "mcp_track": "mcp_non_cypher_analysis",
            "preferred_tool_family": "non_cypher",
        }
        if metadata:
            task_metadata.update(metadata)
        if grade_mode == "node_set" and "answer_contract" not in task_metadata:
            task_metadata["answer_contract"] = {
                "grade_mode": grade_mode,
                "oracle": "materialized_reference",
                "set_semantics": "exact",
                "notes": "The live reference result defines the exact node set.",
            }
        task_metadata.setdefault(
            "reference_scope",
            (
                "anchored"
                if grade_mode in {"path_exists", "no_path"}
                and task_metadata.get("source_name")
                and task_metadata.get("target_name")
                else "reference_defined"
            ),
        )
        tasks.append(
            Task(
                id=id,
                template_id=template_id,
                tier=tier,
                category="mcp_analysis",
                question=question,
                reference_cypher=reference_cypher,
                grade_mode=grade_mode,
                tags=tags,
                metadata=task_metadata,
            )
        )

    # Global / domain-level MCP-native tasks
    add_task(
        id="mcp-global-admin-to",
        template_id="global",
        tier=1,
        question=(
            "Use BloodHound MCP tools to determine which computers the Domain Admins group "
            "has local admin access to. Prefer higher-level BloodHound tools before raw Cypher."
        ),
        reference_cypher=reference_globals["global-admin-to"],
        grade_mode="node_set",
        tags=["mcp_native", "admin_to", "domain_admins"],
    )
    add_task(
        id="mcp-global-privileged-sessions",
        template_id="global",
        tier=1,
        question=(
            "Use BloodHound MCP tools to find all computers with active sessions from Domain "
            "Admins or groups nested beneath Domain Admins. Prefer non-Cypher tools."
        ),
        reference_cypher=reference_globals["global-privileged-sessions"],
        grade_mode="node_set",
        tags=["mcp_native", "sessions", "domain_admins"],
    )
    add_task(
        id="mcp-global-da-direct-members",
        template_id="global",
        tier=1,
        question=(
            "Using BloodHound MCP group tools, list the direct members of the Domain Admins "
            "group. Prefer group/member inspection over raw Cypher."
        ),
        reference_cypher=(
            f"MATCH (n)-[:MemberOf]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN n"
        ),
        grade_mode="node_set",
        tags=["mcp_native", "group_members", "domain_admins"],
    )
    add_task(
        id="mcp-global-da-direct-member-count",
        template_id="global",
        tier=1,
        question=(
            "Using BloodHound MCP group tools, count how many direct members are in the Domain "
            "Admins group. Return only the numeric count in the final answer JSON."
        ),
        reference_cypher=(
            f"MATCH (n)-[:MemberOf]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN n"
        ),
        grade_mode="row_count",
        tags=["mcp_native", "group_members", "row_count"],
    )

    group_membership = planted_by_template.get("t1_group_membership")
    if group_membership:
        source_name = group_membership.get("source_name", "")
        add_task(
            id="mcp-user-privileged-group-memberships",
            template_id="t1_group_membership",
            tier=1,
            question=(
                f"Using BloodHound MCP tools, determine which privileged groups {source_name} "
                "belongs to. Return the privileged group names you find."
            ),
            reference_cypher=(
                f"MATCH (u:User {{name: '{source_name}'}})-[:MemberOf*1..]->(g:Group) RETURN g"
            ),
            grade_mode="node_set",
            tags=["mcp_native", "memberships", "privileged_groups"],
            metadata={"source_name": source_name},
        )
        add_task(
            id="mcp-user-group-memberships-mmoore",
            template_id="t1_group_membership",
            tier=1,
            question=(
                f"Use BloodHound MCP user and group tools to list all groups {source_name} belongs to. "  # noqa: E501
                "Prefer user membership inspection over raw Cypher."
            ),
            reference_cypher=(
                f"MATCH (u:User {{name: '{source_name}'}})-[:MemberOf*1..]->(g:Group) RETURN g"
            ),
            grade_mode="node_set",
            tags=["mcp_native", "memberships", "user"],
            metadata={"source_name": source_name},
        )

    session_path = planted_by_template.get("t1_has_session")
    if session_path:
        source_name = session_path.get("source_name", "")
        target_name = session_path.get("target_name", "")
        add_task(
            id="mcp-computer-active-sessions",
            template_id="t1_has_session",
            tier=1,
            question=(
                f"Using BloodHound MCP tools, list the users with active sessions on {source_name}. "  # noqa: E501
                "Prefer computer/session-focused tools."
            ),
            reference_cypher=(
                f"MATCH (c:Computer {{name: '{source_name}'}})-[:HasSession]->(u:User) RETURN u"
            ),
            grade_mode="node_set",
            tags=["mcp_native", "sessions", "computer"],
            metadata={"source_name": source_name, "target_name": target_name},
        )
        add_task(
            id="mcp-user-session-locations-privileged-user",
            template_id="t1_has_session",
            tier=1,
            question=(
                f"Using BloodHound MCP tools, list the computers where {target_name} currently has an active session. "  # noqa: E501
                "Prefer user/session-focused tools."
            ),
            reference_cypher=(
                f"MATCH (c:Computer)-[:HasSession]->(u:User {{name: '{target_name}'}}) RETURN c"
            ),
            grade_mode="node_set",
            tags=["mcp_native", "sessions", "user"],
            metadata={"source_name": source_name, "target_name": target_name},
        )
        add_task(
            id="mcp-shortest-path-has-session",
            template_id="t1_has_session",
            tier=1,
            question=(
                f"Use BloodHound MCP graph tools to show the session path connecting {source_name} to {target_name}. "  # noqa: E501
                "Prefer graph/path tools before raw Cypher."
            ),
            reference_cypher=session_path["verification_cypher"],
            grade_mode="path_exists",
            tags=["mcp_native", "sessions", "attack_path"],
            metadata={"source_name": source_name, "target_name": target_name},
        )

    admin_to = planted_by_template.get("t1_admin_to")
    if admin_to:
        source_name = admin_to.get("source_name", "")
        target_name = admin_to.get("target_name", "")
        add_task(
            id="mcp-shortest-path-admin-to",
            template_id="t1_admin_to",
            tier=1,
            question=(
                f"Use BloodHound MCP graph-analysis tools to find the shortest attack path from "
                f"{source_name} to {target_name}. Prefer graph/path tools before raw Cypher."
            ),
            reference_cypher=admin_to["verification_cypher"],
            grade_mode="path_exists",
            tags=["mcp_native", "shortest_path", "attack_path"],
            metadata={"source_name": source_name, "target_name": target_name},
        )
        add_task(
            id="mcp-computer-admin-users-dc01",
            template_id="t1_admin_to",
            tier=1,
            question=(
                f"Using BloodHound MCP computer tools, list the users or groups that have admin rights on {target_name}. "  # noqa: E501
                "Prefer computer-focused rights inspection over raw Cypher."
            ),
            reference_cypher=(
                f"MATCH (n)-[:AdminTo]->(c:Computer {{name: '{target_name}'}}) RETURN n"
            ),
            grade_mode="node_set",
            tags=["mcp_native", "admin_users", "computer"],
            metadata={"source_name": source_name, "target_name": target_name},
        )

    nested = planted_by_template.get("t2_nested_groups")
    if nested:
        source_name = nested.get("source_name", "")
        target_name = nested.get("target_name", "")
        add_task(
            id="mcp-shortest-path-nested-groups",
            template_id="t2_nested_groups",
            tier=2,
            question=(
                f"Use BloodHound MCP graph and membership tools to show the complete path from "
                f"{source_name} to {target_name}. Prefer higher-level graph tools before raw Cypher."  # noqa: E501
            ),
            reference_cypher=nested["verification_cypher"],
            grade_mode="path_exists",
            tags=["mcp_native", "nested_groups", "attack_path"],
            metadata={"source_name": source_name, "target_name": target_name},
        )
        add_task(
            id="mcp-group-members-server-admins",
            template_id="t2_nested_groups",
            tier=2,
            question=(
                "Using BloodHound MCP group tools, list the direct members of SERVER-ADMINS. "
                "Prefer group/member inspection over raw Cypher."
            ),
            reference_cypher=(
                f"MATCH (n)-[:MemberOf]->(g:Group {{name: 'SERVER-ADMINS@{domain}'}}) RETURN n"
            ),
            grade_mode="node_set",
            tags=["mcp_native", "group_members", "server_admins"],
        )
        add_task(
            id="mcp-group-memberships-infra-team",
            template_id="t2_nested_groups",
            tier=2,
            question=(
                f"Using BloodHound MCP group tools, list the groups that INFRA-TEAM@{domain} belongs to. "  # noqa: E501
                "Prefer group membership inspection over raw Cypher."
            ),
            reference_cypher=(
                f"MATCH (g1:Group {{name: 'INFRA-TEAM@{domain}'}})-[:MemberOf*1..]->(g2:Group) RETURN g2"  # noqa: E501
            ),
            grade_mode="node_set",
            tags=["mcp_native", "group_memberships", "nested_groups"],
        )
        add_task(
            id="mcp-group-admin-rights-server-admins",
            template_id="t2_nested_groups",
            tier=2,
            question=(
                "Using BloodHound MCP group tools, list the computers that SERVER-ADMINS has admin rights over. "  # noqa: E501
                "Prefer group rights inspection over raw Cypher."
            ),
            reference_cypher=(
                f"MATCH (g:Group {{name: 'SERVER-ADMINS@{domain}'}})-[:AdminTo]->(c:Computer) RETURN c"  # noqa: E501
            ),
            grade_mode="node_set",
            tags=["mcp_native", "admin_rights", "server_admins"],
        )
        add_task(
            id="mcp-computer-admin-users-srv-file-01",
            template_id="t2_nested_groups",
            tier=2,
            question=(
                f"Using BloodHound MCP computer tools, list the users or groups that have admin rights on {target_name}. "  # noqa: E501
                "Prefer computer-focused rights inspection over raw Cypher."
            ),
            reference_cypher=(
                f"MATCH (n)-[:AdminTo]->(c:Computer {{name: '{target_name}'}}) RETURN n"
            ),
            grade_mode="node_set",
            tags=["mcp_native", "admin_users", "computer"],
            metadata={"source_name": source_name, "target_name": target_name},
        )

    kerberoast = planted_by_template.get("t2_kerberoast_chain")
    if kerberoast:
        source_name = kerberoast.get("source_name", "")
        target_name = kerberoast.get("target_name", "")
        add_task(
            id="mcp-shortest-path-kerberoast-chain",
            template_id="t2_kerberoast_chain",
            tier=2,
            question=(
                f"Use BloodHound MCP graph and group tools to find the path from {source_name} to {target_name}. "  # noqa: E501
                "Prefer graph/path tools before raw Cypher."
            ),
            reference_cypher=kerberoast["verification_cypher"],
            grade_mode="path_exists",
            tags=["mcp_native", "kerberoasting", "attack_path"],
            metadata={"source_name": source_name, "target_name": target_name},
        )

    acl_chain = planted_by_template.get("t2_acl_chain")
    if acl_chain:
        source_name = acl_chain.get("source_name", "")
        target_name = acl_chain.get("target_name", "")
        add_task(
            id="mcp-shortest-path-acl-chain",
            template_id="t2_acl_chain",
            tier=2,
            question=(
                f"Use BloodHound MCP graph tools to find the ACL abuse path from {source_name} to {target_name}. "  # noqa: E501
                "Prefer graph/path tools before raw Cypher."
            ),
            reference_cypher=acl_chain["verification_cypher"],
            grade_mode="path_exists",
            tags=["mcp_native", "acl", "attack_path"],
            metadata={"source_name": source_name, "target_name": target_name},
        )

    unconstrained = planted_by_template.get("t3_unconstrained_delegation")
    if unconstrained:
        source_name = unconstrained.get("source_name", "")
        target_name = unconstrained.get("target_name", "")
        add_task(
            id="mcp-computer-active-sessions-unconstrained",
            template_id="t3_unconstrained_delegation",
            tier=3,
            question=(
                f"Using BloodHound MCP tools, list the users with active sessions on {target_name}. "  # noqa: E501
                "Prefer computer/session-focused tools."
            ),
            reference_cypher=(
                f"MATCH (c:Computer {{name: '{target_name}'}})-[:HasSession]->(u:User) RETURN u"
            ),
            grade_mode="node_set",
            tags=["mcp_native", "sessions", "unconstrained_delegation"],
            metadata={"source_name": source_name, "target_name": target_name},
        )

    constrained = planted_by_template.get("t3_constrained_delegation")
    if constrained:
        source_name = constrained.get("source_name", "")
        target_name = constrained.get("target_name", "")
        add_task(
            id="mcp-user-constrained-delegation-targets",
            template_id="t3_constrained_delegation",
            tier=3,
            question=(
                f"Using BloodHound MCP user delegation tools, list the computers or services that {source_name} "  # noqa: E501
                "can delegate to. Prefer delegation-focused tools before raw Cypher."
            ),
            reference_cypher=(
                f"MATCH (u:User {{name: '{source_name}'}})-[:AllowedToDelegate]->(c) RETURN c"
            ),
            grade_mode="node_set",
            tags=["mcp_native", "constrained_delegation", "user"],
            metadata={"source_name": source_name, "target_name": target_name},
        )

    return tasks
