# ORI OpenGraph extension framework — design v1

Status: independently reviewed design, September 5, 2026; conference freeze pending. Owns P12-W01–W06 of
the [OAIC execution plan](plans/2026-09-05-oaic-execution-plan.md). This document
defines future behavior; none of the proposed extension APIs or commands is
implemented by this document. It is not a claim of OpenGraph certification.

Independent architecture and repository/domain-boundary reviews found no
remaining blocking findings after revision. Reviews covered explicit registries,
identity and alias rules, bridge/helper ownership, immutable locks, cache scope,
proof completeness, canonical encoding and strict record fields. The configured
specialist reviewers could not start; separate general architecture and
repository reviewers performed the actual reviews. This is design assurance,
not executable implementation or live backend qualification. Document QA
recomputed both synthetic encoding vectors and checked local links and secrets.

## 1. Decision and release boundary

ORI will support vendor-neutral domain packages using an explicit registry,
typed graph contracts and claim-bound evidence. A domain is not an MCP server,
provider, host, or agent installation. The same portable CLI and scoring path
serve terminal users and external automation.

Before October 5: deliver this reviewed design, contract walkthroughs, migration
rules and an acceptance matrix. Design review closes by September 24; its
conference copy joins the September 26 evidence package and September 29
technical freeze. Do not implement an extension loader, migrate AD, install a
BloodHound extension, or build a vendor-specific integration before the talk.
Those are post-conference work, starting no earlier than October 6 and requiring
their own reviewed implementation and qualification. No GitHub-specific domain
is privileged or required by the framework.

Future extension artifacts use **ORI evaluation protocol v3**, not a silent
reinterpretation of V2. Existing V1/V2 products, identities, recipes, comparators,
command defaults and historical readers remain available unchanged. New generic
records may reuse proven algorithms, but they must not broaden V2 validation.
V3 cannot resume, rescore as official, or merge ranking denominators with V2.

## 2. What the current repository actually supplies

| Existing surface | Reuse | Required future boundary |
| --- | --- | --- |
| `eval/v2/schema.py`: typed route/set/count/decision/absence claims, AcceptanceSpec, EvidenceIR-related records | Claim/answer separation, explicit bounds and proof policies | V3 schema registry resolves qualified kinds before validation; do not remove AD checks in place |
| `relationships.py`: canonical kinds, endpoint contracts, SharpHound carriers | AD compatibility package retains these exact contracts | New domain relationship registry; an arbitrary string is not a certified relationship |
| `eval/task_recipes.py` and V2 re-export | Fail-closed recipe coverage and track exclusions | Registry keyed by qualified recipe ID, not legacy task-ID dispatch |
| `eval/v2/compiler.py` | Public acceptance and private oracle derived from one typed claim | Separate V3 compiler context carrying an immutable extension lock |
| `eval/v2/identity.py` | Collision rejection and stable identity return | Current case folding and AD alias synthesis stay AD-only |
| `eval/v2/graph.py` | Exact graph digests and bounded read acquisition | Generic graph snapshot without mandatory AD domain/SID; OpenGraph wire importer/exporter |
| `eval/v2/mcp.py`, `schema.py`, projectors | Native receipt audit and claim-relevance proof | Existing singleton profile/resource restrictions and literal `cypher_query.run` contract are not generic extension support |
| `eval/v2/certification.py`, fixtures and comparator | Full fixture replay, failure-closed promotion, semantic equivalence checks | Certification bound to domain registry, native projector and complete implementation closure |
| `eval/v2/public_surfaces.py`, campaign reporting/status | Separate solver contract from redacted publication | V3 projections retain this distinction; “solver-visible” never means safe for public result export |

Current AD graph evidence and the 46/70-task proof inventory do not certify any
new domain. Current string-valued `EntityRef.object_type` and `EdgeWitness.relationship`
alone do not establish OpenGraph support: downstream endpoint, query, identity,
projection and certification boundaries still matter.

## 3. Domain package and manifest

The first framework revision is `ori-extension-manifest-v1`. Use UTF-8 JSON,
unique object keys, strict types, finite numbers and `additionalProperties: false`
at every record boundary. No YAML coercion, remote schema resolution, wildcard
dependencies, runtime package downloads or arbitrary file-path entrypoints.

Every manifest has exactly these required fields (empty arrays are explicit):

| Field | Contract |
| --- | --- |
| `schema_version` | Literal `ori-extension-manifest-v1` |
| `namespace` | Lowercase reverse-DNS syntax: at least two dot-separated labels, each `[a-z][a-z0-9-]{0,62}`; max 253 characters; no normalization after acceptance |
| `package_kind` | `domain` or `bridge`; a bridge owns no nodes and must own at least one relationship |
| `version` | Exact `major.minor.patch`, each nonnegative integer; no ranges or prerelease aliases in a certified lock |
| `framework_api` | Literal `ori-extension-api-v1` |
| `identity_version` | Positive integer; independent from display labels |
| `display_name`, `description`, `license` | Nonempty plain strings; no executable/remote-rendered content |
| `dependencies` | Sorted records `{namespace, version, package_sha256}`; one version per namespace; dependency DAG required |
| `imports` | Sorted `{dependency_namespace, node_kind_ids, relationship_kind_ids, reason_ids}` records; exact qualified IDs from a locked dependency, no wildcards; dependency presence alone grants no imports |
| `node_kinds`, `relationship_kinds` | Sorted schema records defined below; domain nodes must be nonempty; bridge nodes must be empty; relationships must be nonempty; no duplicate qualified IDs |
| `derivations` | Sorted `{id, version, implementation_id, implementation_sha256, premise_relationship_ids, output_relationship_id, max_depth, max_input_facts, max_output_facts}`; empty allowed |
| `negative_reasons` | Sorted `{id, version, public_meaning, applicable_claim_kinds, predicate_id, predicate_sha256}`; empty allowed; applicable kinds are only `absence` and `decision` |
| `recipes`, `fixtures` | Sorted declarations with local ID, version, registered implementation ID and content SHA-256 |
| `entrypoints` | Exact registered IDs for `generate`, `compile_claim`, `build_oracle`; not Python import strings |
| `projectors` | Records `{implementation_id, capability_profile_sha256, projector_id, projector_sha256, supported_claim_kinds, supported_surfaces}` |
| `backend_profiles` | Exact backend implementation/version, wire-schema digests, serializer/projector IDs, normalization version and capability requirements |
| `files` | Sorted records `{relative_path, sha256, audience}`; audience is `public_definition`, `solver_contract`, or `scorer_private` |

Local IDs match `[A-Za-z][A-Za-z0-9_]{0,63}`. A qualified ID is
`namespace::LocalId`; namespaces and local IDs are case-sensitive after the
namespace syntax check. Qualified IDs are registry keys, not BloodHound object
IDs or wire labels. `ori.*`, `org.ori.core`, and `org.ori.compat.ad` are reserved
for the framework; examples use `org.ori.examples.access` and
`org.ori.examples.workload` and cannot enter an official candidate pool.

Package digest: canonical JSON hash of the manifest plus the sorted file digest
inventory, excluding a digest field itself. The manifest does not list itself in
`files`. All symlinks, absolute paths, `..`, duplicate/case-colliding paths, extra
unlisted files and missing files fail registration. Registration verifies bytes
before any callable is resolved. The reviewed installed implementation registry
maps IDs to factories and complete code/dependency hashes. A package cannot
invent a new executable ID merely by naming it. New Python implementation code
is installed and approved separately by the operator; registry validation is not
a sandbox or a substitute for code review.

Registration creates an immutable `ExtensionLock` in the requested output root,
not a global machine registry. It pins package/dependency digests, framework API,
all callable implementation closures, backend and native MCP capability profiles.
No network or provider calls occur. An identical lock is idempotent; a different
lock in the same output root fails. Namespace ownership is an explicit approval
in that registry, not proof inferred from a DNS-looking string or package name.

Registration holds an exclusive output-root process lock. Verified package bytes
are copied into scorer-private content-addressed storage inside that root; the
copy is hashed again, then the lock file is atomically replaced and file/parent
directories flushed using the campaign durability pattern. No executable is
resolved from the mutable original package directory. A differing concurrent
registration fails without replacing the winner; an identical one verifies and
returns the existing lock. Revalidate installed implementation/dependency bytes
before worker startup. Changed content is `EXT_STALE_EVIDENCE`, not an automatic
lock refresh. Content-addressed storage is integrity checking, not OS isolation.

## 4. Schema, identity and graph semantics

### Nodes and properties

A node-kind record contains `id`, `description`, `properties`
and `principal` (boolean). Exactly one semantic node kind belongs to an object.
`properties` is a map of local property IDs to `{type, required, nullable,
normalization, visibility}`. Visibility is the single literal `public_graph`:
any graph fact needed for evaluated reasoning must be observable, not hidden by
a private property tag. Oracle bookkeeping belongs in separate sealed artifacts.
Types are `string`, `boolean`, `integer`, or
`number`; integers stay within signed 64-bit range and numbers are finite.
No nested objects/lists in v1 facts. Use explicit related nodes for collections.
Boolean `true`, integer `1`, missing and null are distinct in typed fact hashes.
No implicit string/number conversions. Required means present even when nullable.
Normalization is one of `exact`, `unicode_nfc`, or `ascii_casefold`; it must be
declared per field and applied identically to generated, live and answer facts.
Opaque source identity fields use `exact` only. Unknown properties fail input
validation; backend-generated bookkeeping is excluded only by a pinned backend
normalization allowlist, never by blanket removal of unfamiliar fields.

### Stable IDs and aliases

Canonical identity input is the ordered record
`{identity_version, namespace, kind, authority, source_id}`. `authority` is the
exact synthetic tenant/source-instance ID and `source_id` is the exact immutable
source identifier. Both are nonempty UTF-8 strings. Leading/trailing whitespace,
control characters and non-NFC strings fail instead of being silently trimmed.
Display names, email addresses, host paths, model IDs and array positions are not
source identity. Seeded generation creates stable source IDs using its named
random stream; changing a display label does not change identity.

`kind` in the identity input is the complete qualified kind ID. Domains cannot
override, extend or remove identity fields in manifest v1. The external object
ID is `oriog-` followed by the full lowercase SHA-256 of the canonical identity
input, using the exact canonical algorithm in section 12. No colons, truncation
or case-folded source keys.
Different authority/namespace/kind combinations remain different identities.
Identical IDs with unequal identity inputs are a hard collision error; duplicate
identical objects are coalesced only when their complete typed facts also agree.

Aliases are explicit `{value, normalization}` records associated with one object.
Lookup is a discriminated record: `{by: "object_id", object_id}` or
`{by: "alias", namespace, kind, authority, value}`. Alias normalization is drawn
from the object's registered declaration; multiple matching normalized aliases
still require exactly one object. There is no inferred textual delimiter syntax.
Canonical-ID-shaped tokens (`oriog-` plus 64 lowercase hex characters) resolve
only as exact IDs and may not be registered as aliases; unknown ones never fall
through to alias lookup. Ambiguous aliases fail, even if one candidate is
encountered first. No email/UPN/NETBIOS aliases are synthesized outside the AD
compatibility package. Cross-domain accounts are linked by declared edges; they
are never merged merely because names match.

### Relationships and derivations

Each relationship record contains `id`, `description`, `source_kinds`,
`target_kinds`, `properties`, `semantics`, `traversable`, and `derivation`.
Endpoints are qualified kind IDs; dependency-owned kinds must be explicitly
imported. Edges are directed. `semantics` is `direct` or `derived`; `derivation`
is null for direct edges and a required registered rule ID/version/hash for
derived edges. The first revision admits no undirected primitive: symmetric
relations require two explicitly justified directed edges.

One `(source_object_id, relationship_id, target_object_id)` has one canonical
property record. These endpoints are canonical `oriog-...` IDs, not the
authority-local `source_id` input to identity hashing.
Identical duplicate records collapse; conflicting properties fail. Multi-instance
relationships must be reified as distinct intermediary objects, not depend on
an undocumented backend edge-ID policy. A graph may contain cycles, but a route
claim requiring a simple path rejects repeated identities.

Derived facts carry a private proof DAG of rule IDs and premise fact hashes.
Rules are deterministic positive, bounded joins over declared facts; no network,
wall clock, hidden query, unbounded recursion or absence-as-fact inference.
Rule dependency cycles fail. Declared rule depth is at most eight and each rule
must have explicit input/output cardinality limits. Exceeding any limit fails
generation/certification rather than silently truncating the graph.
Transitive route search remains a bounded claim operation, not an invented edge.

A native response may prove a derived relationship only when its certified
projector attests that fact and its declared premise proof through observed
records. It cannot reconstruct missing premises from the oracle. A backend-only
derived label without the required observable proof is exploratory, not scoreable.
Declarative metadata about traversability is not itself evidence of permission.

## 5. Ownership and typed interfaces

Proposed module ownership is fixed; these modules do not yet exist:

| Owner / future module | Operation | Typed result and authority |
| --- | --- | --- |
| Registry / `ori.extensions.registry` | `register(package, approved_registry, dependency_locks)` | `ExtensionLock`; local bytes only, no callable execution |
| Schema / `ori.extensions.schema` | `validate(lock, domain_records)` | `ValidatedDomainGraph`; strict kinds, identities, endpoints and property types |
| Generator / `ori.extensions.generation` | `generate(lock, GenerationSpec)` | `GeneratedDomain(graph, wire_payloads, provenance)`; deterministic synthetic data only |
| Compiler / `ori.extensions.compiler` | `compile_claim(lock, RecipeInstance, PublicGraphContext)` | `ClaimContract(public_claim, AcceptanceSpec, submission_schema, bounds)`; no oracle argument |
| Oracle / `ori.extensions.oracle` | `build_oracle(lock, ClaimContract, SealedGraph)` | `OracleArtifact`; separate scorer process/storage, never provider context |
| Runtime / `ori.extensions.projection` | `project(lock, NativeObservation, PublicClaimContext)` | `ProjectionReceipt`; no sealed graph/oracle input and no tool-name translation |
| Certification / `ori.extensions.certification` | `certify(lock, artifacts, FixtureInventory, graph_receipts)` | `CertificationCatalog`; promotion only after all required cases pass |
| Release / existing selector successor | `select(certified_pool, SelectionPolicy, seed)` | `ReleaseManifest`; exact quotas, semantic uniqueness and reproducible subset |

All values are immutable strict records. Success is returned explicitly; expected
failures use `ExtensionError{code, stage, extension_id, item_id, retryable, detail}`.
Private `detail` may identify the defective field but is never public-exported.
Public failures contain only code/stage and opaque public IDs. No truthy empty
result is a successful registration, projection, oracle, or certificate.

`GenerationSpec` contains unsigned 64-bit `seed`, `product_id`, `generator_version`,
explicit per-kind counts and total node/edge limits. `RNGContext` derives each
stream from SHA-256 of canonical `{seed, namespace, component_id, version}`;
indexed counter blocks and rejection sampling define bounded integer draws.
No Python hash seed or global RNG is used. Sort objects by ID, edges by exact
triple and properties by qualified key before canonical serialization. ZIP
metadata and random-stream encoding follow section 12 exactly. Both graph bytes
and task selection must vary across the
qualification seed matrix while remaining identical for repeated same-seed runs.

`NativeObservation` contains the original implementation/revision, native
tool/resource ID, discovery schema hash, validated argument record, ordered
response parts, transport status, observation sequence, graph receipt ID and
content hashes. Prompts remain native discovered provenance; availability is
explicit. `ProjectionReceipt` contains EvidenceIR facts, completeness status,
source observation references, projection version, policy result and typed
failure. Missing response fields remain missing, not oracle-filled.

The framework controls process IO boundaries; purity annotations alone are not
enforcement. Generator/compiler/oracle/certifier workers receive no provider or
BloodHound credentials, deny networking in test/qualification sandboxes, and have
separate writable output roots. Projectors consume captured observations and do
not launch tools. Live acquisition stays in the existing controlled runtime.

Every graph cache context is immutable and scoped by
`(protocol, extension_lock_sha256, graph_sha256, normalization_sha256)`; projection
caches additionally include projector and capability-profile hashes. Reuse across
locks is forbidden even when graph bytes match. Keep the current two-graph LRU
bound per worker, evicting all derived indexes for an expired context together.
No process-global namespace-only or graph-hash-only cache serves V3 records.

## 6. Recipes, claims, evidence and selection

A recipe declaration identifies qualified recipe/template IDs, exact version,
supported tracks, claim kind, family, generator requirements, public parameter
schema, explicit per-track exclusion reasons and the fixture inventory. Every
generated template must resolve to exactly one registered recipe or a declared
non-grading exclusion. Missing and extra generated variants fail compilation.
An exclusion cannot silently lower a release quota.

The supported first-revision claim algebra is route, set, count, decision and
bounded absence. Bounds, endpoint/population selectors, required properties,
relationship directions, mechanism ordering and completeness rules are compiled
into AcceptanceSpec and its concise task description from the same typed claim.
No scorer restriction may exist only in the sealed oracle. Unsupported claim
semantics block certification instead of being coerced into an AD task kind.

V3 EvidenceIR retains stable entities, directed edges, typed facts, supporting
evidence and bounded-negative proofs, with the extension-lock fingerprint and
qualified kinds added to its versioned contract. The comparator resolves all
kinds against that lock. It accepts only graph-attested facts, applies declared
extra-evidence rules, and evaluates proof completion separately from correctness.
Invalid structured output, incomplete evidence, wrong reasoning and infrastructure
failure remain distinct. Direct and MCP results stay separate; Tool Use and
Resource Use remain N/A for Direct. No opaque combined ranking is introduced.

Complete set/count/absence claims require a mechanically established scope:
exact public population, filters, distinctness, declared ordering/window and
bounded graph context. A resource or high-level tool proves completeness only
when its actual native response carries the necessary mechanical guarantees.
A nonempty shape with an unknown count cannot prove empty results; unknown
pagination cannot prove a complete set. Route proof requires ordered observed
endpoints and directed relationships, not names mentioned elsewhere in a trace.

MCP implementations keep their native tools, prompts and resources. No normalized
tool facade is added. Each projector is keyed by exact implementation revision,
discovered capability hash and response-schema contract. An implementation may
be exploration-capable but ineligible for a particular scored claim. Missing
resources are recorded as unavailable; no synthetic resources or tool calls
stand in for them. Mutating tools are filtered before exposure, and unknown
operations fail closed. Prompt injection in native content has no authority to
change budgets, graph target, score policy or the extension lock.

Future generic releases apply the OAIC quota contract when using that release
policy: at least 60 unique certified contracts per track in the candidate pool,
exactly 50 Direct plus 50 MCP selected tasks, one graph, separate denominators.
Semantic uniqueness includes namespace-resolved public claims and must also
check oracle equivalence; renaming IDs or aliases does not create new contracts.
Use distinct deterministic seed namespaces for generation and selection. An
extension's unavailable MCP coverage never causes fallback to another server or
quiet substitution of a Direct task. Insufficient quotas stop release creation.

## 7. BloodHound wire adapter, not framework semantics

BloodHound distinguishes generic graph payloads from structured extension
definitions; basic ingest acceptance is not proof of higher-level pathfinding
capability. The first certified backend profile requires a controlled CE
PostgreSQL instance, exact version/image digest, supported schema digests and
captured capability receipts. No claim is made that a current ORI target meets
that future profile. See the official [graph-data overview](https://bloodhound.specterops.io/opengraph/developer/graph-data).

The backend exporter maps each semantic namespace to `ORIExt` plus the first
16 lowercase hex characters of SHA-256(namespace). Wire kind is that prefix,
underscore, then local ID. Collisions against another installed namespace or
built-in kind fail; no suffix retry or silent rebinding. The complete mapping
is fingerprinted. A structured definition's unique `schema.name` is
`ORIExtension_` plus that namespace digest; its `namespace` is the wire prefix
and `version` is the domain version prefixed with `v`. Never upload over an
existing different schema merely because its name matches. Namespacing and
traversability requirements come from the official [graph-definition contract](https://bloodhound.specterops.io/opengraph/developer/graph-definition).

Node wire IDs are the colon-free canonical object IDs defined above. Emit exactly
one semantic kind plus the wire label `<wire_prefix>_Source`, at most two kinds.
The source label is registered as classification-only in the effective backend
schema, not a semantic object kind. No third label is admitted by backend API v1.
Do not emit built-in AD kinds for extension-owned objects.
Declared properties map through a fingerprinted bijection to safe wire keys;
the first revision maps each local property ID to itself, rejecting reserved
`objectid`, `name`, `displayname`, `system_tags`, `environmentid`, `collected` and
case-colliding keys. The exporter alone sets identity/display fields from the
public graph definition; they are not alternative source identity. See
[OpenGraph nodes](https://bloodhound.specterops.io/opengraph/developer/nodes).

Edges always use exact object-ID matching on both endpoints; no name/property
matching or placeholder-node creation. Endpoint existence and kind membership
are checked before export and again after ingest. Only registered directed
relationship kinds are emitted. The wire payload includes no ORI oracle,
recipe answer, proof fixture or private registry in node/edge properties. See
[OpenGraph edges](https://bloodhound.specterops.io/opengraph/developer/edges).

For each domain package the framework reserves local IDs `DomainRoot` and
`DomainMemberOf` (authors cannot redeclare them). The effective registry/lock adds
`namespace::DomainRoot` with no domain properties and `principal=false`, and
`namespace::DomainMemberOf` from every ordinary owned kind to DomainRoot,
direct/nontraversable with no properties. For each authority with ordinary nodes,
one root has that authority, kind `namespace::DomainRoot`, and literal source ID
`ori-domain-root`; it uses the same canonical ID algorithm. Every ordinary node
gets one DomainMemberOf edge to its authority root, regardless of other edges.
Empty authorities produce no root. Global/per-kind generation limits include
these additions. Bridge packages add neither helpers nor source labels because
they own no nodes. The effective schema reserves the wire source label too and
checks it against built-ins and every installed kind before any upload.

The helper definitions, source-label registration and wire mappings are hashed
in the effective registry and backend profile. Recipes cannot target helpers in
v1 graded populations or use their edges as attack-route steps. The public
AcceptanceSpec identifies this metadata-only classification where relevant;
ordinary graph-attested supporting context remains governed by the explicit
extra-evidence policy. Both example domains receive this exact root machinery.

Export extension-owned subgraphs and cross-domain links as separate payloads.
The owned payload may register its source kind; the link payload has no
`metadata.source_kind`, does not redeclare foreign nodes, and references existing
IDs only. Omitting `metadata.source_kind` does not undo previous source-kind
registration; the adapter verifies actual resulting ownership, not just the
payload field. ORI provenance remains in sidecars, never extra wire `metadata`
fields. Root nodes and helper edges are included in graph certification counts.
Source-kind behavior and limitations
are documented in the official [metadata reference](https://bloodhound.specterops.io/opengraph/developer/metadata)
and [OpenGraph FAQ](https://bloodhound.specterops.io/opengraph/faq).

Registration/compile/readiness never upload, install schemas, clear data or
migrate database engines. Operator-controlled ingest uses a dedicated synthetic
environment and a reviewed target/payload list. Exact live verification compares
identities, kinds, properties, direct/derived facts and source ownership against
the complete generated graph. Before, between and after track gates remain
independent; unknown objects, drift and incomplete pagination invalidate evidence.

## 8. Certification and failure policy

Certification stages are `registered -> schema_valid -> offline_certified ->
live_certified -> candidate`. They are not success-shaped placeholders. Every
stage binds its predecessor's hashes; changing a dependency invalidates promotion.
The same fixture crosses actual compiler, submission schema, Direct/native MCP
projector, EvidenceIR normalization and comparator boundaries. A hand-constructed
EvidenceIR-only pass does not certify native projection.

Required fixture families, with explicit per-claim applicability explanations:

1. Perfect proof, wrong answer, partial proof, empty valid result and invalid output.
2. Unknown/ambiguous identity; cross-authority same-name collision; renamed alias.
3. Reversed edge, unknown kind, wrong endpoint type, fabricated property and
   truthful connected versus disconnected extra evidence.
4. Exact set, duplicate/missing page, changed population, count mismatch,
   unstable ordering and zero-node/nonempty-literal response.
5. Within/outside hop bounds, irrelevant selector and missing derivation premise.
6. Missing native resources, malformed response, policy rejection, transport error
   and an attempted mutating call never reaching the server.
7. Changed schema, implementation bytes, backend normalization, capability profile
   or dependency hash; stale certificate/readiness/resume refusal.
8. Model-free readiness, oracle sentinel isolation, malicious observation text and
   public-export redaction; successful and interrupted graph-gated publication.

No fixture identifier can be unknown or multiply owned. An inapplicable fixture
needs a typed reason; missing coverage blocks candidate promotion. Qualification
uses seeds 67, 1234 and 4401, repeated generation for every seed, both tracks,
every admitted native projector and offline/live graph parity. Examples do not
count toward the official 60/60 pool. Model-in-the-loop qualification remains
local-first; hosted provider canaries/final campaigns retain existing spend gates.

Registration/compiler codes are `EXT_SCHEMA_INVALID`, `EXT_UNKNOWN_ID`,
`EXT_DEPENDENCY_MISMATCH`, `EXT_NAMESPACE_COLLISION`, `EXT_IDENTITY_COLLISION`,
`EXT_ENDPOINT_INVALID`, `EXT_PROPERTY_INVALID`, `EXT_DERIVATION_INVALID`,
`EXT_LIMIT_EXCEEDED`, `EXT_RECIPE_COVERAGE`, `EXT_FIXTURE_COVERAGE`,
`EXT_CAPABILITY_UNSUPPORTED`, and `EXT_STALE_EVIDENCE` (all nonretryable until
inputs/code change). Runtime additionally uses existing typed provider, policy,
proof, invalid-output, infrastructure and harness failures. Raw unexpected
exceptions become nonrankable harness failures, never model errors. Do not retry
a semantic failure as infrastructure or use a different backend implicitly.

V3 negative reason codes are qualified IDs in `negative_reasons` or explicit
imports. Their registered predicate consumes only the declared bounded claim and
observed proof. Public meaning and predicate version are included in AcceptanceSpec;
the oracle cannot supply a secret condition. Undeclared/inapplicable reason codes
in claims, submissions, EvidenceIR or fixtures fail validation. Existing AD-specific
V2 reason codes stay in the V2 reader and later AD compatibility package.

## 9. Versions, fingerprints and migration

Semver describes compatibility; content hashes decide evidence validity. Patch:
documentation/display correction with no semantic change. Minor: additive kind
or recipe with unchanged existing interpretation. Major: changed identity,
direction, meaning, required property, normalization or completeness contract;
removal/rename of a registered ID also requires major. Framework API and protocol
versions are separate from domain versions. No certificate is automatically
grandfathered by a patch/minor label.

| Change | Required invalidation |
| --- | --- |
| Any package/dependency byte, including documentation | Extension lock and certificates/readiness/resume; generated graph bytes may compare equal but must be rebound |
| Generator, identity or wire normalization | Regenerate graph and IDs as applicable; recompile claims/oracles and all evidence |
| Kind/relationship/schema/derivation semantics | New major domain version; all graph/claim/oracle/projector/fixture qualification |
| Recipe, AcceptanceSpec, submission schema or comparator | New task/oracle/candidate release; no previous-result official reuse |
| Projector, native server revision, discovered tools/prompts/resources | New capability/projector certificate, readiness and output root; reuse graph only after exact verification |
| Selected subset, execution bounds, model settings, seed or budget policy | New release/campaign fingerprint and output root; never resume mixed provenance |
| Presentation-only rendering | New renderer/export artifact hashes; retain unchanged certified private evidence, repeat privacy/report QA |

The lock hash feeds graph generation provenance, public claim/task fingerprints,
private oracle binding, fixtures, native projection receipts, certification,
candidate release, readiness, task checkpoints, track completion, reports and
resume admission. Source hashing covers all transitive extension/framework code,
dependency locks and installed package bytes, not a handpicked subset of files.
Equivalent graph hashes do not prove equivalent identity or interpretation rules.

Post-conference migration order is fixed: strict schemas/registry and inert lock;
domain-neutral graph/identity layer; new V3 claims/oracle interfaces; approved
generators and wire adapter; native projector contracts; fixture certification;
release/campaign/report integration; then an opt-in AD compatibility domain.
The AD migration has a separate golden-byte/score/failure-equivalence gate and
never rewrites historical V2 artifacts. Unknown versions fail with actionable
errors. There is no automatic downgrade, unknown-kind-to-AD mapping or legacy
alias fallback. Old environments need no extension installation to run V1/V2.

## 10. Two vendor-neutral end-to-end contract walkthroughs

These are developer design examples, not benchmark tasks or publishable model
evidence. They contain no instantiated task prompt, answer, query or sealed oracle.
Actual fixture graph instances, expected answers and native response bodies stay
in scorer-private qualification artifacts. The public spec describes the complete
processing contract without exposing those values.

### A. Access service: bounded delegation route

`org.ori.examples.access` defines Principal, Role and Resource; AssignsRole
(Principal to Role), DelegatesRole (Role to Role), and GrantsAccess (Role to
Resource). `active` is a required boolean on grants. Delegation is directed;
same-name roles in different authorities are different objects. A recipe declares
a source Principal role, a target Resource role, a public maximum hop count and
an active-grant requirement. It supports Direct and MCP route claims, with an
explicit ordered endpoint and connected-supporting-evidence policy.

The generator uses seed 67 and bounded role counts to produce a graph containing
positive, decoy, reversed and out-of-bound cases. Registration resolves every
kind/recipe; generation validates endpoints and serializes the owned payload.
Compilation emits one AcceptanceSpec and a schema; the oracle builder derives
the accepted route family privately from that same claim. The Direct projector
must observe an actual path; each MCP projector must map its native path receipt
without relabeling tools. A high-level response listing role names alone is
inconclusive. The certifier replays perfect/reversed/missing-edge/wrong-authority/
inactive-grant/out-of-bound fixtures, verifies the live graph, and promotes only
supported track contracts. Selection includes it only if its public semantics
are unique and quotas remain satisfied. Public reporting emits typed outcomes
and separate track metrics, not the witnessed path or native response.

### B. Workload plane: exact bounded resource window

`org.ori.examples.workload` defines Tenant, WorkloadIdentity, Queue and WorkerPool;
OwnsQueue (Tenant to Queue), MaySubmit (WorkloadIdentity to Queue), and ExecutesOn
(Queue to WorkerPool). `enabled` is a required queue boolean. The recipe's public
population is queues owned by a declared Tenant and eligible for a declared
WorkloadIdentity, with explicit enabled filtering, distinctness, ascending ordering
by canonical object ID, offset 0 and limit 20. This is an exact deterministic
window, not a claim to enumerate every eligible queue. No GitHub repository, cloud vendor
or particular scheduler is assumed.

The generator uses seed 1234 and includes duplicate display names across tenants,
an inactive queue and unrelated queues. The sealed oracle computes the exact
window identities; the public contract supplies all filters and window bounds,
never its answer cardinality. Direct evidence is the certified entity projection;
native MCP evidence must establish that same population, ordering and exact
window. A full 20-row window proves its own declared coverage without a global
total. A shorter window requires a certified exhaustion marker or an exact
claim-bound backend query receipt proving that no eligible rows remain in that
window; absence of a next-page field alone is not exhaustion. An unavailable
resource is recorded as unavailable, not replaced
with an invented queue list. Missing/duplicate pages, changed-tenant counts and
scalar-only nonempty responses cannot establish complete proof. Both tracks
cross all applicable fixtures and graph gates before candidate promotion; release
selection and public report redaction are identical to walkthrough A.

A future cross-domain bridge is a third explicitly approved package importing
both exact locks. Its relationship links distinct identities; it cannot mutate
their identity domains or add source ownership to foreign nodes. Bridge tasks
must expose the imported relationship semantics and satisfy both domains'
fixture/capability coverage. The first two walkthroughs do not depend on a bridge.

## 11. Design acceptance and post-conference test contract

Before the talk, architecture and BloodHound-domain reviewers must independently
check sections 3–12 against this matrix. Review findings must be resolved in the
spec; a review is not an executed implementation test. Design completion does
not advance P3–P9 model, release or campaign gates.

| Gate / future test group | Exact acceptance |
| --- | --- |
| `extension_registry` | Reject unknown fields/IDs, corrupt file hash, path escape, symlink, conflicting namespace, duplicate dependency and dependency cycle before loading code |
| `extension_identity` | Same input bytes produce same ID; authority/kind/namespace changes differ; display-only changes retain ID; ambiguous aliases and collisions fail |
| `extension_graph_schema` | Strict property types/nullability, endpoints, direction, duplicate conflicts, derivation DAG/limits; no hidden AD normalization |
| `extension_generation` | Repeated 67/1234/4401 outputs byte-identical; changed seed changes graph and selected subset; no wall-clock/global-RNG/network dependency |
| `extension_claim_oracle` | Public clauses mechanically cover every scored constraint; unknown recipes or fixtures fail; oracle sentinels never reach provider envelopes |
| `extension_native_projection` | Each admitted native revision replays its own tool/resource receipts; no facade, invented facts or fake resource availability; unsupported shapes stay inconclusive |
| `extension_certification` | Every applicable fixture crosses real adapter and comparator; all exact offline/live graph gates agree; unknown/missing coverage blocks promotion |
| `extension_selection` | At least 60 unique certified contracts per track for the OAIC policy; exactly 50/50 chosen deterministically; no namespace/alias duplicates counted as new semantics |
| `extension_resume` | Every changed lock/profile/claim/graph/runtime component rejects old checkpoints; no provider calls during readiness |
| `extension_publication` | Invalid cells excluded from ranking; Direct/MCP/local-hosted separation retained; no secrets, private paths, prompts, answers, queries, tool bodies or sealed data in exports |
| `extension_portability` | Clean Linux/macOS CLI installation and arbitrary config/output paths; no personal service-manager/agent dependency; external driver uses identical commands |
| `extension_v2_compatibility` | Existing V1/V2 golden artifacts and error contracts unchanged; V3 requires explicit protocol selection and fresh output roots |

Stop for any unresolved semantic meaning, unknown namespace, unsupported wire
schema or unprovable native receipt. Do not turn a backend limitation into a
hidden scorer assumption. Broader extension implementation may begin only after
the conference and a separately approved implementation work package.

## 12. Normative encoding and record appendix

These are specification rules, not implementations added to ORI. They apply only
to the future extension/V3 domain and never alter existing V2 hashing.

### Canonical hash preimage: `ori-extension-canonical-v1`

First validate the input against its strict record schema. Then transform every
value recursively into a tagged JSON tree `T`:

| Input | Tagged value |
| --- | --- |
| null | `["null"]` |
| boolean | `["boolean", value]` |
| integer | `["integer", decimal_string]`; no plus sign/leading zeros; zero is `"0"` |
| binary64 number | `["number", bits]`; `bits` is exactly 16 lowercase hex digits of its big-endian IEEE-754 encoding; NaN/infinity forbidden |
| string | `["string", value]`; no normalization by the encoder |
| ordered sequence | `["array", [T(item), ...]]` |
| string-keyed object | `["object", [[key, T(value)], ...]]`, keys sorted by Unicode scalar value sequence |

Boolean detection precedes integer detection. The schema defines integer versus
number; a `number` property converts a JSON numeric value, excluding boolean, to
binary64 with nearest/ties-to-even rounding before tagging. Integer properties
must be integral JSON values within signed-64-bit bounds and are never rounded.
Unsigned seed fields permit 0 through 2^64-1. No unpaired Unicode surrogates or
duplicate object keys are allowed. Negative binary64 zero retains its sign bits.
Typed tagging prevents boolean/integer/number and missing/null collisions.

Serialize the resulting tree as UTF-8 JSON with no BOM, whitespace or trailing
newline. Strings use literal Unicode except quotation mark/backslash and control
characters: escape quote/backslash, use `\b`, `\t`, `\n`, `\f`, `\r` for those
five controls, and lowercase `\u00xx` for remaining U+0000–U+001F. Do not escape
slash or other Unicode scalars. Hash those exact bytes with SHA-256. SHA-256 of
raw package files and implementation bytes remains a raw-byte hash, not `T`.
Record fingerprints exclude only the specifically declared own digest field;
arbitrary path exclusions are not part of the extension API. The ordinary JSON
artifact remains human-readable; only the hash preimage uses the tagged tree.

### RNG and archive encoding

Let `K` be the raw 32-byte hash of the canonical stream record from section 5.
Counter starts at zero; block `i` is raw SHA-256 of
`UTF8("ori-extension-rng-v1") || 0x00 || K || uint64_big_endian(i)`.
Read each block left-to-right as four unsigned big-endian 64-bit words. For a
draw in `[0,n)`, require integer `1 <= n <= 2^64`, set
`L = floor(2^64 / n) * n`, discard words `>= L`, return the first remaining word
modulo `n`. Each component owns its stream/counter; do not share counters across
components. Counter overflow fails with `EXT_LIMIT_EXCEEDED`. Shuffle uses
descending Fisher–Yates, drawing `[0,i+1)` at step `i`; selection uses a separate
`component_id="selection"` stream and ascending qualified semantic-class IDs
before shuffling. Generation uses `component_id="generation"` or an explicitly
declared subcomponent ID; no implicit stream-name concatenation.
The release selector uses namespace `org.ori.core.release`, the campaign seed,
and its exact selection-policy version, independent of the root domain package.

For archive v1, use ZIP_STORED (method 0), not environment-dependent compression.
Member names are ASCII relative paths; sorted by raw ASCII bytes. Fixed timestamp
is 1980-01-01 00:00:00. No directory entries, encryption, data descriptors, extras,
comments or ZIP64; known sizes/CRC32 go in headers. Version-needed is 20,
version-made-by is Unix/20, flags and internal attributes are zero, external
attributes are `0100644 << 16`, and local/central names are identical. Files are
UTF-8 ordinary JSON with sorted keys, no extra whitespace and one trailing newline;
number serialization uses the backend profile's pinned serializer implementation.
Archive payload total is limited to 1 GiB and each member to 256 MiB; exceeding
either bound fails rather than switching formats. Pin serializer code because
wire JSON and typed hash preimages intentionally have different representations.

### Complete nested record fields

All fields below are required unless explicitly nullable. IDs resolve through the
immutable registry; SHA-256 fields are 64 lowercase hex characters. Arrays are
sorted by `id`, `namespace`, or `relative_path` as applicable; tuples/streams keep
declared order. Duplicate sort keys fail. Unknown fields fail. The API version,
not caller convention, owns every field meaning.

| Record | Fields and constraints |
| --- | --- |
| Recipe declaration | `id, version, implementation_id, content_sha256, template_ids, supported_tracks, claim_kind, family, generator_requirements, parameter_schema_id, parameter_schema_sha256, track_exclusions, fixture_ids`; claim kind is one of the five in section 6; tracks are direct/mcp; `generator_requirements` is a sorted list of required owned/imported kind IDs; each exclusion is `{track, reason}` and cannot overlap supported tracks |
| Fixture declaration | `id, version, implementation_id, content_sha256, claim_kinds, tracks, applicability_predicate_id, applicability_predicate_sha256`; predicate returns applicable or a typed declared reason, never an implicit skip; expected values remain scorer-private files |
| Backend profile | `id, backend_implementation_id, backend_version, image_sha256, graph_driver, wire_schema_sha256, serializer_id, serializer_sha256, live_projector_id, live_projector_sha256, normalization_id, normalization_sha256, required_capabilities, source_label_policy`; graph driver is `postgresql` and source label policy is literal `owned-plus-link-v1` for first certified BloodHound profile; wire schema map has exactly node/edge/metadata/extension digests |
| Required capability | `{id, version, contract_sha256}` from an approved backend registry, e.g. exact ID lookup, registered labels and bounded snapshot pagination; unknown IDs fail |
| Projector declaration | Fields in section 3; supported surfaces are `direct_result`, `tool_result`, `resource_read`; prompt discovery is provenance, never an evidence surface by itself |
| Node record | `object_id, kind, authority, source_id, canonical_name, aliases, properties`; name may be null; property map obeys node-kind schema; IDs are verified by recomputation |
| Edge record | `source_object_id, relationship_id, target_object_id, properties, derivation_receipt_id`; receipt ID nullable only for direct edges; property map obeys relationship schema |
| ExtensionLock | `schema_version="ori-extension-lock-v1", protocol="ori-eval-protocol-v3", root_package_sha256, packages, effective_registry_sha256, implementations, backend_profiles, capability_profiles, lock_sha256`; packages are dependency records plus file inventories; implementation records are `{id, api, code_sha256, dependency_closure_sha256}` |
| ValidatedDomainGraph | `schema_version="ori-domain-graph-v1", extension_lock_sha256, nodes, edges, derivation_receipts, graph_sha256`; no mandatory AD domain/SID |
| GeneratedDomain | `graph, wire_payloads, provenance`; payload records are `{relative_path, sha256, owning_namespace, role}` with role `owned` or `link`; provenance is `{extension_lock_sha256, generation_spec_sha256, generator_implementation_sha256, serializer_sha256}` |
| GenerationSpec | Fields in section 5 plus `authorities` (sorted unique IDs) and `dependency_graphs` (sorted immutable ValidatedDomainGraph inputs keyed by dependency lock hash); per-kind counts map qualified kinds to nonnegative integers; hard totals at most 100,000 nodes and 1,000,000 edges, including helpers; requested lower limits bind too |
| RecipeInstance | `recipe_id, recipe_version, extension_lock_sha256, parameters, role_bindings`; parameters validate against the registered schema; bindings map declared roles to public identity-lookup records |
| PublicGraphContext | `extension_lock_sha256, graph_sha256, public_registry_sha256, role_bindings, execution_bounds`; no complete graph, oracle, solution registry or expected facts |
| PublicClaimContext | `extension_lock_sha256, task_id, public_claim, acceptance_spec, submission_schema, execution_bounds`; no sealed fields |
| ClaimContract | `schema_version="ori-extension-claim-v1", extension_lock_sha256, recipe_instance_sha256, public_claim, acceptance_spec, submission_schema, execution_bounds, claim_sha256`; exact V3 claim schemas keep the V2 five-claim algebra and replace kind/reason references with qualified registry IDs |
| SealedGraph | `graph, fact_registry_sha256, derivation_proofs`; only the oracle builder/certifier receives it; graph facts themselves remain observable through admitted runtime surfaces |
| OracleArtifact | `schema_version="ori-extension-oracle-v1", extension_lock_sha256, claim_sha256, graph_sha256, oracle_builder_sha256, private_oracle, oracle_sha256`; private oracle retains versioned typed answer policy and witness/negative-fact forms, never arbitrary grader code |
| ProjectionReceipt | `schema_version="ori-extension-projection-v1", extension_lock_sha256, claim_sha256, projector_sha256, capability_profile_sha256, observation_refs, evidence, completeness, policy_result, error, receipt_sha256`; completeness is complete/incomplete/not_applicable; policy result admitted/rejected/not_applicable; error nullable, evidence nullable on failure |
| CertificationCatalog | `schema_version="ori-extension-certification-v1", extension_lock_sha256, claim_catalog_sha256, oracle_catalog_sha256, fixture_inventory_sha256, graph_receipts, implementation_closure_sha256, cases, state, catalog_sha256`; every case has `{fixture_id, track, projector_id, status, receipt_sha256, error}`; status pass/fail/inapplicable; inapplicable requires declared reason in error |

All three entrypoint IDs are nonnull for both package kinds. A bridge's approved
generator consumes the immutable dependency graphs in GenerationSpec and emits
only its declared linking edges, never new or rewritten nodes. Its implementation
and any derivation rules are pinned and certified like a domain generator.
Bridge output graph limits
include imported nodes for verification, but no imported object is regenerated.
The global merged graph deduplicates identical dependency objects by canonical
ID and full fact equality; conflicts fail. Bridge `GenerationSpec` per-kind
counts is empty and authorities must equal the locked dependency authority union.

The solver receives only PublicClaimContext plus admitted native discovery/tools.
Public result export uses its own allowlist of task IDs, fingerprints, typed
outcomes and metrics; it never dumps any of these private records wholesale.

### Nonsecret encoding test vectors

These synthetic serialization/RNG vectors are not task instances or oracle
answers. They define interoperable bytes for future implementations. They were
calculated with a standalone encoding check, not an installed extension engine.

```json
{
  "identity_input": {
    "identity_version": 1,
    "namespace": "org.ori.examples.access",
    "kind": "org.ori.examples.access::Principal",
    "authority": "example-tenant",
    "source_id": "account-1"
  },
  "object_id": "oriog-0c055daef4cf97238a67348be6ba91ca657ec1fec3166ce98db9c7bd84d633d5"
}
```

```json
{
  "stream_input": {
    "seed": 67,
    "namespace": "org.ori.examples.access",
    "component_id": "generation",
    "version": "1.0.0"
  },
  "stream_digest_hex": "8fa1bfcfaa671490337642e1700a33464a6e3d659ad08aa9981582b9a7a364bb",
  "counter_zero_block_hex": "3fec55eb3cd175df963de686666c92f2a4ced6db82d1f9c7690f4c26407d569d",
  "first_six_draws_n10": [3, 8, 5, 1, 1, 4]
}
```

Design QA must parse both blocks, recompute the identity/stream/block hashes and
draws, and compare exactly. Future implementation tests additionally cover
boolean versus integer versus number, non-ASCII keys, signed zero, rejection
sampling and integer bounds; passing these two vectors alone is not certification.
