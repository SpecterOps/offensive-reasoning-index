# ORI inference configuration examples

These templates show common ways to connect ORI to local or hosted inference
providers. They are intentionally sanitized and disabled by default.

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
- `nvidia-nim.yaml` — NVIDIA NIM via OpenAI-compatible API.
- `bloodhound-mcp.yaml` — BloodHound MCP and BloodHound CE environment shape.

## Safety boundary

These examples are public-safe templates. Real inference routing files should
remain local-only when they contain private hostnames, private model aliases,
local output directories, reservation state, or internal network details.
