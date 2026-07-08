# ORI Phase 4B/v2 Query Strategy Resources

These templates are non-answer-leaking strategy guidance for MCP/direct diagnostic runs. They describe search/query shape only; they must not include planted reference answers.

## ADCS / ESC1

Recommended evidence path:
1. Enumerate certificate templates with enrollee-supplies-subject and client-authentication EKUs.
2. Identify principals/groups with enrollment rights.
3. Resolve nested group membership before declaring reachability.
4. Validate the CA/template chain exists before finalizing.

Common failure modes:
- treating template display names as principals
- missing nested membership through delegated groups
- reporting an ESC1-like template without proving enrollment rights

## Unconstrained delegation plus sessions

Recommended evidence path:
1. Find computers with unconstrained delegation.
2. Find active sessions on those computers.
3. Resolve whether the session principal is privileged or path-relevant.
4. Preserve both the delegated host and session user in the final answer.

Common failure modes:
- reporting only the host
- reporting only the session user
- skipping nested/admin group context

## Active sessions

Recommended evidence path:
1. Query session edges from computers/users as required by the task.
2. Normalize node names to BloodHound canonical names.
3. Distinguish `HasSession` evidence from admin/control edges.

Common failure modes:
- using stale/non-canonical names
- confusing local admin rights with active session presence

## Source-to-target pathfinding

Recommended evidence path:
1. Anchor exact source and target nodes first.
2. Run shortest path with bounded depth.
3. Return all required path nodes, not just endpoints.
4. If no path is found, verify both source and target existed before reporting no path.

Common failure modes:
- missing an intermediate hop
- reporting endpoint-only paths
- setting `path_found=false` despite successful tool evidence

## Nested group membership

Recommended evidence path:
1. Expand group membership recursively.
2. Preserve directionality: member -> group -> privileged target.
3. Include the nested group(s) required by the strict reference path.

Common failure modes:
- one-hop membership only
- wrong direction through MemberOf edges
- omitting the nested group hop in final answer
