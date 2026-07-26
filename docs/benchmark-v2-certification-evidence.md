# ORI benchmark correctness v2 certification evidence

Date: 2026-07-25 through 2026-07-26
Branch: `feat/benchmark-correctness-v2`
Containment boundary: `0a56029471c5426be348c61c0969724eaee38599`
Protocol: `ori-eval-protocol-v2`

This record contains deterministic harness evidence only. No GPT-5.6 Sol,
GPT-5.5, local-model, or other provider campaign was launched.

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

The existing controlled graph first passed the legacy ingest verifier with all
30 planted paths. No upload or replacement was performed.

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

| Track | Candidates | Applicable parity cases | Evidence/verdict parity | Live catalog fingerprint | Candidate catalog fingerprint |
| --- | ---: | ---: | --- | --- | --- |
| direct | 46 | 348 | all equal | `72438f7bcf09cb666b0c39f9bb7d75031049afcb6875c473cf4159ba50550430` | `5427aafb98f25a3bb3d3f9d9ec405103eb0f74ba28cc9ecdd8ca7d68942e29f9` |
| MCP | 70 | 487 | all equal | `30df26007da860b87f2a1eedc8f8bd9966022323270f875674d8805c8a4194ca` | `e7d491d284edda1756f674c47fdc9f0a13e5f747fd39ff6bf1e6e380d30db22d` |

The final catalogs were regenerated from the immutable successful pre/middle/post
receipts after the MCP finalization source fingerprint was added to the
capability profile. This changed the profile-bound certification fingerprints
without changing any graph or parity evidence. A fresh `verify-ingest` after
the regeneration again passed all node counts and 30/30 planted paths.

The local private receipts and public candidate catalogs are retained under
`results/v2/complex-seed-4401/live/`, which is intentionally gitignored.

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
| pre-direct | 183 | 894 | 17 | 34 | `5d6341a7eb457ace172e822019e9eca9aca79cb081bbfb8209e89bfc49898bb4` |
| between tracks | 183 | 894 | 17 | 34 | `5d6341a7eb457ace172e822019e9eca9aca79cb081bbfb8209e89bfc49898bb4` |
| post-MCP | 183 | 894 | 17 | 34 | `5d6341a7eb457ace172e822019e9eca9aca79cb081bbfb8209e89bfc49898bb4` |

Expected and observed graph fingerprints were
`edcb735723deca3888ad5a3a3c419edb58e023cda7117acdbfcf08e9e0d4afba`
at every gate.

Live/offline parity:

| Track | Candidates | Applicable parity cases | Evidence/verdict parity | Live certification artifact | Candidate catalog fingerprint |
| --- | ---: | ---: | --- | --- | --- |
| direct | 20 | 136 | all equal | `cc6006d356c0452bbc9e214dd578c5ba002b5a176ff18f833d80191e03cd73a9` | `0f5cd44fd95449be64b62cefe3be6b32058627766dcb0bfcc08d66dde2e39aa0` |
| MCP | 40 | 255 | all equal | `29f1255893bbd0fb1c84867b79ff49af826044e6ccac65ab01352f572f84f176` | `1b5531bdbaeb2839ed713c495c841950f949649111bf587861ac2c2fb5a58621` |

The local private receipts and public candidate catalogs are retained under
`results/v2/simple-seed-1234/live/`, which is intentionally gitignored. Per the
operator's replacement approval, simple seed 1234 remains loaded on the
controlled server. The same directory includes the sanitized private ingest
receipt for MCP upload job `10`.

## Final repository gates

- `uv run pytest`: 520 passed.
- `uv run ruff check src scripts tests`: passed.
- `uv lock --check`: passed.
- `git diff --check`: passed.
- Simple final task preflight: 20 direct and 40 MCP tasks, zero errors and
  zero warnings.
- Candidate-catalog JSON Schema validation: passed for both tracks.
- Gitleaks: 106 commits and approximately 2.74 MB scanned, zero findings.
- Independent review: no critical, high, medium, or low findings.
- Containment-owned modules and tests: no diff from the pinned boundary.

## Controlled-environment result

Both current products are candidate-certified against immutable live graph
receipts. Complex seed 4401 remains certified by its preserved receipts and
simple seed 1234 is the graph currently loaded on the controlled server. No
provider or model campaign ran during either certification.
