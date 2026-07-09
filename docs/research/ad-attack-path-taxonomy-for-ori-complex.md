# AD Attack Path Taxonomy for ORI Complex Benchmark

Date: 2026-07-07
Status: research synthesis for implementation planning

## Purpose

Expand ORI complex benchmark attack paths beyond ADCS. Complex multi-hop paths should cover the major BloodHound-style Active Directory abuse primitives and combine them into realistic chained operator decisions.

## Source notes

Primary source lane: SpecterOps BloodHound edge documentation, because ORI already models BloodHound/SharpHound graph semantics.

Referenced BloodHound edge docs:

- AdminTo — local administrator on target computer: `https://bloodhound.specterops.io/resources/edges/admin-to`
- HasSession — authenticated user leaves credentials/session exposure on a computer: `https://bloodhound.specterops.io/resources/edges/has-session`
- AllowedToDelegate — constrained delegation primitive: `https://bloodhound.specterops.io/resources/edges/allowed-to-delegate`
- AllowedToAct — resource-based constrained delegation / RBCD: `https://bloodhound.specterops.io/resources/edges/allowed-to-act`
- AbuseTGTDelegation — unconstrained delegation through trust/TGT forwarding: `https://bloodhound.specterops.io/resources/edges/abuse-tgt-delegation`
- GenericAll — full control over target object: `https://bloodhound.specterops.io/resources/edges/generic-all`
- GenericWrite — write non-protected attributes including group members or SPNs: `https://bloodhound.specterops.io/resources/edges/generic-write`
- WriteDacl — grant self privileges by modifying target DACL: `https://bloodhound.specterops.io/resources/edges/write-dacl`
- WriteOwner — become owner / modify security descriptor path: `https://bloodhound.specterops.io/resources/edges/write-owner`
- AddMember — add arbitrary principals to a target security group: `https://bloodhound.specterops.io/resources/edges/add-member`
- ForceChangePassword — reset target user password: `https://bloodhound.specterops.io/resources/edges/force-change-password`
- CanRDP — interactive remote access path to computer: `https://bloodhound.specterops.io/resources/edges/can-rdp`
- CanPSRemote — PowerShell remote session path to computer: `https://bloodhound.specterops.io/resources/edges/can-ps-remote`
- ExecuteDCOM — remote code execution primitive: `https://bloodhound.specterops.io/resources/edges/execute-dcom`
- GPLink — linked GPO applies settings to linked container: `https://bloodhound.specterops.io/resources/edges/gp-link`
- ReadLAPSPassword / SyncLAPSPassword — local admin password retrieval paths: `https://bloodhound.specterops.io/resources/edges/read-laps-password`, `https://bloodhound.specterops.io/resources/edges/sync-laps-password`
- SameForestTrust / CrossForestTrust — intra/cross forest trust relationships: `https://bloodhound.specterops.io/resources/edges/same-forest-trust`, `https://bloodhound.specterops.io/resources/edges/cross-forest-trust`

## Complex benchmark families

### 1. Host compromise via admin session exposure

Core idea: compromise a host where an admin has a session, then use exposed credentials/tokens to continue.

Useful primitives:

- `AdminTo`
- `CanRDP`
- `CanPSRemote`
- `ExecuteDCOM`
- `HasSession`
- `MemberOf`

Example ORI path:

```text
low-priv user
  -> MemberOf workstation operators
  -> CanPSRemote / AdminTo on WS-01
  -> HasSession from Tier-1 admin on WS-01
  -> Tier-1 admin MemberOf server admins
  -> AdminTo on MGMT-01
  -> HasSession from Domain Admin on MGMT-01
  -> Domain Admins / DC
```

Chained session variant:

```text
low-priv user
  -> compromise HOST-A through AdminTo / CanPSRemote / local admin password
  -> HOST-A has session from ADMIN-B
  -> use ADMIN-B to compromise HOST-B
  -> HOST-B has session from ADMIN-C or Domain Admin
  -> use ADMIN-C to compromise HOST-C / management server
  -> HOST-C has DA session OR a misconfiguration that grants DA path
  -> Domain Admins / DC
```

Terminal misconfiguration variants after chained host compromise:

- final host has DA `HasSession`.
- final host grants `AdminTo` to DC.
- final host exposes `ReadLAPSPassword` for a DC-adjacent management server.
- final host has `AllowedToAct` / RBCD path to a Tier 0 server.
- final host is controlled by a GPO/OU path that can add local admin or scheduled-task execution.
- final host stores a service account with `AllowedToDelegate` to a Tier 0 service.

Question shape:

```text
Starting from {source_user}, identify the host-compromise route to Tier 0. Include the hosts where credential/session exposure creates the next hop.
```

Decoys:

- Hosts with sessions from non-privileged users.
- Admin sessions on hosts where the source cannot obtain execution.
- AdminTo edges that lead to dead-end servers.

### 2. Constrained delegation chain

Core idea: a principal with constrained delegation can impersonate users to specific services on target computers.

Useful primitives:

- `AllowedToDelegate`
- `CanPSRemote` / `CanRDP` / `AdminTo`
- `HasSession`
- `MemberOf`

Example ORI path:

```text
service account
  -> AllowedToDelegate to CIFS/HTTP on APP-01
  -> impersonate privileged user to APP-01
  -> local admin / service control on APP-01
  -> bridge to management server
  -> Tier 0
```

Question shape:

```text
Which delegated service path lets {source_account} reach {target_group}, and which delegated target is the required bridge?
```

Decoys:

- Delegation to services on non-privileged/dead-end hosts.
- Delegation to a host that has no onward Tier 0 path.
- A similarly named service account without delegation.

### 3. Resource-based constrained delegation / RBCD chain

Core idea: `AllowedToAct` on a computer lets a principal pretend to be another user to that computer.

Useful primitives:

- `AllowedToAct`
- `GenericAll` / `GenericWrite` over computer object to configure RBCD
- `AdminTo`
- `HasSession`

Example ORI path:

```text
controlled computer or service account
  -> GenericWrite/GenericAll over TARGET-SRV computer
  -> configure RBCD / AllowedToAct
  -> access TARGET-SRV as privileged identity
  -> discover admin session or local admin route
  -> Domain Admins
```

Question shape:

```text
Can {source_principal} use RBCD to move through a host that reaches Tier 0? Name the computer object and the next hop.
```

Decoys:

- GenericWrite over a computer with no downstream privilege.
- AllowedToAct on a host that does not bridge to Tier 0.

### 4. Unconstrained delegation / TGT capture chain

Core idea: a trusted principal authenticating to an unconstrained delegation host can expose a TGT that enables impersonation.

Useful primitives:

- unconstrained delegation computer property
- `HasSession`
- `AdminTo`
- `AbuseTGTDelegation`
- trust edges where applicable

Example ORI path:

```text
attacker controls or can reach unconstrained host
  -> privileged user authenticates / has session there
  -> TGT capture / impersonation
  -> privileged user reaches DC / Domain Admins
```

Question shape:

```text
Which unconstrained delegation host creates a viable Tier 0 path, and which apparent delegation host is a dead end?
```

Decoys:

- Unconstrained hosts with no privileged sessions.
- Privileged sessions on hosts source cannot compromise.
- Delegation across a trust where TGT delegation is not enabled.

### 5. ACL-to-group escalation chain

Core idea: ACL rights over users/groups let the attacker alter membership or credentials, then inherit group privileges.

Useful primitives:

- `GenericAll`
- `GenericWrite`
- `WriteDacl`
- `WriteOwner`
- `AddMember`
- `ForceChangePassword`
- `MemberOf`

Example ORI path:

```text
user
  -> WriteDacl over Helpdesk Admins
  -> grant AddMember / add self
  -> Helpdesk Admins MemberOf Server Operators
  -> Server Operators AdminTo management server
  -> management server HasSession from DA
  -> Tier 0
```

Question shape:

```text
Find the ACL abuse sequence from {source_user} to Tier 0. Include the rights change or group membership step, not just the final path.
```

Decoys:

- ACL over a lookalike group not nested into privileged roles.
- ForceChangePassword on a user that lacks onward privileges.
- WriteDacl over a protected/high-value object that should require additional assumptions.

### 6. GPO / OU control chain

Core idea: control over GPO or OU linkage can affect computers/users and create execution or local admin paths.

Useful primitives:

- `GenericWrite` / `WriteDacl` over GPO or OU
- `GPLink`
- `GPOAffectedByContainer`
- `AdminTo`
- `CanPSRemote`

Example ORI path:

```text
operator group
  -> GenericWrite over workstation policy GPO
  -> GPO linked to Tier-1 admin workstation OU
  -> creates local admin / execution on affected hosts
  -> HasSession from admin
  -> Tier 0
```

Question shape:

```text
Which GPO/OU control path lets {source_group} compromise a host with onward Tier 0 reachability?
```

Decoys:

- GPO linked to an OU with no relevant computers.
- GPO affecting workstations that contain no admin sessions.
- GPO write without link/effective application.

### 7. LAPS/local admin password retrieval chain

Core idea: reading LAPS or syncing confidential local admin password attributes yields local admin on a host, then host/session pivot.

Useful primitives:

- `ReadLAPSPassword`
- `SyncLAPSPassword`
- `AdminTo`
- `HasSession`
- `CanPSRemote`

Example ORI path:

```text
helpdesk group
  -> ReadLAPSPassword on server/workstation
  -> local admin on host
  -> host has session from privileged admin
  -> privileged admin reaches Domain Admins
```

Question shape:

```text
Does LAPS access give {source_group} a viable route to Tier 0? Identify the host and the credential/session bridge.
```

Decoys:

- LAPS access to isolated workstation.
- LAPS access where no privileged session or onward admin path exists.

### 8. Trust hopping / cross-domain bridge

Core idea: compromise in one domain crosses trust boundaries through trusted principals, group nesting, SID/history-like relationships, or delegation over trust.

Useful primitives:

- `SameForestTrust`
- `CrossForestTrust`
- `TrustedBy`
- `MemberOf`
- `AdminTo`
- `AllowedToDelegate`
- `AbuseTGTDelegation`

Example ORI path:

```text
branch domain user
  -> ACL/group escalation in branch domain
  -> branch admin group nested/bridged into resource access in root domain
  -> root management server AdminTo / HasSession
  -> Enterprise Admins / root DC
```

Question shape:

```text
Which trust bridge, if any, lets {source_principal} move from {source_domain} to root-domain Tier 0? Reject routes that stop at child-domain admin only.
```

Decoys:

- Cross-forest trust without the necessary direction.
- Path reaches only child-domain Domain Admins.
- Similar usernames/groups in different domains.

### 9. Kerberoast / SPN-to-privilege chain

Core idea: Kerberoastable service account compromise becomes meaningful only if the cracked account has onward privileges.

Useful primitives:

- `hasspn` user property
- `MemberOf`
- `AdminTo`
- `AllowedToDelegate`
- ACL edges

Example ORI path:

```text
Kerberoastable svc account
  -> crack credential assumption
  -> service account MemberOf app admins
  -> app admins AdminTo app server
  -> app server HasSession from server admin
  -> server admin reaches Tier 0
```

Question shape:

```text
Which Kerberoastable account produces a real Tier 0 route, and which SPN account is only a decoy?
```

Decoys:

- Kerberoastable accounts with no onward privilege.
- SPN account with privileges only to non-critical apps.

### 10. ADCS as one family, not the center

ADCS remains important, but should be no more than one slice of complex.

Useful primitives:

- `Enroll`
- `AutoEnroll`
- `PublishedTo`
- `IssuedSignedBy`
- `TrustedForNTAuth`
- template properties: client authentication, enrollee supplies subject, manager approval, security extension

Example ORI path:

```text
user/group
  -> enroll in exploitable template
  -> certificate impersonation / identity transition
  -> privileged bridge account
  -> delegation/session/ACL onward path
  -> Tier 0
```

Negative controls:

- enrollment exists but template lacks client authentication.
- enrollment exists but manager approval required.
- template published but not trusted for NTAuth.

## Recommended ORI complex path suite

The complex benchmark needs a large pool, not one hand-authored path per family. The generator should expose many variant templates so seeds can choose different concrete paths while preserving benchmark coverage.

Initial implementation target:

- At least 10 attack-path families.
- At least 5 positive variants per family.
- At least 2 decoy/negative-control variants per family.
- Minimum useful pool before seeded selection: ~70 path templates.
- Better target for `complex/v1`: 100–150 path templates.

Family coverage target:

1. Host compromise via admin session exposure — 12–18 variants.
2. Constrained delegation — 8–12 variants.
3. RBCD — 8–12 variants.
4. Unconstrained delegation — 6–10 variants.
5. ACL-to-group escalation — 10–15 variants.
6. GPO/OU control — 6–10 variants.
7. LAPS/local admin password retrieval — 6–10 variants.
8. Trust hopping / cross-domain bridge — 8–12 variants.
9. Kerberoast-to-privilege chain — 6–10 variants.
10. ADCS composite — 8–12 variants.

Seeded selection should then build:

- `complex diagnostic`: 24 tasks from the pool, with fixed family allocation and seeded concrete choices.
- `complex official`: 100 tasks from the pool, with fixed family allocation and seeded concrete choices.

This means the benchmark contract controls coverage and task counts, while the seed controls which path variants, source principals, targets, bridge hosts, decoys, and negative controls are instantiated.

## Scoring implications

Complex tasks should not only ask “is there any path?” They should ask for:

- Required bridge objects.
- Required mechanisms.
- Ordered transitions.
- Decoy rejection.
- Negative-control proof.
- Invalidated assumption handling.

Failure subtype taxonomy should include:

- `INCOMPLETE_PATH`
- `WRONG_BRIDGE_OBJECT`
- `WRONG_MECHANISM`
- `DECOY_ACCEPTED`
- `WRONG_PATH_SELECTED`
- `SEQUENCE_ERROR`
- `MISSING_IDENTITY_TRANSITION`
- `INVALIDATED_EDGE_USED`
- `NEGATIVE_CONTROL_FALSE_POSITIVE`
- `INSUFFICIENT_EVIDENCE`

## Immediate implementation recommendation

Do not implement only ADCS. Start with a non-ADCS path pack:

1. `t6_host_session_pivot_tier0`
2. `t6_constrained_delegation_bridge_tier0`
3. `t6_rbcd_computer_takeover_tier0`
4. `t6_unconstrained_delegation_tgt_capture_tier0`
5. `t6_acl_group_nesting_tier0`

Then add:

6. `t6_gpo_ou_control_tier0`
7. `t6_laps_session_pivot_tier0`
8. `t6_trust_hopping_tier0`
9. `t6_kerberoast_privilege_chain_tier0`
10. `t6_adcs_identity_transition_tier0`

This creates a broad enough corpus for meaningful seeded selection later.
