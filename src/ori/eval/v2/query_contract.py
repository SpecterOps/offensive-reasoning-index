"""Public, model-neutral validation for query-backed proof contracts."""

from __future__ import annotations

import re
from typing import Literal

from .schema import EntityRef, TaskBundle

_IDENTIFIER = r"`?[A-Za-z_][A-Za-z0-9_]*`?"
_LITERAL = r"(?:'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")"
_NODE_PATTERN = re.compile(
    (
        r"\(\s*"
        rf"(?:(?P<variable>{_IDENTIFIER})\s*)?"
        rf"(?P<labels>(?:\s*:\s*{_IDENTIFIER})*)"
        r"(?P<properties>\s*\{[^{}]*\})?\s*\)"
    ),
    flags=re.IGNORECASE,
)
_SELECTOR_PREDICATE = re.compile(
    (
        r"\s*"
        r"(?:(?P<lhs_function>TOUPPER|TOLOWER)\s*\(\s*)?"
        rf"(?P<variable>{_IDENTIFIER})\s*\.\s*"
        r"`?(?P<property>objectid|name)`?"
        r"\s*(?(lhs_function)\))\s*=\s*"
        r"(?:(?P<rhs_function>TOUPPER)\s*\(\s*)?"
        rf"(?P<literal>{_LITERAL})"
        r"\s*(?(rhs_function)\))\s*"
    ),
    flags=re.IGNORECASE,
)
_RELATIONSHIP_BODY = re.compile(
    (
        r"\s*"
        rf"(?:(?P<variable>{_IDENTIFIER})\s*)?"
        rf"(?P<types>:\s*{_IDENTIFIER}"
        rf"(?:\s*\|\s*:?\s*{_IDENTIFIER})*)?"
        r"\s*"
        r"(?P<range>\*\s*(?:"
        r"(?P<exact>\d+)"
        r"|(?P<lower>\d+)?\s*\.\.\s*(?P<upper>\d+)"
        r"))?"
        r"\s*"
    ),
    flags=re.IGNORECASE,
)


def _identifier(value: str) -> str:
    return value.strip().strip("`")


def _literal(value: str) -> str:
    return value[1:-1].replace("\\'", "'").replace('\\"', '"')


def _strip_comments(query: str) -> str:
    """Remove Cypher comments without changing literals or identifier spelling."""

    output: list[str] = []
    index = 0
    while index < len(query):
        current = query[index]
        following = query[index + 1] if index + 1 < len(query) else ""
        if current in {"'", '"', "`"}:
            delimiter = current
            start = index
            index += 1
            while index < len(query):
                if query[index] == "\\" and delimiter != "`":
                    index = min(index + 2, len(query))
                    continue
                if query[index] == delimiter:
                    if index + 1 < len(query) and query[index + 1] == delimiter:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            output.append(query[start:index])
            continue
        if current == "/" and following == "/":
            output.append(" ")
            index += 2
            while index < len(query) and query[index] not in "\r\n":
                index += 1
            continue
        if current == "/" and following == "*":
            end = query.find("*/", index + 2)
            if end < 0:
                return query
            output.append(" ")
            index = end + 2
            continue
        output.append(current)
        index += 1
    return "".join(output)


def _selectors(entity: EntityRef, property_name: str) -> tuple[str, ...]:
    """Return values that the declared live graph property can actually equal."""

    if property_name == "objectid":
        return (entity.object_id,)
    if property_name == "name" and entity.canonical_name:
        return (entity.canonical_name,)
    return ()


def _matches_selector(
    literal: str,
    selectors: tuple[str, ...],
    *,
    lhs_function: str = "",
    rhs_function: str = "",
) -> bool:
    actual = _literal(literal)
    for selector in selectors:
        expected = selector
        if lhs_function.casefold() == "toupper":
            expected = expected.upper()
        elif lhs_function.casefold() == "tolower":
            expected = expected.lower()
        candidate = actual.upper() if rhs_function.casefold() == "toupper" else actual
        if expected == candidate:
            return True
    return False


def _strip_outer_grouping(value: str) -> str:
    """Remove only parentheses that enclose the complete expression."""

    text = value.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        delimiter: str | None = None
        wraps_complete_expression = True
        index = 0
        while index < len(text):
            current = text[index]
            if delimiter is not None:
                if current == "\\" and delimiter != "`":
                    index += 2
                    continue
                if current == delimiter:
                    if index + 1 < len(text) and text[index + 1] == delimiter:
                        index += 2
                        continue
                    delimiter = None
                index += 1
                continue
            if current in {"'", '"', "`"}:
                delimiter = current
            elif current == "(":
                depth += 1
            elif current == ")":
                depth -= 1
                if depth < 0:
                    return text
                if depth == 0 and index != len(text) - 1:
                    wraps_complete_expression = False
                    break
            index += 1
        if not wraps_complete_expression or depth != 0 or delimiter is not None:
            break
        text = text[1:-1].strip()
    return text


def _split_top_level_and(value: str) -> tuple[str, ...] | None:
    """Split an expression on ungrouped ``AND`` keywords."""

    terms: list[str] = []
    depth = 0
    delimiter: str | None = None
    start = 0
    index = 0
    while index < len(value):
        current = value[index]
        if delimiter is not None:
            if current == "\\" and delimiter != "`":
                index += 2
                continue
            if current == delimiter:
                if index + 1 < len(value) and value[index + 1] == delimiter:
                    index += 2
                    continue
                delimiter = None
            index += 1
            continue
        if current in {"'", '"', "`"}:
            delimiter = current
            index += 1
            continue
        if current == "(":
            depth += 1
            index += 1
            continue
        if current == ")":
            depth -= 1
            if depth < 0:
                return None
            index += 1
            continue
        keyword = value[index : index + 3]
        before = value[index - 1] if index else ""
        after = value[index + 3] if index + 3 < len(value) else ""
        if (
            depth == 0
            and keyword.casefold() == "and"
            and (not before or not (before.isalnum() or before == "_"))
            and (not after or not (after.isalnum() or after == "_"))
        ):
            term = value[start:index].strip()
            if not term:
                return None
            terms.append(term)
            start = index + 3
            index += 3
            continue
        index += 1
    if depth != 0 or delimiter is not None:
        return None
    final = value[start:].strip()
    if not final:
        return None
    terms.append(final)
    return tuple(terms)


def _map_has_only_role_selectors(
    properties: str | None,
    entity: EntityRef,
) -> bool:
    if not properties:
        return False
    body = properties.strip()[1:-1].strip()
    entries = tuple(part.strip() for part in body.split(",") if part.strip())
    if not entries:
        return False
    for entry in entries:
        match = re.fullmatch(
            (
                r"`?(?P<property>objectid|name)`?\s*:\s*"
                rf"(?P<literal>{_LITERAL})"
            ),
            entry,
            flags=re.IGNORECASE,
        )
        if (
            match is None
            or match.group("property") not in {"objectid", "name"}
            or not _matches_selector(
                match.group("literal"),
                _selectors(entity, match.group("property")),
            )
        ):
            return False
    return True


def _where_selector_variables(
    where_body: str,
    *,
    source: EntityRef,
    target: EntityRef,
) -> dict[str, str] | None:
    """Accept only exact public endpoint selectors joined by ``AND``."""

    where_body = _strip_outer_grouping(where_body)
    if re.search(r"\b(?:OR|NOT|XOR)\b", where_body, flags=re.IGNORECASE):
        return None
    roles: dict[str, str] = {}
    terms: list[str] = []
    pending = [where_body]
    while pending:
        candidate = _strip_outer_grouping(pending.pop(0))
        split = _split_top_level_and(candidate)
        if split is None:
            return None
        if len(split) > 1:
            pending[0:0] = split
        else:
            terms.append(candidate)
    for term in terms:
        match = _SELECTOR_PREDICATE.fullmatch(term)
        if match is None:
            return None
        if match.group("property") not in {"objectid", "name"}:
            return None
        variable = _identifier(match.group("variable"))
        matches_source = _matches_selector(
            match.group("literal"),
            _selectors(source, match.group("property")),
            lhs_function=match.group("lhs_function") or "",
            rhs_function=match.group("rhs_function") or "",
        )
        matches_target = _matches_selector(
            match.group("literal"),
            _selectors(target, match.group("property")),
            lhs_function=match.group("lhs_function") or "",
            rhs_function=match.group("rhs_function") or "",
        )
        role = "source" if matches_source else "target" if matches_target else None
        if role is None or (variable in roles and roles[variable] != role):
            return None
        roles[variable] = role
    return roles


def _endpoint_matches(
    node: re.Match[str],
    *,
    entity: EntityRef,
    role: str,
    where_roles: dict[str, str],
) -> bool:
    labels = {
        _identifier(label)
        for label in re.findall(
            rf":\s*(?P<label>{_IDENTIFIER})",
            node.group("labels"),
            flags=re.IGNORECASE,
        )
    }
    if labels and labels != {entity.object_type}:
        return False
    properties = node.group("properties")
    if properties:
        if not _map_has_only_role_selectors(properties, entity):
            return False
        variable = node.group("variable")
        if variable is None:
            return not where_roles
        where_role = where_roles.get(_identifier(variable))
        return where_role is None or where_role == role
    variable = node.group("variable")
    return variable is not None and where_roles.get(_identifier(variable)) == role


def _prefix_endpoint_roles(
    prefix: str,
    *,
    source: EntityRef,
    target: EntityRef,
) -> dict[str, str] | None:
    """Resolve exact endpoint-only ``MATCH`` declarations before the path."""

    if not prefix.strip():
        return {}
    nodes = tuple(_NODE_PATTERN.finditer(prefix))
    if not nodes:
        return None
    residual_parts: list[str] = []
    cursor = 0
    for node in nodes:
        residual_parts.append(prefix[cursor : node.start()])
        cursor = node.end()
    residual_parts.append(prefix[cursor:])
    if (
        re.fullmatch(
            r"(?:\s|,|\bMATCH\b)*",
            "".join(residual_parts),
            flags=re.IGNORECASE,
        )
        is None
    ):
        return None

    roles: dict[str, str] = {}
    seen_roles: set[str] = set()
    for node in nodes:
        variable = node.group("variable")
        if variable is None:
            return None
        matches_source = _endpoint_matches(
            node,
            entity=source,
            role="source",
            where_roles={},
        )
        matches_target = _endpoint_matches(
            node,
            entity=target,
            role="target",
            where_roles={},
        )
        if matches_source == matches_target:
            return None
        role = "source" if matches_source else "target"
        normalized_variable = _identifier(variable)
        if normalized_variable in roles or role in seen_roles:
            return None
        roles[normalized_variable] = role
        seen_roles.add(role)
    return roles


def negative_query_scope_mode(
    task: TaskBundle,
    query: str,
) -> Literal["exact", "broader"] | None:
    """Validate a complete bounded source-to-objective zero-count proof."""

    acceptance = task.acceptance_spec
    if (
        task.claim_kind != "absence"
        or acceptance.source_role is None
        or acceptance.target_role is None
    ):
        return None
    entities_by_role = {entity.role: entity for entity in task.input_entities}
    source = entities_by_role.get(acceptance.source_role)
    target = entities_by_role.get(acceptance.target_role)
    if source is None or target is None:
        return None

    normalized = " ".join(_strip_comments(query).split())
    assignment = re.search(
        rf"\b(?P<optional>OPTIONAL\s+)?MATCH\s+(?P<path>{_IDENTIFIER})\s*=\s*",
        normalized,
        flags=re.IGNORECASE,
    )
    if assignment is None:
        return None
    prefix_roles = _prefix_endpoint_roles(
        normalized[: assignment.start()],
        source=source,
        target=target,
    )
    if prefix_roles is None:
        return None
    if assignment.group("optional") and set(prefix_roles.values()) != {
        "source",
        "target",
    }:
        # OPTIONAL MATCH is the standard zero-preserving form, but it is safe
        # proof only after exact singleton endpoints have already been bound.
        return None
    path_variable = _identifier(assignment.group("path"))
    boundary = re.search(
        r"\b(?:OPTIONAL\s+MATCH|MATCH|WHERE|WITH|RETURN)\b",
        normalized[assignment.end() :],
        flags=re.IGNORECASE,
    )
    if boundary is None:
        return None
    traversal_end = assignment.end() + boundary.start()
    traversal = normalized[assignment.end() : traversal_end]
    boundary_kind = boundary.group(0).casefold()

    nodes = tuple(_NODE_PATTERN.finditer(traversal))
    relationships = tuple(re.finditer(r"\[(?P<body>[^\]]*)\]", traversal))
    if len(nodes) != 2 or len(relationships) > 1:
        return None
    if (
        traversal[: nodes[0].start()].strip()
        or traversal[nodes[1].end() :].strip()
    ):
        return None
    if relationships:
        relationship = relationships[0]
        if not (
            nodes[0].end()
            <= relationship.start()
            <= relationship.end()
            <= nodes[1].start()
        ):
            return None
        left_connector = traversal[nodes[0].end() : relationship.start()]
        right_connector = traversal[relationship.end() : nodes[1].start()]
        if re.fullmatch(r"\s*-\s*", left_connector) is None:
            return None
        right_match = re.fullmatch(
            r"\s*(?P<direction>->|-)\s*",
            right_connector,
        )
        relationship_body = relationship.group("body")
    else:
        right_match = re.fullmatch(
            r"\s*-\s*(?P<direction>->|-)\s*",
            traversal[nodes[0].end() : nodes[1].start()],
        )
        relationship_body = ""
    if right_match is None:
        return None
    outbound = right_match.group("direction") == "->"

    suffix = normalized[traversal_end:]
    where_roles: dict[str, str] = {}
    if boundary_kind == "where":
        where_match = re.fullmatch(
            r"\s*WHERE\s+(?P<body>.*?)\s+RETURN\s+(?P<return>.*)",
            suffix,
            flags=re.IGNORECASE,
        )
        if where_match is None:
            return None
        resolved = _where_selector_variables(
            where_match.group("body"),
            source=source,
            target=target,
        )
        if resolved is None:
            return None
        where_roles = resolved
        endpoint_variables = {
            _identifier(variable)
            for node in nodes
            if (variable := node.group("variable")) is not None
        }
        if not set(where_roles).issubset(endpoint_variables):
            return None
        return_body = where_match.group("return")
    elif boundary_kind == "return":
        return_match = re.fullmatch(
            r"\s*RETURN\s+(?P<return>.*)",
            suffix,
            flags=re.IGNORECASE,
        )
        if return_match is None:
            return None
        return_body = return_match.group("return")
    else:
        return None

    endpoint_variables = {
        _identifier(variable)
        for node in nodes
        if (variable := node.group("variable")) is not None
    }
    if not set(prefix_roles).issubset(endpoint_variables):
        return None
    combined_roles = dict(prefix_roles)
    for variable, role in where_roles.items():
        if variable in combined_roles and combined_roles[variable] != role:
            return None
        combined_roles[variable] = role

    if not _endpoint_matches(
        nodes[0],
        entity=source,
        role="source",
        where_roles=combined_roles,
    ) or not _endpoint_matches(
        nodes[1],
        entity=target,
        role="target",
        where_roles=combined_roles,
    ):
        return None
    count_match = re.fullmatch(
        (
            r"\s*COUNT\s*\(\s*(?P<target>"
            rf"(?:DISTINCT\s+)?{_IDENTIFIER}"
            r"|\*)\s*\)"
            r"\s*(?:AS\s+`?[A-Za-z_][A-Za-z0-9_]*`?)?"
            r"\s*(?:LIMIT\s+1)?\s*;?\s*"
        ),
        return_body,
        flags=re.IGNORECASE,
    )
    if count_match is None:
        return None
    count_target = re.sub(
        r"^\s*DISTINCT\s+",
        "",
        count_match.group("target"),
        flags=re.IGNORECASE,
    )
    if count_target != "*" and _identifier(count_target) != path_variable:
        return None

    body = relationship_body
    body_match = _RELATIONSHIP_BODY.fullmatch(body)
    if body_match is None:
        return None
    if body_match.group("range") is None:
        lower_hops = upper_hops = 1
    elif body_match.group("exact") is not None:
        lower_hops = upper_hops = int(body_match.group("exact"))
    else:
        lower_hops = int(body_match.group("lower") or 1)
        upper_hops = int(body_match.group("upper"))
    if lower_hops > 1 or upper_hops < task.binding.bounds.max_hops:
        return None
    relationship_types = [
        match.group("kind")
        for match in re.finditer(
            r"(?::|\|)\s*`?(?P<kind>[A-Za-z_][A-Za-z0-9_]*)`?",
            body_match.group("types") or "",
            flags=re.IGNORECASE,
        )
    ]
    if relationship_types:
        allowed = set(acceptance.allowed_mechanisms)
        if not allowed or set(relationship_types) != allowed:
            return None
        return (
            "exact"
            if (
                outbound
                and lower_hops == 1
                and upper_hops == task.binding.bounds.max_hops
            )
            else "broader"
        )
    return "broader"
