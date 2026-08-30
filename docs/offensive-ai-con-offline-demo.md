# Offensive AI Con Offline Demo

This package is a deterministic, network-free visual walkthrough of ORI's
current evidence boundaries. It makes no provider calls, does not connect to
BloodHound, and contains no credentials, model responses, prompts, or sealed
oracle data.

## Stage package

- Interactive entry point: `docs/assets/offensive-ai-con/offline-demo/index.html`
- Static fallbacks: `docs/assets/offensive-ai-con/offline-demo/static/beat-01.png`
  through `beat-06.png`
- Public evidence receipt:
  `docs/assets/offensive-ai-con/offline-demo/demo-evidence.json`
- Render receipt:
  `docs/assets/offensive-ai-con/offline-demo/render-manifest.json`

Open `index.html` locally in a browser. Use the left and right arrow keys,
spacebar, or number keys 1 through 6. Keep the six PNGs open in a second window
or embedded in the deck as immediate fallbacks.

## Six beats

1. Establish graph truth before model spend.
2. Show that no-model readiness validates artifacts, runtime, capability,
   graph, and credential-source presence with zero provider calls.
3. Explain the read-only campaign monitor and its typed next actions.
4. Show how one typed claim produces solver-visible and sealed boundaries.
5. Explain typed provider, output, and circuit outcomes using the sanitized Nous
   interoperability canaries.
6. Close with the frozen historical Direct development results and their
   explicit limitations.

## Claim boundary

- The historical chart is Direct development evidence from the frozen V29
  campaign. It is not a current Release 1 result or an MCP ranking.
- The two Nous samples validate endpoint and harness interoperability only.
  Both final answers were `OUTPUT_INVALID`; they are not model-quality evidence.
  They predate the final fail-closed endpoint correction and must be rerun from
  fresh campaign roots before Release 1 can merge.
- OpenRouter has deterministic contract coverage but no live canary in this
  package.
- The current V29 release schedules 42 Direct and 55 MCP tasks. It is not the
  future official 100/100 suite.
- The interactive demo is illustrative. The JSON receipts and source artifacts
  are the authoritative evidence.

## Rebuild and verify

Rebuild the self-contained HTML and evidence receipt:

```bash
uv run python scripts/build_offensive_ai_con_demo.py
uv run pytest tests/test_offensive_ai_con_demo.py -q
uv run ruff check scripts/build_offensive_ai_con_demo.py \
  tests/test_offensive_ai_con_demo.py
```

Static rendering is optional and requires Playwright plus a local Chromium or
Chrome executable:

```bash
ORI_PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs \
ORI_CHROME_EXECUTABLE=/path/to/chrome \
node scripts/render_offensive_ai_con_demo.mjs
```

The renderer starts a fresh browser page for every beat, captures exactly
1280 by 720 pixels, and writes SHA-256 hashes to `render-manifest.json`. The
test suite verifies every checked-in PNG against that receipt.

## Stage abort and fallback rules

- If the interactive file does not open instantly, switch to the six static
  PNGs. Do not troubleshoot networking on stage; the demo needs none.
- Do not add `--execute`, provider keys, live model calls, or live BloodHound
  queries to the conference demo.
- If a claim cannot be tied to the public evidence receipt, omit it from the
  live narration.
- If the final current-runtime campaign is not complete and validated before
  the deck freeze, present the historical Direct chart with its existing
  limitations rather than substituting partial results.
