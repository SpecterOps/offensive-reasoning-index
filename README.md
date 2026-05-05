# Offensive Reasoning Index

ORI is a benchmark harness for evaluating model reasoning over BloodHound CE Active
Directory attack-path data. The current repo workflow is config-driven through
`ori run --config ... --profile ...`.

## Current Runbooks

- [Phase 4 v1 Hermes Runbook](docs/phase4-v1-hermes-runbook.md): generate the
  Phase 4 benchmark dataset, hand it to BloodHound CE ingest, and run MCP
  resources evaluations from `run-config-phase4-v1.yaml`.
- [OpenAI-Compatible Model Examples](docs/openai-compatible-model-examples.yaml):
  disabled profile examples for Ollama OpenAI compat, llama.cpp, MLX, vLLM,
  LM Studio, OpenRouter, NVIDIA NIM, and Hermes Portal.

## Common Commands

```bash
uv run pytest
uv run ruff check src tests
uv run ori run --config run-config-phase4-v1.yaml --profile phase4_v1
uv run ori run --config run-config-phase4-v1.yaml --profile phase4_v1_direct_best_stage3
uv run ori run --config run-config-phase4-v1.yaml --profile phase4_v1_full_mcp_best_stage3
```
