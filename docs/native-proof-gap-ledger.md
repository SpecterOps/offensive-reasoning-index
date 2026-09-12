# Native MCP Proof Gap Ledger

Status: M0A source integration candidate; no native server, model provider, graph upload, or
qualification operation has been run from this integration branch.

This ledger tracks evidence-contract gaps for the fixed OAIC 2026 MCP roster.
An unsupported contract is unranked: it is not replaced, retried through a
different implementation, or converted into a model score.

| Implementation | Frozen revision | Required native evidence | Current gap / admission state | Next ORI-only action |
| --- | --- | --- | --- | --- |
| `mwnickerson` | `92a37dd481ce675fe552f14c9957a31dbbcd212e` | CE API observations that project exact nodes, directed typed edges, endpoint identity, declared properties, and mechanically complete sets/counts | Runtime/discovery/whole-roster feasibility and fixture qualification source is integrated; native diagnostics share the same evidence/lifecycle boundary. No live runtime or qualification receipt exists in this branch | Finish installed cross-package acceptance and freeze the exact source/build before creating live receipts |
| `mordavid` | `1eb21b01da14fd2eda941234e3a545e876bef296` | Both explicit `neo4j` and `bloodhound` databases; ordered route nodes, relationship type/direction, endpoint identity, and required properties | Raw route projection has not been demonstrated in a frozen native result. A count or an unordered node list is not a route proof | Inspect pinned source and sanitized fixtures. Add a projector only if the native result mechanically contains the declared facts; otherwise emit typed unsupported results |
| `armadin` | `6ad4a4703d1117c3019400539911ca689a537197` | Explicit observed home database; proof of scoped set/count/route/decision/absence result contracts | Existing evidence is limited to set and positive-route possibilities. Generic lists do not prove filtered completeness; positive route tools do not prove negative/decision claims | Map each exposed native tool to the five public claim contracts and add only fixture-certified projections. Do not add generic Cypher or alter the pinned tool catalog |

## Per-contract record format

Every feasibility or qualification implementation must append a private receipt
and a public-safe row containing:

```text
implementation_id
source_revision
task_id
public_semantic_fingerprint
claim_kind / public answer shape
required proof facts
observed native tool shape
projector/compiler limitation or intrinsic missing fact
outcome: supported | unsupported | invalid
qualification receipt fingerprint
```

The complete original 50-task selected MCP roster must be checked before an
implementation is eligible for model execution. A five-task diagnostic canary
uses the same native proof boundary and cannot make an unsupported official
cell eligible.
