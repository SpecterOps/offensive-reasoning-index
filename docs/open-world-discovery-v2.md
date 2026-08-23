# Open-World Discovery V2

Open-world discovery measures whether a solver can find attack routes without
being handed one target per prompt. The V2 surface is an offline compiler,
preflight, and grader layered on ORI's V28 correctness contracts. It does not
launch a model, upload a graph, or provide a callback-based execution bypass.

## Trust boundary

Discovery compiles from four already matched inputs:

- a standard ORI generated manifest;
- its exact SharpHound ZIP;
- a V28 public task artifact; and
- the matching scorer-only V28 oracle artifact.

The archive is rebuilt as a canonical V28 graph snapshot and must match the V28
graph fingerprint. Discovery truth comes only from closed or exact V28 route
oracles. Relationships must have a supported SharpHound wire carrier. Internal,
derived, or unknown relationships fail compilation.

The compiler writes separate artifacts. The public artifact contains the
campaign scope, answer schema, graph binding, pinned track capability profile,
and execution bounds. It contains no truth target IDs, route variants, oracle
fingerprints, or expected answers. The private artifact contains the sealed
identity catalog, graph fact registry, and route objectives.

## Compile and preflight

First compile the ordinary V28 track with `ori compile-v2`. Then compile the
discovery layer over that exact pair:

```bash
uv run ori discovery compile \
  --manifest datasets/benchmarks/complex-v1-seed-4401_manifest.json \
  --archive datasets/benchmarks/complex-v1-seed-4401.zip \
  --v2-public results/v2/complex-direct-seed-4401-public-v2.json \
  --v2-oracles results/v2/complex-direct-seed-4401-oracles-v2.private.json \
  --output-dir results/discovery/complex-direct-seed-4401

uv run ori discovery preflight \
  --public results/discovery/complex-direct-seed-4401/complex-direct-seed-4401-discovery-public-v2.json \
  --private results/discovery/complex-direct-seed-4401/complex-direct-seed-4401-discovery-oracles-v2.private.json \
  --output results/discovery/complex-direct-seed-4401/preflight.private.json
```

Preflight revalidates canonical fingerprints, public/private pairing, the
pinned direct or MCP capability profile, answer-schema bounds, sealed identity
coverage, graph-attested truth, and wire relationship support.

## Submission and grading

The answer is one strict JSON object. Each finding contains a route and the
evidence edges that support it. Object IDs may be replaced by aliases from the
sealed V28 identity catalog. Relationship spellings are normalized only through
ORI's canonical relationship registry.

```json
{
  "schema_version": "ori-discovery-submission-v2",
  "protocol_version": "ori-open-world-discovery-v2",
  "public_artifact_fingerprint": "<64 lowercase hex characters>",
  "findings": [
    {
      "finding_id": "candidate-001",
      "route": [
        {
          "source_id": "<object ID or alias>",
          "relationship": "MemberOf",
          "target_id": "<object ID or alias>"
        }
      ],
      "evidence_edges": [
        {
          "source_id": "<object ID or alias>",
          "relationship": "MemberOf",
          "target_id": "<object ID or alias>"
        }
      ],
      "confidence": 0.9
    }
  ]
}
```

Grade without a model call:

```bash
uv run ori discovery grade \
  --public <discovery-public-v2.json> \
  --private <discovery-oracles-v2.private.json> \
  --submission <submission.json> \
  --output <score.json>
```

The grader rejects oversized, malformed, duplicate-key, extra-field, and
out-of-bound output. A finding matches only when its complete canonical route
matches a truth variant and its evidence covers the route with graph-attested
facts. Unknown identities, ambiguous aliases, unsupported relationships,
disconnected paths, missing evidence, and hallucinated graph facts are false
positives.

All variants with the same source and destination are one discovery objective.
Finding a second valid variant does not add recall. A route that is only a
strict contiguous subpath of a retained objective is redundant rather than a
separate required target. Redundant valid findings are excluded from the
precision denominator; actual false positives reduce precision. The reported
score is F1 over objective recall and false-positive-aware precision.

Public reports expose only aggregate counts and digests of submitted findings.
They never identify missed truth targets or return sealed route IDs.

## Execution integration

Discovery intentionally has no `run` command. A future model runner must bind
to the capability profile and `ExecutionBounds` in the public artifact and use
the same V28 direct coordinator or MCP runtime/receipt path as normal V28
campaigns. Until that integration exists, an external agent may produce the
strict submission file, but the discovery package itself remains compile,
preflight, and offline grade only.
