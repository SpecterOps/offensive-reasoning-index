"""Task schema and auto-generation from manifest."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    template_id: str
    tier: int
    category: str         # path_finding | enumeration | cypher_generation
    question: str
    reference_cypher: str
    grade_mode: str       # path_exists | node_set | row_count
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
            "path_finding", "path_exists", ["admin_to", "member_of"],
            None,  # use verification_cypher from manifest
        ),
    ],
    "t1_has_session": [
        (
            "Which computers have active sessions from members of the Domain Admins group "
            "or groups nested within it?",
            "enumeration", "node_set", ["has_session", "lateral_movement"],
            # Returns the computers themselves, not the full path
            "MATCH (c:Computer)-[:HasSession]->(u:User)-[:MemberOf*1..]->(g:Group"
            " {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN c",
        ),
        (
            "Find all workstations where a privileged user (Domain Admin or IT Admin) "
            "has an active session.",
            "path_finding", "path_exists", ["has_session"],
            None,
        ),
    ],
    "t1_group_membership": [
        (
            "Is {source_name} a member of any privileged groups? Show the membership path.",
            "path_finding", "path_exists", ["member_of", "privilege_escalation"],
            None,
        ),
        (
            "Find all users who are direct members of the Domain Admins group "
            "(not through nested group membership).",
            "enumeration", "node_set", ["member_of", "domain_admins"],
            # Direct MemberOf only — not transitive
            "MATCH (u:User)-[:MemberOf]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN u",
        ),
    ],
    "t2_kerberoast_chain": [
        (
            "Find all Kerberoastable service accounts (hasspn = true) and show their path "
            "to any server computer.",
            "path_finding", "path_exists", ["kerberoasting", "service_accounts"],
            None,
        ),
        (
            "List all user accounts that have a Service Principal Name (SPN) set, "
            "making them vulnerable to Kerberoasting.",
            "enumeration", "node_set", ["kerberoasting", "spn"],
            "MATCH (u:User {{hasspn: true}}) RETURN u",
        ),
    ],
    "t2_acl_chain": [
        (
            "Find users with GenericAll rights on any privileged group that has AdminTo "
            "access on servers.",
            "path_finding", "path_exists", ["acl", "generic_all", "admin_to"],
            None,
        ),
        (
            "Find all ACL-based attack paths where a regular user can reach server admin "
            "access through group rights abuse.",
            "path_finding", "path_exists", ["acl", "privilege_escalation"],
            None,
        ),
    ],
    "t2_nested_groups": [
        (
            "Find the full attack path from {source_name} to any server computer, "
            "including all nested group membership hops.",
            "path_finding", "path_exists", ["nested_groups", "member_of"],
            None,
        ),
        (
            "Show the complete group nesting chain that connects INFRA-TEAM@{domain} "
            "to Server-Admins.",
            "path_finding", "path_exists", ["nested_groups"],
            "MATCH p=(g:Group {{name: 'INFRA-TEAM@{domain}'}})-[:MemberOf*1..]->"
            "(sa:Group {{name: 'SERVER-ADMINS@{domain}'}}) RETURN p",
        ),
    ],
    "t3_unconstrained_delegation": [
        (
            "Find all non-domain-controller computers that have unconstrained Kerberos "
            "delegation enabled and also have active sessions from privileged users.",
            "path_finding", "path_exists", ["unconstrained_delegation", "kerberos"],
            None,
        ),
        (
            "Which computers have unconstrained delegation configured? Exclude domain controllers.",
            "enumeration", "node_set", ["unconstrained_delegation"],
            "MATCH (c:Computer {{unconstraineddelegation: true}}) WHERE NOT c.isdc = true RETURN c",
        ),
    ],
    "t3_constrained_delegation": [
        (
            "Find all service accounts configured with constrained delegation "
            "(trustedtoauth = true) that can delegate access to domain controllers.",
            "path_finding", "path_exists", ["constrained_delegation", "s4u2proxy"],
            None,
        ),
        (
            "List all accounts with the 'Trust this computer for delegation to specified "
            "services only' (trustedtoauth) flag set.",
            "enumeration", "node_set", ["constrained_delegation"],
            "MATCH (u:User {{trustedtoauth: true}}) RETURN u",
        ),
    ],
}

# Global enumeration tasks that don't depend on specific planted paths
_GLOBAL_TASKS: list[tuple[str, str, str, str, list[str]]] = [
    # (id_suffix, question, category, grade_mode, tags)
    (
        "global-kerberoastable",
        "List all user accounts with a Service Principal Name (SPN) set (hasspn = true).",
        "enumeration", "node_set", ["kerberoasting"],
    ),
    (
        "global-admin-to",
        "Find all computers where the Domain Admins group has AdminTo (local admin) access.",
        "enumeration", "node_set", ["admin_to", "domain_admins"],
    ),
    (
        "global-da-members",
        "List all users who are direct or transitive members of the Domain Admins group.",
        "enumeration", "node_set", ["domain_admins", "member_of"],
    ),
    (
        "global-unconstrained",
        "Find all computers with unconstrained Kerberos delegation enabled, "
        "excluding domain controllers.",
        "enumeration", "node_set", ["unconstrained_delegation"],
    ),
    (
        "global-privileged-sessions",
        "Find all computers that have active sessions from members of the Domain Admins group.",
        "enumeration", "node_set", ["has_session", "lateral_movement"],
    ),
]


def generate_tasks(manifest: dict) -> list[Task]:
    """Generate evaluation tasks from a manifest dict."""
    domain = manifest.get("domain", "CORP.LOCAL")
    tasks: list[Task] = []

    for path in manifest.get("planted_paths", []):
        tid = path["template_id"]
        source_name = path.get("source_name", path.get("source_node", ""))
        target_name = path.get("target_name", path.get("target_node", ""))

        for i, (q_tmpl, category, grade_mode, tags, ref_cypher_tmpl) in enumerate(
            _TEMPLATE_QUESTIONS.get(tid, []), start=1
        ):
            question = q_tmpl.format(
                source_name=source_name,
                target_name=target_name,
                domain=domain,
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
                reference_cypher = path["verification_cypher"]
            tasks.append(Task(
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
                    "domain": domain,
                    "description": path["description"],
                    "mitre": path.get("mitre", []),
                },
            ))

    # Global tasks use a placeholder reference Cypher (will be graded path_exists / node_set
    # against the actual BH CE results — reference Cypher runs live)
    _GLOBAL_REFERENCE_CYPHER = {
        "global-kerberoastable":
            "MATCH (u:User {hasspn: true}) RETURN u",
        "global-admin-to":
            f"MATCH (g:Group {{name: 'DOMAIN ADMINS@{domain}'}})-[:AdminTo]->(c:Computer) RETURN c",
        "global-da-members":
            f"MATCH (u:User)-[:MemberOf*1..]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN u",
        "global-unconstrained":
            "MATCH (c:Computer {unconstraineddelegation: true}) WHERE NOT c.isdc = true RETURN c",
        "global-privileged-sessions":
            f"MATCH (c:Computer)-[:HasSession]->(u:User)-[:MemberOf*1..]->(g:Group {{name: 'DOMAIN ADMINS@{domain}'}}) RETURN c",
    }

    for id_suffix, question, category, grade_mode, tags in _GLOBAL_TASKS:
        tasks.append(Task(
            id=id_suffix,
            template_id="global",
            tier=1,
            category=category,
            question=question.format(domain=domain),
            reference_cypher=_GLOBAL_REFERENCE_CYPHER[id_suffix],
            grade_mode=grade_mode,
            tags=tags,
            metadata={"domain": domain},
        ))

    return tasks
