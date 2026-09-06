# S1-04D — model-card public string admission

Revision 3, September 6. Status: complete; independent implementation, full
regression and evidence review passed. Bounded F01 closure only.

## Entry, ownership and dependencies

S1-04C is independently complete: 1,108 tests and 176 subtests, unchanged frozen
card outputs and a reconciled 160-file inventory. Start from that dirty consolidated
tree; preserve every unrelated change. No remote Git/signing/PR, provider/model,
live graph, host, service or dependency-install operation is part of this slice.

Root owns `src/ori/eval/v2/model_card.py`, supervisor-contract documentation,
planning/progress and ignored evidence. The test worker owns only
`tests/test_v2_model_card.py` after snapshot approval. Independent reviewer owns
challenge, snapshot, implementation and evidence review. No shared schema,
runner, provider, scorer, compiler, graph or certification edits.

## Requirement and limits

An accepted model card must not emit strings containing the explicit unsafe
patterns below, regardless of whether the string came from a display override,
model identity, revision, fingerprint field or nested list. Reject before rendering
or creating output assets. Do not rewrite or substitute identities, scrub output
after hashing, repair input files or invent a public alias.

Preserve legitimate slash-qualified identifiers such as
`poolside/laguna-s-2.1`. An unmarked token such as `organization/model` cannot be
classified as a filesystem path by syntax alone. Such names remain allowed;
operators remain responsible for selecting non-sensitive public names. This is
bounded pattern admission, not universal secret detection or a guarantee against
arbitrary encoded or intentionally disguised private content. Do not inspect the
filesystem, DNS, environment or credentials to classify a label.

No behavior change to campaign execution, evidence admission from S1-04C, metrics,
schema `ori-v30-model-card-v3`, filenames, JSON keys or SVG escaping. Valid frozen
inputs must still produce byte-identical JSON/SVG outputs. Previously accepted
explicit unsafe labels now fail closed. Private evidence stays untouched and
readable by its existing tools; operators may choose a safe display override for
an unsafe display name, but an unsafe underlying public identity cannot be hidden
by that override. Use a new legitimate public campaign identity for future runs;
never rewrite completed evidence to force a card through validation.

## Exact implementation proposal

Keep `_public_label(value, label)` and its existing strip/nonempty behavior. Add
one reusable non-mutating `_assert_public_string(value, label)`. `_public_label`
must validate the original value first, then strip, then perform the existing
empty check and return. Existing error categories
for empty, control, local-path and secret material stay; add a URL category.
Error text contains only the supplied fixed field/category label, never the input.

String admission, in this order:

1. Reject characters in Unicode general categories `Cc`, `Cf`, `Cs`, `Zl`, `Zp`.
   Check the original value before stripping, so leading/trailing controls cannot
   disappear. Ordinary Unicode letters, accents and spaces remain unchanged.
2. Reject URL syntax matching a case-insensitive ASCII scheme followed by `://`
   anywhere, or explicit `http:`, `https:`, `file:`, `ftp:`, `sftp:`, `ssh:`, `ws:`,
   `wss:`, `mailto:`, `data:` at a non-word boundary or start. Scheme matching is
   textual, not a network lookup. Scheme-letter matching is ASCII, but the
   non-word boundary is Unicode-aware: `édata:model` is an identifier, not a
   `data:` scheme token. `Poolside: Laguna S 2.1` remains valid.
3. Reject any backslash, leading slash, any slash immediately preceded by
   whitespace or one of `([{'\"=:,;)]}|<>` (recognized embedded absolute-path
   boundaries, not a detector of every possible path), and explicit
   dot/home path segments: `.` or `..` or `~` immediately followed by slash at
   start or after whitespace, slash or one of `([{'\"=:,;)]}|<>`. Retain Windows absolute
   drive detection. Namespace tokens and model tags remain allowed. This rule
   rejects `../private`, `./private`, `~/private`, `org/../private`, embedded POSIX
   paths, UNC and Windows paths without opening anything.
4. Retain existing credential assignment detection; extend the `sk-` prefix
   rejection to a token boundary (start or any preceding non-word character),
   so `Model sk-synthetic` cannot bypass it. Do not add a claim to detect every
   provider's credential format or arbitrary bare secrets.

Add `_assert_public_strings(value)` which recursively visits string values in
dicts/lists/tuples, also checking string mapping keys, with the fixed label
`public card string`. Non-string scalar values are unchanged. Unsupported types
are not introduced by this slice; existing JSON serialization remains authoritative.
Call it on the complete card dictionary immediately before `_svg_bytes(card)`.
At that point all input-derived output strings are present. Later generated asset
keys are fixed constants and values/evidence fingerprints are fresh SHA-256 hex.
Do not use `_public_label` recursively: its stripping must not change source
fingerprints or names. Keep existing selected model lookup behavior intact.

The read-only output-string inventory is an entry review artifact: reviewer must
confirm this single pre-render boundary covers every input-derived string and
dynamic key actually emitted. Do not assume only fields named `label` are public.

The completed read-only inventory identified these exact input-derived surfaces:
top-level product/provider/model/display name, MCP revision, source-config/graph/
readiness fingerprints; per-track catalog/candidate/live/graph-before/graph-after/
completion fingerprints, public-report fingerprint arrays and locally generated
report/state SHA arrays. Runner version is a schema literal; resource modes,
warnings and other string fields are source constants. There are no input-derived
dynamic keys. SVG consumes display name, provider, graph prefix and the runner
literal; HTML escaping remains mandatory but does not establish privacy.

Inventory review suggested strict hexadecimal reference-field grammars. This
proposal intentionally does not add them: current schemas use plain strings and
the supported synthetic fixtures include `candidate-direct` and `live-direct`.
The bounded F01 requirement rejects explicit unsafe syntax across all these
fields without inventing stricter evidence schemas. Likewise retain the existing
abbreviated revision `92a37dd`; lexical privacy is not proof of a full revision pin.
Independent challenge must approve this compatibility decision explicitly.

## Snapshot and baseline

Before edits, make complete byte-verified copies of model-card implementation,
its test file and supervisor contract under ignored `results/p2-slices/s1-04d/`.
Record all 160 source files, source membership, base commit, original 17 collected
IDs and complete original test bodies using the existing snapshot-v1 convention.
Independent reviewer verifies copies before implementation.

Reuse the complete frozen S1-04C synthetic input bundle by a private byte-identical
copy, capture all copied hashes and baseline JSON/SVG with unchanged production.
Do not regenerate timestamps. Include a safe display override baseline for
`Poolside: Laguna S 2.1`. No synthetic fixture is certified campaign evidence.

## Exact tests and controls

Preserve all original test IDs and bodies. Add three collected scenario functions:

1. `test_public_string_admission_patterns`: named subtests, each independent and
   crossing `_public_label`, not only a regex. The exact finite map below is
   frozen privately before controls. Rejections require category-only errors,
   absence of the entire synthetic value from displayed error text, and no
   normalization of accepted non-whitespace names.
2. `test_model_card_rejects_unsafe_public_strings`: start from immutable complete
   campaign bytes per case and cross `build_model_card`. Three original synthetic
   display cases (`https://private.example.invalid/service`, `../synthetic-private/run`,
   `Model /private/synthetic-private/run`), plus one unsafe `mcp_server_revision`
   (rehash readiness), plus a nested candidate-release string (change matching
   provenance/report/track-readiness/track-completion fields and rebuild every
   containing hash/pointer/lifecycle reference). All individual artifacts must
   validate and normal cross-file evidence admission must succeed before public
   string rejection. Output directory is fresh and must remain absent.
   For the URL display case also cross the actual CLI; both stdout/stderr must
   omit the synthetic URL and return failure. Error tracebacks must not restore
   the input value through exception chaining.
3. `test_public_string_recursive_boundary`: construct small independent dict/list/
   tuple payloads containing unsafe strings as dict value, nested list item,
   nested tuple item and mapping key. Require rejection for each; accepted
   namespace/model values remain byte/structure identical. These helper tests
   supplement, not replace, actual file-reader builds above.

Pattern map (ordered):

| Category | Exact values |
| --- | --- |
| Empty | empty string; three spaces |
| Control | `safe\x00name`; `safe\rname`; `safe\nname`; `\tsafe`; `safe\x7f`; `safe\u202ename`; `safe\u2028name`; `safe\ud800name` |
| URL | `https://private.example.invalid/service`; `Model custom+scheme://host`; `FILE:/private/model`; `mailto:synthetic@example.invalid`; `Model data:text/plain,synthetic` |
| Path | `/private/model`; `../private/model`; `./private/model`; `~/private/model`; `Model /private/model`; `Model (/private/model)`; `org/../private`; `C:/private/model`; `C:\\private\\model`; `\\\\server\\share`; `org\\model`; `Model,/private/model`; `Model;/private/model`; `Model)/private/model`; `Model]/private/model`; `Model}/private/model`; `Model` followed by pipe and `/private/model`; `Model</private/model`; `Model>/private/model` |
| Secret | `api_key=synthetic`; `Model password:synthetic`; `sk-synthetic`; `Model sk-synthetic` |
| Accepted | `poolside/laguna-s-2.1`; `Qwen/Qwen3.8-27B`; `org/model:latest`; `org/team/model`; `Poolside: Laguna S 2.1`; `Modèle local`; `ORI (local)`; `  Model Name  ` returning `Model Name`; `édata:model` |

This is 47 pattern subtests, five full-build subtests and five recursive subtests
(four rejections plus one unchanged positive): 57 new named obligations and three
new collected tests. Validate actual counts and correct the plan through review
if this arithmetic is wrong; do not hide or rename existing cases.

Private controls: inject a no-op recursive pre-render validator and require the
raw revision/nested release cases to fail their expected-rejection assertions;
display cases may still fail at the early label boundary and are not evidence for
the recursive guard. Separately inject a first-case setup error in the pattern
scenario and require all later cases and the final positive case to execute.
Use test-local/private monkeypatching, no permanent bypass or trust flags.

## Review, documentation and exit

Update supervisor documentation with explicit rejected patterns, ambiguous namespace
limits, operator responsibility and no evidence rewriting. Preserve the broader
audit's open graph-receipt/configured-run/recovery obligations. Independently review
full changed files, frozen map, namespace compatibility, error disclosure and
pre-render placement. Run focused tests, both frozen output comparisons, controls,
full suite, Ruff, offline lock and diff checks. Reconcile exact validation-start/
end inventory and require all unowned source bytes unchanged. Do not claim fewer
tests, faster runtime or whole-stage completion from this correctness slice.

Stop on safe-output drift, broken slash-qualified names, private error disclosure,
hidden identity rewriting, broader source edits, failed review or inability to
prove the raw-string cases reach the new boundary. Any policy change returns to
independent plan review before implementation. Merged certification and hosted
campaign/publication gates remain deferred, not waived.

## Execution evidence

Independent challenge resolved explicit punctuation delimiters and validation
before trimming. Production review then identified an ASCII-only word-boundary
regression; scoped scheme matching preserves `édata:model`, with a new explicit
positive case. Revision 3 and the resulting 57-obligation map are approved.
Snapshots cover three complete owned files, 17 original collected cases and all
160 source inventory entries. The ten synthetic baseline inputs are byte-identical
to S1-04C; default and display-override JSON/SVG pairs remain unchanged.

Focused validation passed 20 tests and 157 subtests in 7.96 seconds, retaining
all 13 original test bodies. The private first-pattern control reports one
intentional failed subtest and 46 subsequent passes through `accepted-8`.
Bypassing only the recursive validator causes exactly the raw-revision and
nested-release rejection assertions to fail; all three earlier display checks
still pass. Each control has the expected failed containing test, not an extra
independent failure. Frozen maps, logs and receipts remain ignored/private.

Full regression passed 1,111 tests and 233 subtests in 250.65 seconds. All 160
source entries match the validation-start inventory, and all 158 unowned entries
match the pre-edit snapshot. Ruff, offline lock and diff checks passed. Independent
final review reconciled the log/hash, 57 obligations, both controls, unchanged
inputs and both unchanged output pairs; bounded F01 closure is approved. Universal
secret detection and the remaining publication-audit obligations are not closed.
