# Native MCP backend verification

## Development integration status

Native compilation now has an explicit Python API through
`compile_legacy_product(..., native_profile=profile)` and the OAIC compiler.
The default remains historical. Native profiles are separate from CE profiles
and bind exact source, runtime/dependency, backend and discovery expectations;
their hashes do not prove that those expectations were qualified. The native
implementation fingerprint conservatively covers packaged ORI Python source,
including shared proof helpers, without requiring a Git checkout.

Compilation preserves claim and recipe identities while creating new public
task, oracle and catalog fingerprints. Native contracts name the actual server
tools, not a normalized Cypher facade. Unsupported Armadin contracts fail closed.
The historical fixture certifier explicitly rejects native tasks: native adapter
replay and complete selected-task admission are still under implementation.
There is not yet a native campaign CLI path; do not substitute these contracts
into a historical certified campaign or reuse its certification.

A native session may bind the expected capability profile. It then rejects task
or discovery mismatches before backend dispatch. Its initial count adjudicator
uses actual native results plus public query scope, without oracle data. Counts
after intermediate limiting/filtering/rebinding, property counts, arithmetic and
unproven population changes cannot unlock proof. Relationship-derived counts
must count distinct node identities. Those restrictions are solver-visible in
the compiled native contract. Positive paths from main and Armadin now require
observed stable endpoint identities, actual directed connectivity within the
public hop bound, and public query/argument scope. Main graph map keys are only
edge locators, never stable identities; auxiliary scalar columns cannot become
count proof. Armadin's complete Domain listing can establish an unfiltered full
Domain set, including an empty set, only with unique identities and a matching
explicit native count. Filtered/windowed tasks and truncated results cannot use
that proof. This follows the pinned server's
[unbounded Domain query](https://github.com/armadin-public/bloodhound-mcp-server/blob/6ad4a4703d1117c3019400539911ca689a537197/tools/active_directory/domain_tools.py)
and [complete record iteration](https://github.com/armadin-public/bloodhound-mcp-server/blob/6ad4a4703d1117c3019400539911ca689a537197/lib/bloodhound_client.py).
Other proof shapes remain non-unlocking until their native adjudicators are
implemented. Native fixture certification remains pending. A proof event alone
does not admit a campaign or establish answer correctness.

The native MorDavid and Armadin implementations use Neo4j Bolt, not the
BloodHound CE API. `ori verify-native-graph` independently checks one explicitly
selected database against the generated archive's scoring graph. It does not
launch an MCP server or model, ingest data, authorize a campaign, or replace a
native tool with a normalized facade. Historical CE workflows are unchanged.

## Operator workflow

Install the optional, pinned driver in the project environment:

```bash
uv sync --extra native-mcp --locked
```

Configure a dedicated synthetic-data database and a database-enforced read-only
principal outside ORI. Supply credentials through a protected environment file,
never command arguments or tracked configuration. MorDavid uses
`BLOODHOUND_URI`, `BLOODHOUND_USERNAME`, and `BLOODHOUND_PASSWORD`; Armadin uses
`NEO4J_URI`, `NEO4J_USERNAME`, and `NEO4J_PASSWORD`. Credentials never fall back
between these groups. Direct `bolt://` and certificate-verified `bolt+s://`
connections are supported; routing, embedded credentials, and URI query options
are rejected. Select the actual database name explicitly, not an unverified alias.

After authorization to contact that backend, run:

```bash
uv run ori verify-native-graph \
  --implementation armadin \
  --database neo4j \
  --manifest datasets/benchmarks/oaic-2026-v1-seed-67_manifest.json \
  --archive datasets/benchmarks/oaic-2026-v1-seed-67.zip \
  --product oaic-2026-v1 \
  --output results/native-graph/armadin-neo4j.private.json
```

Use `--implementation mordavid` with its credential group for MorDavid. Never
assume that checking `neo4j` checks `bloodhound`: the pinned MorDavid server can
fall back between databases after errors. Every reachable effective fallback
database needs the same verified graph and read-only isolation, or enforced
inaccessibility. A failed probe is not proof of enforced inaccessibility.

`--page-size` defaults to 500 and is bounded to 1–2,000. The entire transport has
a 120-second default client deadline and a 60-second transaction deadline;
`--transaction-timeout-seconds` cannot exceed `--timeout-seconds`. Repeated
`--auxiliary-label` options explicitly permit non-concrete labels such as `Base`;
they never permit a second concrete object type or ignore unknown objects.

## What a pass proves

The verifier checks global node and relationship counts before and after bounded,
ordered enumeration. It rejects extra disconnected objects, unexpected labels,
duplicate identities or physical edges, incomplete pages, foreign endpoints, and
changed scorer-relevant properties. The graph includes the archive-derived
`ADLocalGroup` inventory and incident relationships. A display fallback for a
nameless object does not create a native `name` property.

All queries use one explicit transaction and database with no managed retries.
The observed database and server metadata must remain consistent. The transaction
is explicitly rolled back on success. Driver read routing is **not** a privilege
check. Neo4j read-committed isolation is **not** a frozen snapshot: an operator
must prevent concurrent writes; matching pre/post counts alone cannot prove that.

The private result reuses the existing graph-verification structure and adds a
separate fingerprint of all observed native properties and label ordering. That
native fingerprint records the observation; it does not assert archive parity for
properties that the scoring graph normalizes away. Unsupported property values
are rejected rather than converted to strings or silently dropped.

## Remaining campaign gates and privacy

A pass deliberately records `campaign_admitted`, `read_only_privileges_verified`,
`quiescence_verified`, and `mcp_source_verified` as false. The source revision is
an expected pin, not proof that a server at that revision ran. Native runtime and
dependency pinning, privilege/isolation verification, source qualification,
task-level proof coverage, certification, and runner admission remain separate
requirements. Do not use this diagnostic result to bypass them.

Output must have a new `.private.json` filename; existing files and symlinks are
not replaced. Atomic output is owner-readable/writable and contains no password,
but does contain private endpoint/principal information. Keep it out of public
exports. The console reports only a safe status or typed failure code. The
repository ignores `*.private.json`; this is an additional safeguard, not a
substitute for release privacy review.

The driver is pinned to 5.28.2. Its explicit transaction and result-summary
interfaces are documented in the [pinned driver source](https://github.com/neo4j/neo4j-python-driver/tree/f4fcf70d9e03266481bb4dd26aff73903595450b).
See also Neo4j's [transaction documentation](https://neo4j.com/docs/python-manual/current/transactions/)
for the distinction between routing and read-only permissions.
