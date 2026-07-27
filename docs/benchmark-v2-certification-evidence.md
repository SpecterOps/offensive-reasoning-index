# ORI benchmark correctness v2 certification evidence

Date: 2026-07-25 through 2026-07-27
Branch: `feat/benchmark-correctness-v2`
Containment boundary: `0a56029471c5426be348c61c0969724eaee38599`
Protocol: `ori-eval-protocol-v2`

This record contains deterministic harness evidence only. The certification and
readiness actions recorded here launched no GPT-5.6 Sol, GPT-5.5, local-model,
or other provider calls. Later operator-authorized development runs that
exposed runtime incidents are documented separately in the implementation plan.

## Corpus migration

| Product | Track | Legacy capabilities | V2 candidates | Blocked |
| --- | ---: | ---: | ---: | ---: |
| simple | direct | 20 | 20 | 0 |
| simple | MCP | 40 | 40 | 0 |
| complex | direct | 42 | 46 | 0 |
| complex | MCP | 62 | 70 | 0 |

The candidate count exceeds the legacy count where an oversized enumeration was
replaced by deterministic 500-object pages. Every candidate has a complete
fixture manifest or a compiler-recorded inapplicability reason bound to a
registered adversarial micrograph.

## Generator determinism

Two independent generations of each same-seed artifact were byte-identical:

| Product and seed | ZIP SHA-256 | Manifest SHA-256 |
| --- | --- | --- |
| simple 1234 | `c3e939f063fcf9501551a80f07fcddd84966838a5f5cc9bff9ba3b29cdc6f3e4` | `3a20e9a2f9199130a3fa2b29f06433f7c949627126398c4bb1eba549fc5f9c1b` |
| complex 4401 | `a00e60e0f7ba8a02bcfee74e0ef1b99d7e46107d25b5172ffb1231e51f8f5edb` | `d2dd22a037a3d8f2d4055c2792a5abee8806a43a2900519378e0b1822c314be9` |

Changed seeds produced different graph identities while retaining the same task
IDs and seed-independent contract shape:

| Product | Seeds | Track | Contract-shape SHA-256 |
| --- | --- | --- | --- |
| simple | 1234 / 5678 | direct | `6716b5147459d88098d88b6355b6fa777228e24659f049101bb352f0a3a19c83` |
| simple | 1234 / 5678 | MCP | `ec4ff308116e751824b37f3c39f65763b93e6a656c0c0fd6e3d483bc8862dd00` |
| complex | 4401 / 4402 | direct | `349825d10e069e5d8850c8effc82e752061694a6c991d85cf30bb2cef247ee69` |
| complex | 4401 / 4402 | MCP | `c69a4d8330729312d4e976722258670079c55a27b5ea595441e6f8f912ac1469` |

The graph digests correctly changed:

- simple 1234: `edcb735723deca3888ad5a3a3c419edb58e023cda7117acdbfcf08e9e0d4afba`
- simple 5678: `b85a048e999ea082eac69b2d37a13005ac36808055ac396af534faa9ed7f2977`
- complex 4401: `78ae0686047a372c273d2045abfac0f76fce03b3bb4029cb178727dd3ade65a5`
- complex 4402: `36d83be15dbfe7fc7c58e7cc2f49910f140eea941ece70b09651b4f3e3da6079`

Concrete cardinality/page limits and cost bands remain seed-specific and are
still bound by each immutable `TaskBundle`; the contract-shape digest excludes
only those resolved values.

## Controlled BloodHound certification: complex seed 4401

The operator explicitly approved replacing the simple seed-1234 graph with
complex seed 4401 on `bloodhound-ori.mwnickerson.com`. Before deletion, the
recoverable simple archive and manifest were confirmed at their frozen hashes.
The collected-graph clear returned HTTP 204, and BloodHound MCP upload job `11`
accepted the exact complex archive:

```text
complex-v1-seed-4401.zip
SHA-256 a00e60e0f7ba8a02bcfee74e0ef1b99d7e46107d25b5172ffb1231e51f8f5edb
```

After the datapipe returned to idle, MCP exposed
`GRANITEMANUFACTURING.LOCAL` and
`PARTNER.GRANITEMANUFACTURING.LOCAL`. The independent ingest verifier matched
all declared object counts and all 30/30 planted paths.

`ori certify-v2-live` then completed three bounded, read-only projections:

| Gate | Objects | Relationships | Object queries | Relationship queries | Verification fingerprint |
| --- | ---: | ---: | ---: | ---: | --- |
| pre-direct | 7,828 | 40,340 | 29 | 136 | `c76aafd3590ca3ee2840baf64d9b3532ab563f76c8b642029a000707b5292d74` |
| between tracks | 7,828 | 40,340 | 29 | 136 | `c76aafd3590ca3ee2840baf64d9b3532ab563f76c8b642029a000707b5292d74` |
| post-MCP | 7,828 | 40,340 | 29 | 136 | `c76aafd3590ca3ee2840baf64d9b3532ab563f76c8b642029a000707b5292d74` |

Expected and observed graph fingerprints were
`78ae0686047a372c273d2045abfac0f76fce03b3bb4029cb178727dd3ade65a5`
at every gate.

Live/offline parity:

| Track | Candidates | Comparator cases | Adapter cases | Mismatches | Offline certification | Live certification | Candidate release |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| direct | 46 | 348 | 376 | 0 | `1f4f1579e4df983b0ff32c72c07a29bc5e6107858d1b62e8bfc19b3403e3c514` | `fc032cbe541daf98fd4b6e7f9be78fdc71390d75730d84b90b1317b905b427ef` | `c7d9a2b02c011f69057e0a192c47ac8ea768b00e10da0acf8b348f8754aa535d` |
| MCP | 70 | 486 | 520 | 0 | `4a37f4c0dbcd186b426c7d8313455745782ce352a38ceeab39fcda773f4475d2` | `65f1debf039aed00f07040e6d05490cee395e96acf383464268621fa56402b27` | `10ccee8eaa33683e87f6e21ec12dc6b6c5633e8c8d4cbc004c5c5401334b6729` |

All of these artifacts bind certifier fingerprint
`d0a70336f3078ec8264fb584d644a30604ac0e0c73bfd1605a197268817ac26e`.
The direct adapter cases comprise 208 graph-snapshot, 140 adversarial, and 28
malformed replays. The MCP adapter cases comprise 321 graph-snapshot, 165
adversarial, and 34 malformed replays.

The model-backed V2 entry point was then exercised in readiness-only mode using
the intended GPT-5.6 Sol and GPT-5.5 config. It verified both candidate
releases, sealed artifacts, the pinned MCP revision, BloodHound health, and the
live graph before and after both tracks. Readiness fingerprint
`b1faa48d6692667cb05a7118569d6468aa9584a14752b9be502d10d83954969f`
passed with 46 direct and 70 MCP tasks and zero provider calls.

The current private receipts and public candidate catalogs are retained under
`results/v2/complex-seed-4401/adapter-parity-v3-entity-scoped-reaudit/`,
which is intentionally gitignored.

## Controlled BloodHound certification: simple seed 1234

The operator explicitly approved replacing the complex graph with simple seed
1234. BloodHound accepted the scoped collected-graph deletion with HTTP 204,
then MCP upload job `10` ingested the exact archive:

```text
simple-v1-seed-1234.zip
SHA-256 c3e939f063fcf9501551a80f07fcddd84966838a5f5cc9bff9ba3b29cdc6f3e4
```

After materialization, MCP exposed only `BEACONANALYTICS.INTERNAL`. The
independent ingest verifier matched 95 users, 58 computers, 19 groups, 10 OUs,
one domain, and all 8/8 planted paths.

`ori certify-v2-live` then completed three bounded, read-only projections:

| Gate | Objects | Relationships | Object queries | Relationship queries | Verification fingerprint |
| --- | ---: | ---: | ---: | ---: | --- |
| pre-direct | 183 | 894 | 17 | 36 | `191e58a71499949a6f5a21e43e20e9c28b1476ec2976d9683273721e481f437f` |
| between tracks | 183 | 894 | 17 | 36 | `191e58a71499949a6f5a21e43e20e9c28b1476ec2976d9683273721e481f437f` |
| post-MCP | 183 | 894 | 17 | 36 | `191e58a71499949a6f5a21e43e20e9c28b1476ec2976d9683273721e481f437f` |

Expected and observed graph fingerprints were
`edcb735723deca3888ad5a3a3c419edb58e023cda7117acdbfcf08e9e0d4afba`
at every gate.

Live/offline parity:

| Track | Candidates | Applicable parity cases | Evidence/verdict parity | Live certification artifact | Candidate catalog fingerprint |
| --- | ---: | ---: | --- | --- | --- |
| direct | 20 | 136 | all equal | `3f48bcc3a7732b1432218ca5d28d28f93a41edaa2325f8d6348019b145cd3ea8` | `0f5cd44fd95449be64b62cefe3be6b32058627766dcb0bfcc08d66dde2e39aa0` |
| MCP | 40 | 255 | all equal | `930bd407aee453753e2c3dea50d7863730d01b339fd7001030d0dad432deb07a` | `70cdfbbea62c40d47b35c6954c190cc125d4115092f2e418e97bd35e5ffd2531` |

The model-backed V2 entry point was then exercised in readiness-only mode using
the intended GPT-5.6 Sol and GPT-5.5 config. It verified the exact candidate
releases, sealed artifacts, clean pinned MCP revision, BloodHound health, and
live graph before and after both tracks:

| Gate | Result |
| --- | --- |
| Readiness fingerprint | `5d49762f65394243a22c763366ec41f79898ee8a23b64f6afb399df5c372be01` |
| MCP revision | `009c88f41fae302becad4b00777a3749a0f6f0fa` |
| GPT-5.6 Sol access | Codex login passed; `gpt-5.6-sol` present in the local model cache |
| GPT-5.5 access | Codex login passed; `gpt-5.5` present in the local model cache |
| Direct schedule | 20 candidate tasks, release `cbbee7a24db0bfe94cee849ead5124ddf82ee8499a8d2b72924d70767ce3209b` |
| MCP schedule | 40 candidate tasks, release `f2e5d9aaa3a1447fd72abdb70004da1bdb4370c272da83e415c418bfc3a2d3c3` |
| Provider calls | zero |

The local private receipts and public candidate catalogs are retained under
`results/v2/simple-seed-1234/live/`, which is intentionally gitignored. The
same directory includes the sanitized private ingest receipt for MCP upload job
`10`. Simple seed 1234 was later removed under explicit operator approval so
the complete complex catalog could be re-certified; its frozen ZIP and manifest
remain available for recovery.

## Final repository gates

- `uv run pytest`: 581 passed in 437.85 seconds.
- `uv run ruff check src scripts tests`: passed.
- `uv lock --check`: passed.
- `git diff --check`: passed.
- Simple final task preflight: 20 direct and 40 MCP tasks, zero errors and
  zero warnings.
- Candidate-catalog JSON Schema validation: passed for both tracks.
- TruffleHog 3.96.0: 2,516 chunks and 3,062,253 bytes scanned, zero verified
  or unverified secrets.
- Independent review: no critical, high, medium, or low findings.
- Containment-owned modules and tests: no diff from the pinned boundary.

## Controlled-environment result

Both current products are candidate-certified against immutable live graph
receipts. Complex seed 4401 is the graph currently loaded on the controlled
server. No provider or model campaign ran during either certification or the
final complex readiness gate.
