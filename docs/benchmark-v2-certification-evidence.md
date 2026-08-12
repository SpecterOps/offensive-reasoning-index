# ORI benchmark correctness v2 certification evidence

Date: 2026-07-25 through 2026-07-31
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

## Post-campaign correctness certification: complex V15

The operator-authorized V10 campaign exposed false-negative risk in both the
provider contract and the evidence boundary. The V15 correction keeps exact
sets, exact counts, and structural routes strict while ensuring that:

- each provider receives one authoritative public V2 contract and question;
- a returned route binds the public source and target at its witness endpoints;
- scalar identity rows can prove the identities actually returned by
  BloodHound;
- truthful additional route evidence must be graph-attested and connected;
- stable scalar and list-valued graph properties are attestable even when the
  current oracle does not require them;
- CE bookkeeping metadata is excluded explicitly; and
- known case-only ingest transformations, including operating-system values,
  are canonical across archive, live, and answer evidence.

The final bounded live gate projected 17,088 objects and 60,342 relationships
with 31 object queries and 98 relationship queries at each pass. Pre-direct,
between-track, post-MCP, and no-model readiness all matched graph fingerprint
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.

| Track | Candidates | Offline fixtures | Live certification | Candidate catalog | Candidate release |
| --- | ---: | ---: | --- | --- | --- |
| direct | 46 | 276 | `1288e491556a3273c789e6e1c2bd3742824bddb03a3ea5a35bd4613a43147853` | `877ad272ff1595281cd49ef590cae6f816f7d269e2c3250b70562064afa7c669` | `9c18bbd386de06f5949ec8c3066b2181ab2243137b31e20057a8b72effeff0a2` |
| MCP | 70 | 420 | `add95284b7640c80c7c4cc5bb7521c4747d360badc36273102b78242bc47f4c8` | `2cd4360777d0da7759c63942591fc7b50d4f98f6b36d27f826f8a4a8eaab794e` | `2cbe4159d3b9df82dd5e1f20bb708df85814fdf46a398072edc4a8edb9b2b191` |

Both tracks bind compiler
`c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e`,
comparator
`5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`,
and certifier
`cd44cfaf5486dc8437e20d6e1b5e4730dc73c18c621430f59308eb82c2e6439f`.
Two-model/two-track readiness passed with fingerprint
`72689d503bb4f8cd9b55acd16972442695d46453af3567f692c7d607f22ebcc2`,
the pinned MCP revision
`009c88f41fae302becad4b00777a3749a0f6f0fa`, and zero provider calls.

Stored V10 answers and receipts were replayed diagnostically across the
corrected scorer boundary. All 38 previously correct GPT-5.6 Sol MCP answers and
all 35 previously correct GPT-5.5 MCP answers remained correct. Together with
the finalization/proof corrections, the diagnostic totals are 45/70 and 40/70,
respectively; these are not official rescored campaigns because the original
models saw the earlier V10 prompts. Direct replay remained 23/46 and 21/46.

## Independent-review closure: complex V16

Independent review of V15 found two proof-boundary defects and one fingerprint
dependency gap. A follow-up reproduction also found that an omitted first-page
`SKIP 0` produced a different population key from later explicit `SKIP` pages.
V16 closes all four cases:

- a broader non-zero direct absence count is proof-insufficient instead of an
  incorrect reasoning verdict;
- a route path variable must preserve lineage through `WITH` projections;
- MCP route/decision finalization requires an actual node-and-edge path receipt;
- first-page `LIMIT` and later contiguous `SKIP/LIMIT` pages share one canonical
  population key; and
- the MCP finalization fingerprint includes the shared query contract.

All 38 previously correct GPT-5.6 Sol MCP answers and all 35 previously correct
GPT-5.5 MCP answers remain correct under the corrected scorer boundary. The
45/70 and 40/70 diagnostic totals therefore remain useful regression evidence,
but they are not official V16 model scores because no model saw the V16
contract.

The V16 live gate again projected 17,088 objects and 60,342 relationships with
31 object queries and 98 relationship queries. Pre-track, middle, post-track,
and no-model readiness all matched
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.

| Track | Candidates | Offline fixtures | Live certification | Candidate catalog | Candidate release |
| --- | ---: | ---: | --- | --- | --- |
| direct | 46 | 276 | `98f0df5c974be0b50cac9875a2ae0b3b781d9e8fd98bc1b828de39789340e568` | `43d2c71c3f9f98cf892a6b9b7b13753ff6ed1cc9ba257e3bc8b06595d5d14f52` | `b9d3d42ea63ae0569aae2ad2f59674ec06d7e68660f64ae81183eb27a988f9c9` |
| MCP | 70 | 420 | `066586c27159a260d66682908c351363d3d3fbcecce42f87c79bdcb1299302b0` | `e92946461f37813979a0df26e679ce57c8328741da87e55eecbcb7d41fe79153` | `39aa51a4bec0f8fd49183b9ca3a0faac4195a2bdd295cac90af06344f735958a` |

Both tracks bind compiler
`c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e`,
comparator
`5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`,
and certifier
`90d6a98ee555fd6c74a17b2c3c3051d128fcdb7c8f71e89cb2959eeaa235dc58`.
Two-model/two-track no-model readiness passed with fingerprint
`c0b01c6c331a0ec495e40ed02fe77ab505587bb3c611beae8bc838303ebb7ff7`,
the pinned MCP revision
`009c88f41fae302becad4b00777a3749a0f6f0fa`, and zero provider calls.

## Final-review closure: complex V17

A second independent review found three remaining MCP evidence-boundary gaps.
V17 closes them without weakening exact answer comparison:

- endpoint selectors must remain live in the returned path's Cypher scope, so
  a later detached `MATCH` cannot authorize an unrelated path;
- complete set pages use an alpha-equivalent population key, so changing only
  a bound variable name cannot produce a false `PROOF_INSUFFICIENT`; and
- relationship endpoint/type contracts are included in the MCP finalization
  fingerprint because those contracts participate in public claim relevance.

The V17 live gate projected 17,088 objects and 60,342 relationships with 40
object queries and 159 relationship queries. Pre-track, middle, post-track,
and no-model readiness all matched
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.

| Track | Candidates | Offline fixtures | Live certification | Candidate catalog | Candidate release |
| --- | ---: | ---: | --- | --- | --- |
| direct | 46 | 276 | `669d8e34d4a75d89055ddee35fdfe4accbfd6d83fe7696f9f53afaa8851d275c` | `f46a56d1e24c6f5fb257a68d0e47d82113c2de49cee141a84697e146401536ce` | `be6abca0dc99ed2881611a698d5ce3dabf1eaaecf81cd0052e36688222875b19` |
| MCP | 70 | 420 | `9b70f4a7312a3099c67566e79af8d0ff4d8593d2d7dcc079e06a13498a23aaf9` | `6973f469b609200674580b9370eed811117318f23a711c20caaf7b8bce68b229` | `b665d9fa200def86a3df76ff71bc679c04d8528f08809a9a2ce0fbf7505a75d4` |

Both tracks bind compiler
`c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e`,
comparator
`5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`,
and certifier
`55170d1aa62df366cf20f8152a9c89035e15ed33fcaf36bec4e03676be1d62f0`.
Two-model/two-track no-model readiness passed with fingerprint
`53dff3f02350516faba5cbc80f5e9a566c57532a936ab6f3eb2e730bdef08ec6`,
the pinned MCP revision
`009c88f41fae302becad4b00777a3749a0f6f0fa`, and zero provider calls.

## Final false-negative closure: complex V18

The V17 re-review found two valid-proof forms that were still too
conservatively rejected. V18 accepts both while preserving the negative
fixtures:

- public endpoint constraints may be applied after path assignment when the
  constrained variables are still the returned path's live endpoint lineage,
  including through `WITH` passthrough/aliases; genuinely detached variables
  remain irrelevant; and
- a supported single anonymous `COUNT(*)` population is canonical with the
  equivalent named entity-enumeration population.

The V18 live gate again projected 17,088 objects and 60,342 relationships with
40 object queries and 159 relationship queries. All three certification gates
and no-model readiness matched graph fingerprint
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.

| Track | Candidates | Offline fixtures | Live certification | Candidate catalog | Candidate release |
| --- | ---: | ---: | --- | --- | --- |
| direct | 46 | 276 | `6c8a7b424e7777e5041125709570b302701e2f97bbb4fc7a430d079891984bfa` | `2c21803a71d3b01125f25ee58144b2e39b9a30ed8836e4fd85cc5acab023abce` | `c388e3981a0cb14bd8d37e61e4083a6ac746271b1230f97903e58618f94db77f` |
| MCP | 70 | 420 | `d19468008e21a6e0dedff34223c96fedbaa4adc856d05ce10bd11ecf5f576ca2` | `ad42aac383c580333d6485a6b03abcf7ed27ffe219fc84e78d343bcb34a976e8` | `2d4bb2c4feccb4d518ba9509fd3ca740e84403d23b529d68bab5cca6fb77c298` |

Both tracks bind compiler
`c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e`,
comparator
`5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`,
and certifier
`1c9d8d9801066503a96644edbcd1fdf514bc8a3ecd757ae0e66e077b15b0b5b0`.
Two-model/two-track no-model readiness passed with fingerprint
`028c6b4c21e9d77e055867d742a2d8daf9dbac8bfd8e9b94ce7a310aa912f6c6`,
the pinned MCP revision
`009c88f41fae302becad4b00777a3749a0f6f0fa`, and zero provider calls.

## Final prompt and proof-equivalence closure: complex V19

The final V19 audit distinguished answer correctness from execution proof and
closed four remaining false-negative or attribution edges:

- multiline `RETURN` and `WITH` projections retain their Cypher lineage;
- count/page population matching preserves identity distinctness, while a
  distinct count paired with ordinary rows is accepted only when the receipts
  prove one globally unique object ID per row;
- an otherwise exact bounded-negative query with a wider hop ceiling is treated
  as broader, so only its zero result is a valid stronger proof; and
- MCP `circuit_open` receipts are infrastructure rather than model query errors.

The solver-visible MCP result contract now tells the model that a full-set count
and pages must use the same identity population and distinctness and that pages
must return one unique answer identity per row. It still discloses no expected
count, entity, route, or oracle fact.

Stored V10 final-answer replay remains diagnostic rather than an official
rescore because those models saw a different prompt. Every answer previously
judged correct still compares correctly under the corrected comparator. Some
old transcripts no longer establish V19 proof—for example, an open-ended
membership traversal cannot prove a public two-hop claim—but V19 communicates
the exact public bound before execution.

The V19 live gate projected 17,088 objects and 60,342 relationships with 40
object queries and 159 relationship queries. All three live gates and no-model
readiness matched graph fingerprint
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`
and live verification fingerprint
`5a832aaf6d15972eaf4f75c44d442f0e839004b185777eb0947b5bb939f12f53`.

| Track | Candidates | Offline fixtures | Live certification | Candidate catalog | Candidate release |
| --- | ---: | ---: | --- | --- | --- |
| direct | 46 | 276 | `cc2b7ca17ab7646fff6715f689281b64dda6c0aba09d9d7c4441258f15e6e583` | `3734c3d15cb629758e4f77d006cef17e096d6c57caaf3bf9fb9943c78bdef74f` | `9ac6bccc07b032186df0ee30b2e477c61c8e99f3f60f1ddec2a296da633aee57` |
| MCP | 70 | 420 | `07465856be2f55d6d55b07a734a1dd57d103fcdcd1233eea481a65ff9fc133d6` | `51788c8f9aeb1f052b82e76619dd2888794e78dd64690cdacce763dae46a1e84` | `c7c075d2e0a420309acf9e7dd31fbd57df71403c345ee4a7458a1bc478cd0721` |

Both tracks bind compiler
`c686787c45b9a367fe0c3a6d9895fa22c5af5b6c2c59e41a460c5d4bbc57f68e`,
comparator
`5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`,
and certifier
`ce74acc27190a91362327599dcb5cb6e4ceadf3b59e1ef60783de63f5c0742eb`.
Two-model/two-track no-model readiness passed with fingerprint
`c492c28eb584868b15764040c0cf1b7766f067418c184581620c1fa9cdd941a6`,
the pinned MCP revision
`009c88f41fae302becad4b00777a3749a0f6f0fa`, and zero provider calls.

An independent V19 re-review then found two bounded-negative proof-contract
edges: a contradictory `WHERE` selector could force a false zero after valid
inline endpoint selectors, while a complete one-hop query was rejected unless
the model wrote recursive `*1..1` syntax. V19 therefore remains historical and
is superseded by V20.

## Final bounded-negative proof closure: complex V20

V20 rejects any conflict between inline and `WHERE` endpoint-role bindings and
accepts ordinary one-edge syntax as equivalent to `*1..1` when the public hop
ceiling is one. The real MCP transcript projector proves both behaviors: a
complete one-hop zero unlocks valid negative proof, while a contradictory
selector remains irrelevant and cannot unlock finalization. The direct result
contract, MCP result contract, MCP evidence state machine, and certifier
boundaries all advanced.

The complete 46-task direct and 70-task MCP catalogs passed offline
certification and read-only live adapter parity. All three live gates and
no-model readiness observed 17,088 objects and 60,342 relationships with graph
fingerprint
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`
and verification fingerprint
`5a832aaf6d15972eaf4f75c44d442f0e839004b185777eb0947b5bb939f12f53`.

| Track | Candidates | Live certification | Candidate catalog | Candidate release |
| --- | ---: | --- | --- | --- |
| direct | 46 | `57226e60e2a5622f3bc31c3ed912c51aeaa8bf64efbe8c8cf4a2ddfe17aaa00e` | `a3fff5385721f1d89ac70b9e5855199da8159d1b285529b9bb05768c91d866ad` | `dfad392d4e7744f88189aafac97b36a3fac1b6d4f8e95817069a1c89dd096c20` |
| MCP | 70 | `d562de4109d1f6c0adbd1159e2ca2067eeb17e016c9c1dc559c64678f795eb8f` | `8344a0cdbde1d8f60f3cc5bfb9632aff56a977e470346d9c9619f400c1980be9` | `2fa21757dc780cb338755dfb0ab99ef350f98957689de3b189e371044598e509` |

Both tracks bind compiler
`e037c580616028daf82e44771ecc59d6353b30bb294ffc2816b7df062800d04d`,
comparator
`5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`,
and certifier
`7a58ed05ecba8ad92f891c0e007039dbd50620781b8981044be5eea919b65c06`.
Two-model/two-track no-model readiness passed with fingerprint
`df1a097c7f866fb33f15443006413c03fd94d9b784e4742bbb1155a69bbb4f1e`,
the pinned MCP revision
`009c88f41fae302becad4b00777a3749a0f6f0fa`, and zero provider calls.

Because implementation fingerprints intentionally invalidate every older
candidate after semantic source changes, the simple product was recompiled and
offline-certified under V20. Its old live certificates remain conservatively
stale until the controlled server is explicitly switched from complex seed
4401 to simple seed 1234 and the read-only live gate is repeated. Simple has no
bounded-negative tasks; this does not block execution of the independently
certified complex V20 campaign.

## Current scorer and communication boundary: V28

An audit prompted by unexpectedly low model scores found real benchmark defects
in addition to genuine model errors. The V10 runtime had injected a
resource-first BloodHound server prompt even though certified runs declared
`resource_mode: off`; that prompt competed with the claim-bound proof contract.
V28 sends one authoritative V2 system prompt and one compiled user question.
The discovered server prompt remains in private provenance but is not sent to
the model in this profile.

The same audit closed false-negative edges in identity normalization, direct and
MCP projection, path lineage, connected supplemental evidence, benign CE
properties, count/page population equivalence, bounded-negative proof, grouped
selectors, and exact one-hop syntax. Failed or truncated receipts can no longer
seed later MCP completeness state. The public task now states every material
acceptance requirement, including exact hop ceilings and case-sensitive
BloodHound identifiers, while expected entities, reference queries, and oracle
facts remain sealed.

A 2026-07-31 completion audit inspected the generated public artifacts rather
than relying only on source review. All 46 direct and 70 MCP tasks have a
non-empty compiled question and `AcceptanceSpec`; all MCP bindings declare
`resource_mode: off`; and recursive key inspection found zero occurrences of
`reference_cypher`, `ref_result`, `valid_node_names`, `expected_entities`,
`reference_results`, `reference_nodes`, or `correct`. Eight focused
compiler/schema/runtime regressions passed, including public-acceptance
equivalence, hidden-scorer-constraint rejection, bounded-negative schema/prompt
agreement, one-question direct requests, and suppression of the discovered
BloodHound server prompt.

Stored V10 final answers were replayed only as a diagnostic. That answer-only
replay increased GPT-5.6 Sol from 38/70 to 45/70 and GPT-5.5 from 35/70 to
40/70 on the MCP catalog while preserving every previously correct answer.
Those values are not official V28 scores: the old models saw an older prompt and
their stored receipts do not satisfy every current execution-proof contract.
A fresh V28 campaign is required for a comparable model result.

The current implementation boundary is:

- compiler `ori-claim-compiler-v2.10.0`;
- direct result contract `ori-direct-result-contract-v13`;
- MCP result/finalization contract `ori-mcp-result-contract-v21` and
  `ori-mcp-evidence-v21`;
- certifier `ori-live-certifier-v23`.

Complex seed 4401 compiled 46 direct and 70 MCP candidates. All 276 direct and
420 MCP offline fixtures passed, followed by read-only live certification
against graph fingerprint
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`.

| Track | Candidates | Public artifact | Candidate release | Live certificate |
| --- | ---: | --- | --- | --- |
| direct | 46 | `6cdc9be48825771cde26e7b31d3e3b2ee54d9a591754deda72255506a95f3745` | `e1be6a859b5212c13acf87de7b3368263a82fd9314b91acbf11e998b08b6408a` | `177f8ff0db9c37d20b2b894dfd7de8940d5f5dae72b53ad10242b0e9a55e910d` |
| MCP | 70 | `6e34c7c0871a428e277d059a784dc8ff690dc9963d60dda330f3eec2819dff5a` | `40766c5c2d7f291a443b75b4ab01a8e17e35e6aee10321ff37f965b6daf1651f` | `79ee3e027c0fd915db1531dd6daac512323ef80c26bb96a49e7faf55c55acc48` |

Both tracks bind compiler fingerprint
`e62dc48d908d99f03cbb7e75c731411d9eb65b5a61fdc79817b75755eba25058`
and comparator fingerprint
`5e6aa7829c856b65fb7020bfe420b968ecf6ede66e5fd03b53ab7766359da9a1`.
The two-model/two-track no-model readiness gate passed for GPT-5.6 Sol and
GPT-5.5 with zero provider calls.

Simple seed 1234 was also recompiled and offline-certified under the same V28
implementation: 20 direct and 40 MCP candidates passed. On 2026-07-31 the
operator approved a temporary graph replacement. The target-pinned clear
returned exactly HTTP 204, and BloodHound MCP upload job 12 installed the exact
simple archive. Health, exact node counts, and all 8/8 planted paths passed.
The three-gate live certifier then reproduced graph fingerprint
`e964390638310fc94f4b4b3fe4a50a2f3e0006510626af6b12f829f8631549ae`
and certified every simple candidate.

| Track | Candidates | Candidate release | Live certificate |
| --- | ---: | --- | --- |
| direct | 20 | `b60a2aeba384a4e398b8ee352dfd765fddc319ecb9229d4f4aad46b597685160` | `215130645ef96daa3a12c547c7b1d6198ae5819fe3d7dbc63a04d0fe1bc42f3a` |
| MCP | 40 | `f78a9dc3227b302a9ee196fbeaa62734cc4434335aa71c1aa257d34de226ee12` | `5a62aeb1694c26fd7a655a5b7fa0a0dd308d1baf6870077f88cc3bb9b8dbf5c8` |

Simple no-model readiness passed for both configured models and both tracks
without provider calls. The live snapshot contained 415 canonical objects and
1,397 canonical relationships; expected and observed fingerprints were equal.

V21 through V27 are superseded development artifacts and must not be executed
or resumed.

## Final repository gates

- `uv run pytest`: 730 passed in 575.58 seconds under the V28 boundary.
- `uv run ruff check src scripts tests`: passed.
- `uv lock --check`: passed.
- `git diff --check`: passed.
- Isolated tracked and non-ignored source scan: TruffleHog 3.96.0 scanned 399
  chunks and 3,866,405 bytes with zero verified or unverified secrets;
  Gitleaks 8.30.1 scanned approximately 3.15 MB with zero findings.
- Authoritative direct-containment regression tests remain green.

## Controlled-environment result

Complex seed 4401 is again the graph currently loaded on the controlled server.
After simple certification, a second target-pinned clear returned exactly HTTP
204 and BloodHound MCP upload job 13 restored the exact complex archive. Health,
exact node counts, and all 30/30 planted paths passed. No-model readiness
reproduced graph fingerprint
`fb0b6785e524d40abcc9033ea2c7887eaa88e13bb6cb1f329636a4ce4b74b1c4`
and accepted the existing 46-direct and 70-MCP releases. Both simple and complex
are therefore offline- and live-certified under V28, while the controlled server
ends in its required complex seed-4401 state. No provider or model campaign ran
during this graph swap, certification, restoration, or readiness validation.
Final branch reconciliation is intentionally deferred to PR preparation and is
not part of the completed benchmark-correctness certification goal.
