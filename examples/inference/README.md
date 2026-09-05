# ORI inference configuration examples

These templates show common ways to connect ORI to local or hosted inference
providers. They are intentionally sanitized and disabled by default.

ORI connects to an already running inference API. It does not require a personal
agent, GPU reservation helper, particular hostname or model-service manager.
Replace the endpoint with your local or remote service address. Provisioning and
service lifecycle are operator responsibilities outside the benchmark; the
chosen deployment tool does not change ORI's configuration contract.

Use them as copy-and-edit starting points:

1. Copy one template to a local run config, for example:

   ```bash
   cp examples/inference/openai-compatible.yaml run-config.local.yaml
   ```

2. Replace placeholder model names, base URLs, manifest paths, output paths, and
   MCP checkout paths for your environment.
3. Put secrets in environment variables or your provider gateway. Do not commit
   API keys, private hostnames, local workstation paths, or production
   BloodHound credentials.
4. Run a preflight check before launching an evaluation:

   ```bash
   uv run ori run --config run-config.local.yaml --profile preflight_local
   ```

5. Launch an evaluation profile only after BloodHound health, ingest, and
   preflight mock gates pass.

## Templates

- `openai-compatible.yaml` — generic OpenAI-compatible endpoint; works for many
  hosted gateways and local servers that expose `/v1/chat/completions`.
- `ollama.yaml` — local Ollama using ORI's native Ollama/MCP path.
- `llama-cpp.yaml` — llama.cpp server with OpenAI-compatible API.
- `vllm.yaml` — vLLM OpenAI-compatible server.
- `lm-studio.yaml` — LM Studio local OpenAI-compatible server.
- `openrouter.yaml` — OpenRouter via OpenAI-compatible API.
- `nous.yaml` — Nous Portal via its OpenAI-compatible inference API.
- `nvidia-nim.yaml` — NVIDIA NIM via OpenAI-compatible API.
- `bloodhound-mcp.yaml` — BloodHound MCP and BloodHound CE environment shape.

## Safety boundary

These examples are public-safe templates. Real inference routing files should
remain local-only when they contain private hostnames, private model aliases,
local output directories or internal network details.

The Nous template includes disabled direct and MCP profiles for the current Ox
Alpha catalog entry, `openai-compat/stealth/ox-alpha`. Set `NOUS_API_KEY` (or
`NOUS_PORTAL_API_KEY`) in the environment and enable only the profile you have
validated against your controlled BloodHound target. Start with the direct
profile; tool-enabled compatibility may vary for this newly released model.

## Protocol V2 API surfaces

V2 model entries accept `api_surface: auto | chat_completions | responses`.
Release 1 preserves existing behavior: Codex OAuth resolves `auto` to Responses,
while official OpenAI and OpenAI-compatible endpoints resolve it to Chat
Completions. OpenRouter and Nous therefore use `auto` or explicit
`chat_completions`. Other explicit Responses selections fail readiness before
model usage.

Credentials are endpoint-isolated. OpenRouter uses `OPENROUTER_API_KEY`, Nous
uses `NOUS_API_KEY` or `NOUS_PORTAL_API_KEY`, official OpenAI uses only
`OPENAI_API_KEY`, and an unrecognized compatible endpoint uses only the explicit
`OPENAI_COMPAT_API_KEY` override. ORI never borrows one provider's key for a
different hostname.
