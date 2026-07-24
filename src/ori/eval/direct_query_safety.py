"""Model-blind containment for untrusted direct-Cypher queries.

The policy is intentionally applied after model generation and before BloodHound
execution. It never rewrites the model query or asks the model to repair it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from .bhce import BHCEClient, CypherResult


DIRECT_QUERY_POLICY_VERSION = "bloodhound-cysql-direct-v2"


@dataclass(frozen=True)
class DirectQuerySafetyConfig:
    """Versioned safety contract for official direct-Cypher evaluation."""

    enabled: bool = True
    policy_version: str = DIRECT_QUERY_POLICY_VERSION
    server_timeout_seconds: float = 10.0
    client_timeout_seconds: float = 15.0
    max_recursive_hops: int = 12
    max_result_rows: int = 1000
    max_query_characters: int = 16_384
    max_recursive_patterns: int = 2

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> DirectQuerySafetyConfig:
        raw = dict(value or {})
        supported = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(raw) - supported)
        if unknown:
            raise ValueError(
                "Unsupported direct_query_safety setting(s): " + ", ".join(unknown)
            )
        config = cls(**raw)
        if config.policy_version != DIRECT_QUERY_POLICY_VERSION:
            raise ValueError(
                f"Unsupported direct query policy {config.policy_version!r}; "
                f"expected {DIRECT_QUERY_POLICY_VERSION!r}."
            )
        if config.server_timeout_seconds <= 0:
            raise ValueError("direct_query_safety.server_timeout_seconds must be positive.")
        if config.client_timeout_seconds <= config.server_timeout_seconds:
            raise ValueError(
                "direct_query_safety.client_timeout_seconds must be greater than "
                "server_timeout_seconds so BloodHound can terminate the query first."
            )
        for field_name in (
            "max_recursive_hops",
            "max_result_rows",
            "max_query_characters",
            "max_recursive_patterns",
        ):
            if getattr(config, field_name) < 1:
                raise ValueError(f"direct_query_safety.{field_name} must be at least 1.")
        return config

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QuerySafetyDecision:
    allowed: bool
    rule: str
    detail: str
    fingerprint: str


_FINGERPRINT_KEYWORDS = frozenset(
    {
        "ALL",
        "ALLSHORTESTPATHS",
        "AND",
        "ANY",
        "AS",
        "ASC",
        "BY",
        "CALL",
        "CASE",
        "COLLECT",
        "CONTAINS",
        "COUNT",
        "CREATE",
        "DELETE",
        "DESC",
        "DETACH",
        "DISTINCT",
        "DROP",
        "ELSE",
        "END",
        "ENDS",
        "EXISTS",
        "FALSE",
        "FOREACH",
        "IN",
        "LIMIT",
        "LOAD",
        "MATCH",
        "MERGE",
        "NONE",
        "NOT",
        "NULL",
        "OPTIONAL",
        "OR",
        "ORDER",
        "REMOVE",
        "RETURN",
        "SET",
        "SHORTESTPATH",
        "SINGLE",
        "SKIP",
        "STARTS",
        "THEN",
        "TRUE",
        "UNION",
        "UNWIND",
        "WHEN",
        "WHERE",
        "WITH",
        "XOR",
    }
)
_TWO_CHARACTER_TOKENS = frozenset(
    {"->", "<-", "<=", ">=", "<>", "=~", "..", "+=", "-=", "*=", "/="}
)


def normalize_query_for_fingerprint(query: str) -> str:
    """Canonicalize token-equivalent CySQL without changing identifiers or literals.

    Comments and formatting are discarded and known Cypher keywords are
    case-normalized. Quoted strings, backtick identifiers, and ordinary
    identifiers retain their exact spelling so case-sensitive BloodHound names
    and semantically distinct identifiers cannot collide.
    """

    tokens: list[str] = []
    index = 0
    while index < len(query):
        current = query[index]
        following = query[index + 1] if index + 1 < len(query) else ""
        if current.isspace():
            index += 1
            continue
        if current == "/" and following == "/":
            index += 2
            while index < len(query) and query[index] not in "\r\n":
                index += 1
            continue
        if current == "/" and following == "*":
            index += 2
            while index + 1 < len(query):
                if query[index : index + 2] == "*/":
                    index += 2
                    break
                index += 1
            else:
                index = len(query)
            continue
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
            tokens.append(query[start:index])
            continue
        if current.isalpha() or current == "_":
            start = index
            index += 1
            while index < len(query) and (
                query[index].isalnum() or query[index] == "_"
            ):
                index += 1
            identifier = query[start:index]
            upper = identifier.upper()
            tokens.append(upper if upper in _FINGERPRINT_KEYWORDS else identifier)
            continue
        two_characters = query[index : index + 2]
        if two_characters in _TWO_CHARACTER_TOKENS:
            tokens.append(two_characters)
            index += 2
            continue
        tokens.append(current)
        index += 1
    return "\x1f".join(tokens)


def query_fingerprint(query: str) -> str:
    normalized = normalize_query_for_fingerprint(query)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _mask_literals_and_comments(query: str) -> str:
    """Blank literal/comment contents while retaining syntax positions and quotes."""

    chars = list(query)
    index = 0
    length = len(chars)
    while index < length:
        current = chars[index]
        following = chars[index + 1] if index + 1 < length else ""
        if current == "/" and following == "/":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < length and chars[index] not in "\r\n":
                chars[index] = " "
                index += 1
            continue
        if current == "/" and following == "*":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < length:
                if chars[index] == "*" and index + 1 < length and chars[index + 1] == "/":
                    chars[index] = chars[index + 1] = " "
                    index += 2
                    break
                chars[index] = " "
                index += 1
            continue
        if current in {"'", '"', "`"}:
            delimiter = current
            index += 1
            while index < length:
                if chars[index] == "\\" and delimiter != "`":
                    chars[index] = " "
                    if index + 1 < length:
                        chars[index + 1] = " "
                    index += 2
                    continue
                if chars[index] == delimiter:
                    if index + 1 < length and chars[index + 1] == delimiter:
                        chars[index] = chars[index + 1] = " "
                        index += 2
                        continue
                    break
                chars[index] = " "
                index += 1
            index += 1
            continue
        index += 1
    return "".join(chars)


def _function_ranges(masked_query: str, function_name: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    pattern = re.compile(rf"\b{re.escape(function_name)}\s*\(", re.IGNORECASE)
    for match in pattern.finditer(masked_query):
        open_index = masked_query.find("(", match.start(), match.end())
        depth = 0
        for index in range(open_index, len(masked_query)):
            if masked_query[index] == "(":
                depth += 1
            elif masked_query[index] == ")":
                depth -= 1
                if depth == 0:
                    ranges.append((match.start(), index + 1))
                    break
    return ranges


def _inside(span: tuple[int, int], ranges: list[tuple[int, int]]) -> bool:
    return any(start <= span[0] and span[1] <= end for start, end in ranges)


_EXACT_SELECTOR = re.compile(
    r"\b(?:name|objectid)\s*(?:=|:)\s*['\"]",
    re.IGNORECASE,
)
_INLINE_EXACT_BINDING = re.compile(
    r"\(\s*([A-Za-z_]\w*)[^()]*\b(?:name|objectid)\s*:\s*['\"]",
    re.IGNORECASE,
)
_INLINE_FILTER_BINDING = re.compile(
    r"\(\s*([A-Za-z_]\w*)[^()]*\{",
    re.IGNORECASE,
)
_WHERE_SCALAR_FILTER_BINDING = re.compile(
    r"\b([A-Za-z_]\w*)\s*\.\s*[A-Za-z_]\w*\s*"
    r"(?:=~|<>|<=|>=|=|<|>|STARTS\s+WITH|ENDS\s+WITH|CONTAINS|IN)\s*"
    r"(?:['\"]|TRUE\b|FALSE\b|NULL\b|-?\d|\[)",
    re.IGNORECASE,
)
_WHERE_BOUNDARY = re.compile(
    r"\b(?:WITH|RETURN|UNWIND|OPTIONAL\s+MATCH|MATCH|ORDER\s+BY|SKIP|LIMIT)\b",
    re.IGNORECASE,
)
_PROPERTY_EXACT_BINDING = re.compile(
    r"\b([A-Za-z_]\w*)\s*\.\s*(?:name|objectid)\s*=\s*['\"]",
    re.IGNORECASE,
)
_COALESCE_EXACT_BINDING = re.compile(
    r"\bcoalesce\s*\(\s*([A-Za-z_]\w*)\s*\.\s*(?:name|objectid)\b[^)]*\)"
    r"\s*=\s*['\"]",
    re.IGNORECASE,
)
_RELATIONSHIP_SEGMENT = re.compile(
    r"\)\s*(?:-\s*(?:\[[^\]]*\])?\s*->|<-\s*(?:\[[^\]]*\])?\s*-|"
    r"-\s*(?:\[[^\]]*\])?\s*-)\s*\(",
    re.DOTALL,
)
_MATCH_CLAUSE = re.compile(
    r"\b(?:OPTIONAL\s+)?MATCH\b(?P<body>.*?)"
    r"(?=\b(?:WHERE|WITH|RETURN|UNWIND|OPTIONAL\s+MATCH|MATCH)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_NODE_PATTERN = re.compile(
    r"\(\s*(?P<variable>[A-Za-z_]\w*)?\s*"
    r"(?::\s*(?:`[^`]+`|[A-Za-z_]\w*)"
    r"(?:\s*\|\s*:?\s*(?:`[^`]+`|[A-Za-z_]\w*))*)?\s*"
    r"(?:\{[^()]*\})?\s*\)",
    re.DOTALL,
)


def _where_clause_bodies(masked_query: str) -> list[str]:
    bodies: list[str] = []
    for where in re.finditer(r"\bWHERE\b", masked_query, re.IGNORECASE):
        end = len(masked_query)
        for boundary in _WHERE_BOUNDARY.finditer(masked_query, where.end()):
            if boundary.group(0).upper() == "WITH":
                prefix_words = re.findall(
                    r"[A-Za-z_]+", masked_query[where.end() : boundary.start()]
                )
                if prefix_words and prefix_words[-1].upper() in {"STARTS", "ENDS"}:
                    continue
            end = boundary.start()
            break
        bodies.append(masked_query[where.end() : end])
    return bodies


def _exact_selector_bindings(masked_query: str) -> set[str]:
    """Return variables anchored in MATCH node maps or WHERE predicates.

    Property comparisons in RETURN projections are output expressions, not
    predicates, and must never make an otherwise broad traversal selective.
    """

    bindings: set[str] = set()
    for clause in _MATCH_CLAUSE.finditer(masked_query):
        bindings.update(
            match.group(1)
            for match in _INLINE_EXACT_BINDING.finditer(clause.group("body"))
        )
    for body in _where_clause_bodies(masked_query):
        for pattern in (_PROPERTY_EXACT_BINDING, _COALESCE_EXACT_BINDING):
            bindings.update(match.group(1) for match in pattern.finditer(body))
    return bindings


def _standalone_filter_bindings(masked_query: str) -> set[str]:
    """Return variables narrowed by inline or scalar property predicates."""

    inline_bindings: set[str] = set()
    for clause in _MATCH_CLAUSE.finditer(masked_query):
        inline_bindings.update(
            match.group(1)
            for match in _INLINE_FILTER_BINDING.finditer(clause.group("body"))
        )
    where_bindings: set[str] = set()
    for body in _where_clause_bodies(masked_query):
        where_bindings.update(
            match.group(1)
            for match in _WHERE_SCALAR_FILTER_BINDING.finditer(body)
        )
    return inline_bindings | where_bindings


def _query_stages(masked_query: str) -> list[str]:
    """Split query work at WITH scope boundaries.

    A selector in a stage before WITH must not authorize a later MATCH after
    that variable has been projected away. STARTS WITH and ENDS WITH are
    predicates, not scope boundaries.
    """

    boundaries = [0]
    for match in re.finditer(r"\bWITH\b", masked_query, re.IGNORECASE):
        prefix_words = re.findall(r"[A-Za-z_]+", masked_query[: match.start()])
        if prefix_words and prefix_words[-1].upper() in {"STARTS", "ENDS"}:
            continue
        boundaries.append(match.start())
    boundaries.append(len(masked_query))
    return [
        masked_query[start:end]
        for start, end in zip(boundaries, boundaries[1:])
        if masked_query[start:end].strip()
    ]


def _projected_bindings(stage: str, prior_bindings: set[str]) -> set[str]:
    """Carry only explicitly projected bindings into a WITH stage."""

    with_match = re.match(r"\s*WITH\b", stage, re.IGNORECASE)
    if not with_match:
        return set()
    tail = stage[with_match.end() :]
    boundary = re.search(
        r"\b(?:WHERE|ORDER\s+BY|SKIP|LIMIT|OPTIONAL\s+MATCH|MATCH|UNWIND|RETURN)\b",
        tail,
        re.IGNORECASE,
    )
    projection = tail[: boundary.start()] if boundary else tail
    projection = re.sub(r"^\s*DISTINCT\b", "", projection, flags=re.IGNORECASE)
    carried = set(prior_bindings) if re.search(r"(?:^|,)\s*\*\s*(?:,|$)", projection) else set()
    for item in projection.split(","):
        simple = re.fullmatch(
            r"\s*([A-Za-z_]\w*)(?:\s+AS\s+([A-Za-z_]\w*))?\s*",
            item,
            re.IGNORECASE,
        )
        if not simple or simple.group(1) not in prior_bindings:
            continue
        carried.add(simple.group(2) or simple.group(1))
    return carried


def _node_context_before(masked_query: str, position: int) -> tuple[str | None, bool]:
    open_index = masked_query.rfind("(", 0, position)
    if open_index < 0:
        return None, False
    body = masked_query[open_index + 1 : position]
    variable = re.match(r"\s*([A-Za-z_]\w*)", body)
    binding = variable.group(1) if variable else f"@node:{open_index}"
    return binding, bool(_EXACT_SELECTOR.search(body))


def _node_context_after(masked_query: str, position: int) -> tuple[str | None, bool]:
    open_index = masked_query.find("(", position)
    if open_index < 0:
        return None, False
    close_index = masked_query.find(")", open_index + 1)
    if close_index < 0:
        close_index = len(masked_query)
    body = masked_query[open_index + 1 : close_index]
    variable = re.match(r"\s*([A-Za-z_]\w*)", body)
    binding = variable.group(1) if variable else f"@node:{open_index}"
    return binding, bool(_EXACT_SELECTOR.search(body))


def _relationship_contexts(masked_query: str) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for relationship in _RELATIONSHIP_SEGMENT.finditer(masked_query):
        left_variable, left_inline_selector = _node_context_before(
            masked_query, relationship.start()
        )
        right_variable, right_inline_selector = _node_context_after(
            masked_query, relationship.end() - 1
        )
        contexts.append(
            {
                "endpoint_variables": {
                    variable
                    for variable in (left_variable, right_variable)
                    if variable is not None
                },
                "inline_selector": left_inline_selector or right_inline_selector,
            }
        )
    return contexts


def _standalone_node_bindings(masked_query: str) -> set[str]:
    """Return node patterns that are not endpoints in a relationship pattern."""

    relationship_node_openings: set[int] = set()
    for relationship in _RELATIONSHIP_SEGMENT.finditer(masked_query):
        left_opening = masked_query.rfind("(", 0, relationship.start())
        right_opening = masked_query.find("(", relationship.end() - 1)
        if left_opening >= 0:
            relationship_node_openings.add(left_opening)
        if right_opening >= 0:
            relationship_node_openings.add(right_opening)

    bindings: set[str] = set()
    for clause in _MATCH_CLAUSE.finditer(masked_query):
        body_start = clause.start("body")
        for node in _NODE_PATTERN.finditer(clause.group("body")):
            opening = body_start + node.start()
            if opening in relationship_node_openings:
                continue
            variable = node.group("variable")
            bindings.add(variable if variable else f"@node:{opening}")
    return bindings


def _all_relationship_components_are_selective(
    contexts: list[dict[str, Any]],
    filter_bindings: set[str],
) -> bool:
    """Require every disconnected relationship component to have a selector.

    Selectivity propagates through variables shared by adjacent relationship
    patterns, so a multi-hop path needs only one bound node. It does not
    propagate to a separate comma-separated pattern.
    """

    remaining = list(contexts)
    selected_variables = set(filter_bindings)
    while remaining:
        next_remaining: list[dict[str, Any]] = []
        made_progress = False
        for context in remaining:
            endpoint_variables = context["endpoint_variables"]
            if context["inline_selector"] or endpoint_variables & selected_variables:
                selected_variables.update(endpoint_variables)
                made_progress = True
            else:
                next_remaining.append(context)
        if not made_progress:
            return False
        remaining = next_remaining
    return True


def _recursive_expansions(masked_query: str) -> list[dict[str, Any]]:
    shortest_ranges = _function_ranges(masked_query, "shortestPath")
    all_shortest_ranges = _function_ranges(masked_query, "allShortestPaths")
    expansions: list[dict[str, Any]] = []
    relationship_pattern = re.compile(r"\[[^\]]*\]", re.DOTALL)
    quantifier_pattern = re.compile(r"\*(?:(\d*)\s*\.\.\s*(\d*)|(\d+))?")
    for relationship in relationship_pattern.finditer(masked_query):
        quantifier = quantifier_pattern.search(relationship.group(0))
        if not quantifier:
            continue
        lower_text, upper_text, exact_text = quantifier.groups()
        if exact_text is not None:
            lower = upper = int(exact_text)
        elif lower_text is None and upper_text is None:
            lower = 1
            upper = None
        else:
            lower = int(lower_text) if lower_text else 1
            upper = int(upper_text) if upper_text else None
        relationship_text = relationship.group(0)
        before_star = relationship_text[1 : quantifier.start()]
        typed = ":" in before_star
        span = relationship.span()
        left_variable, left_inline_selector = _node_context_before(
            masked_query, span[0]
        )
        right_variable, right_inline_selector = _node_context_after(
            masked_query, span[1]
        )
        expansions.append(
            {
                "lower": lower,
                "upper": upper,
                "typed": typed,
                "shortest": _inside(span, shortest_ranges),
                "all_shortest": _inside(span, all_shortest_ranges),
                "endpoint_variables": {
                    variable
                    for variable in (left_variable, right_variable)
                    if variable is not None
                },
                "inline_selector": left_inline_selector or right_inline_selector,
            }
        )
    return expansions


class DirectQueryPolicy:
    """Conservative checks derived from BloodHound's documented CySQL surface."""

    _MUTATION_OR_EXTENSION = re.compile(
        r"\b(?:CREATE|MERGE|SET|REMOVE|DELETE|DETACH|DROP|FOREACH|LOAD\s+CSV|CALL)\b",
        re.IGNORECASE,
    )
    _UNSUPPORTED_SET_OPERATION = re.compile(r"\bUNION(?:\s+ALL)?\b", re.IGNORECASE)
    _LIMIT = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
    _RETURN_COUNT_ONLY = re.compile(
        r"\bRETURN\s+(?:DISTINCT\s+)?COUNT\s*\([^)]*\)"
        r"\s*(?:AS\s+[A-Za-z_]\w*)?\s*;?\s*\Z",
        re.IGNORECASE,
    )

    def __init__(self, config: DirectQuerySafetyConfig) -> None:
        self.config = config

    def evaluate(self, query: str) -> QuerySafetyDecision:
        fingerprint = query_fingerprint(query)
        if not self.config.enabled:
            return QuerySafetyDecision(True, "policy_disabled", "policy disabled", fingerprint)
        if not query.strip():
            return QuerySafetyDecision(False, "empty_query", "query is empty", fingerprint)
        if len(query) > self.config.max_query_characters:
            return QuerySafetyDecision(
                False,
                "query_too_large",
                f"query has {len(query)} characters; limit is {self.config.max_query_characters}",
                fingerprint,
            )

        masked = _mask_literals_and_comments(query)
        statements = [item.strip() for item in masked.split(";") if item.strip()]
        if len(statements) > 1:
            return QuerySafetyDecision(
                False,
                "multiple_statements",
                "only one read-only CySQL statement is allowed",
                fingerprint,
            )
        mutation = self._MUTATION_OR_EXTENSION.search(masked)
        if mutation:
            return QuerySafetyDecision(
                False,
                "non_read_only_query",
                f"read-only policy rejected clause {mutation.group(0).upper()}",
                fingerprint,
            )
        if self._UNSUPPORTED_SET_OPERATION.search(masked):
            return QuerySafetyDecision(
                False,
                "unsupported_set_operation",
                "UNION is outside the documented BloodHound direct-query policy",
                fingerprint,
            )

        limit_matches = list(self._LIMIT.finditer(masked))
        limits = [int(match.group(1)) for match in limit_matches]
        if limits and max(limits) > self.config.max_result_rows:
            return QuerySafetyDecision(
                False,
                "result_limit_too_large",
                f"LIMIT {max(limits)} exceeds {self.config.max_result_rows}",
                fingerprint,
            )
        stages = _query_stages(masked)
        expansions = [
            expansion
            for stage in stages
            for expansion in _recursive_expansions(stage)
        ]
        if len(expansions) > self.config.max_recursive_patterns:
            return QuerySafetyDecision(
                False,
                "too_many_recursive_patterns",
                f"query has {len(expansions)} recursive patterns; "
                f"limit is {self.config.max_recursive_patterns}",
                fingerprint,
            )

        prior_selector_bindings: set[str] = set()
        prior_filter_bindings: set[str] = set()
        for stage in stages:
            stage_limit_matches = list(
                re.finditer(r"\bLIMIT\s+(\d+)\b", stage, re.IGNORECASE)
            )
            stage_return_matches = list(
                re.finditer(r"\bRETURN\b", stage, re.IGNORECASE)
            )
            has_result_limit = bool(
                stage_return_matches
                and any(
                    match.start() > stage_return_matches[-1].start()
                    for match in stage_limit_matches
                )
            )
            returns_count_only = bool(self._RETURN_COUNT_ONLY.search(stage))
            selector_bindings = _exact_selector_bindings(stage) | _projected_bindings(
                stage, prior_selector_bindings
            )
            standalone_filter_bindings = (
                selector_bindings
                | _standalone_filter_bindings(stage)
                | _projected_bindings(stage, prior_filter_bindings)
            )
            stage_expansions = _recursive_expansions(stage)
            for expansion in stage_expansions:
                upper = expansion["upper"]
                expansion_is_selective = bool(
                    expansion["inline_selector"]
                    or expansion["endpoint_variables"] & selector_bindings
                )
                if expansion["all_shortest"] and upper is None:
                    return QuerySafetyDecision(
                        False,
                        "unbounded_all_shortest_paths",
                        "allShortestPaths requires a finite upper hop bound",
                        fingerprint,
                    )
                if (
                    expansion["shortest"]
                    and upper is None
                    and not expansion_is_selective
                ):
                    return QuerySafetyDecision(
                        False,
                        "unselective_shortest_path",
                        "open-ended shortestPath requires an exact endpoint selector",
                        fingerprint,
                    )
                if not expansion["shortest"] and not expansion["typed"] and upper is None:
                    return QuerySafetyDecision(
                        False,
                        "unbounded_wildcard_path_enumeration",
                        "raw wildcard recursive expansion requires a finite upper hop bound",
                        fingerprint,
                    )
                if upper is not None and upper > self.config.max_recursive_hops:
                    return QuerySafetyDecision(
                        False,
                        "recursive_hop_limit_exceeded",
                        f"recursive upper bound {upper} exceeds "
                        f"{self.config.max_recursive_hops}",
                        fingerprint,
                    )
                if not expansion_is_selective:
                    return QuerySafetyDecision(
                        False,
                        "unselective_recursive_expansion",
                        "recursive expansion requires an exact selector on a path endpoint",
                        fingerprint,
                    )

            relationship_contexts = _relationship_contexts(stage)
            relationship_is_selective = _all_relationship_components_are_selective(
                relationship_contexts,
                selector_bindings,
            )
            if (
                relationship_contexts
                and not relationship_is_selective
                and not returns_count_only
                and not has_result_limit
            ):
                return QuerySafetyDecision(
                    False,
                    "unselective_relationship_enumeration",
                    "relationship enumeration requires an exact name or objectid "
                    "endpoint, count, or LIMIT in the same WITH stage",
                    fingerprint,
                )

            expensive_enumeration = any(
                expansion["all_shortest"]
                or (not expansion["shortest"] and not expansion["typed"])
                for expansion in stage_expansions
            )
            if (
                expensive_enumeration
                and not returns_count_only
                and not has_result_limit
            ):
                return QuerySafetyDecision(
                    False,
                    "recursive_enumeration_without_limit",
                    "recursive path enumeration requires an explicit LIMIT",
                    fingerprint,
                )
            standalone_nodes = _standalone_node_bindings(stage)
            if (
                any(
                    variable not in standalone_filter_bindings
                    for variable in standalone_nodes
                )
                and not returns_count_only
                and not has_result_limit
            ):
                return QuerySafetyDecision(
                    False,
                    "unselective_node_enumeration",
                    "node enumeration requires a bound selector, count, or result "
                    "LIMIT in the same WITH stage",
                    fingerprint,
                )
            prior_selector_bindings = selector_bindings
            prior_filter_bindings = standalone_filter_bindings
        return QuerySafetyDecision(True, "allowed", "query admitted", fingerprint)


class QueryDenyCache:
    """Durable campaign-scoped deny cache for known expensive query fingerprints."""

    def __init__(
        self,
        path: Path | None,
        *,
        manifest_fingerprint: str,
        policy_version: str,
    ) -> None:
        self.path = path
        self.manifest_fingerprint = manifest_fingerprint
        self.policy_version = policy_version
        self.entries: dict[str, dict[str, str]] = {}
        self._load()
        if self.path is not None and not self.path.exists():
            self._persist()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("manifest_fingerprint") != self.manifest_fingerprint:
            raise ValueError(
                f"Direct query deny cache {self.path} belongs to a different manifest."
            )
        if payload.get("policy_version") != self.policy_version:
            raise ValueError(
                f"Direct query deny cache {self.path} uses a different policy version."
            )
        self.entries = dict(payload.get("entries") or {})

    def reason_for(self, fingerprint: str) -> str | None:
        entry = self.entries.get(fingerprint)
        return entry.get("rule") if entry else None

    def record(self, fingerprint: str, *, rule: str, detail: str) -> None:
        if fingerprint in self.entries:
            return
        self.entries[fingerprint] = {
            "rule": rule,
            "detail": detail,
            "first_seen_at": datetime.now(UTC).isoformat(),
        }
        if self.path is None:
            return

        self._persist()

    def _persist(self) -> None:
        if self.path is None:
            return
        payload = {
            "schema_version": 1,
            "manifest_fingerprint": self.manifest_fingerprint,
            "policy_version": self.policy_version,
            "entries": self.entries,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


class DirectQueryCoordinator:
    """Serialize query execution and hold campaign-wide circuit state."""

    _HEALTH_CHECK_FAILURES = {
        "query_timeout",
        "client_timeout",
        "transport_error",
        "server_unavailable",
        "server_error",
        "rate_limited",
    }

    def __init__(
        self,
        *,
        bhce: BHCEClient,
        config: DirectQuerySafetyConfig,
        deny_cache: QueryDenyCache,
    ) -> None:
        self.bhce = bhce
        self.config = config
        self.policy = DirectQueryPolicy(config)
        self.deny_cache = deny_cache
        self._lock = asyncio.Lock()
        self.circuit_open = False
        self.circuit_reason = ""

    def close_circuit(self) -> None:
        self.circuit_open = False
        self.circuit_reason = ""

    def skipped_result(self) -> CypherResult:
        from .bhce import CypherResult

        return CypherResult(
            success=False,
            error=f"Direct query circuit is open: {self.circuit_reason}",
            failure_type="circuit_open",
            failure_subtype="circuit_open_unexecuted",
            query_executed=False,
            execution_attempts=0,
            safety_policy_version=self.config.policy_version,
            circuit_state="open",
        )

    async def execute(self, query: str) -> CypherResult:
        from .bhce import CypherResult

        decision = self.policy.evaluate(query)
        if not decision.allowed:
            self.deny_cache.record(
                decision.fingerprint,
                rule=decision.rule,
                detail=decision.detail,
            )
            return CypherResult(
                success=False,
                error=f"Direct query rejected by safety policy: {decision.detail}",
                failure_type="policy_rejected",
                failure_subtype=decision.rule,
                query_executed=False,
                execution_attempts=0,
                query_fingerprint=decision.fingerprint,
                safety_policy_version=self.config.policy_version,
                safety_rule=decision.rule,
                bhce_health_after="not_checked",
                circuit_state="open" if self.circuit_open else "closed",
            )

        cached_rule = self.deny_cache.reason_for(decision.fingerprint)
        if cached_rule:
            timeout_quarantine = cached_rule == "server_query_timeout"
            return CypherResult(
                success=False,
                error=(
                    "Direct query rejected because its fingerprint is already quarantined "
                    f"by rule {cached_rule}"
                ),
                failure_type=(
                    "policy_rejected" if timeout_quarantine else "server_unavailable"
                ),
                failure_subtype=(
                    "known_expensive_query"
                    if timeout_quarantine
                    else "quarantined_after_infra_failure"
                ),
                query_executed=False,
                execution_attempts=0,
                query_fingerprint=decision.fingerprint,
                safety_policy_version=self.config.policy_version,
                safety_rule=cached_rule,
                bhce_health_after="not_checked",
                circuit_state="open" if self.circuit_open else "closed",
            )

        async with self._lock:
            if self.circuit_open:
                result = self.skipped_result()
                result.query_fingerprint = decision.fingerprint
                return result

            result = await self.bhce.run_cypher(
                query,
                server_timeout_seconds=self.config.server_timeout_seconds,
                client_timeout_seconds=self.config.client_timeout_seconds,
            )
            result.query_fingerprint = decision.fingerprint
            result.safety_policy_version = self.config.policy_version
            result.safety_rule = "allowed"
            if result.success:
                result.bhce_health_after = "not_checked"
                result.circuit_state = "closed"
                return result

            if result.failure_type == "query_timeout":
                self.deny_cache.record(
                    decision.fingerprint,
                    rule="server_query_timeout",
                    detail=result.error or "BloodHound query timeout",
                )

            if result.failure_type == "auth_error":
                self.circuit_open = True
                self.circuit_reason = result.error or "BloodHound authentication failure"
                result.bhce_health_after = "not_checked"
            elif result.failure_type in self._HEALTH_CHECK_FAILURES:
                try:
                    health = await self.bhce.check_health()
                    result.bhce_health_after = "healthy" if health.ok else "unhealthy"
                except Exception as exc:  # pragma: no cover - defensive boundary
                    health = None
                    result.bhce_health_after = "unhealthy"
                    result.error = f"{result.error}; health check failed: {exc}"
                if health is None or not health.ok:
                    self.circuit_open = True
                    self.circuit_reason = result.error or "BloodHound health check failed"
                    self.deny_cache.record(
                        decision.fingerprint,
                        rule="destabilizing_query",
                        detail=self.circuit_reason,
                    )
            else:
                result.bhce_health_after = "not_checked"

            result.circuit_state = "open" if self.circuit_open else "closed"
            return result


_COORDINATORS: dict[str, DirectQueryCoordinator] = {}


def register_coordinator(coordinator: DirectQueryCoordinator) -> str:
    token = uuid4().hex
    _COORDINATORS[token] = coordinator
    return token


def get_coordinator(token: str) -> DirectQueryCoordinator:
    try:
        return _COORDINATORS[token]
    except KeyError as exc:
        raise RuntimeError(
            "Direct query coordinator is unavailable for this Inspect sample."
        ) from exc


def unregister_coordinator(token: str) -> None:
    _COORDINATORS.pop(token, None)
