#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELFILE_DIR="$ROOT_DIR/ollama/phase3-m4"

if ! command -v ollama >/dev/null 2>&1; then
  echo "error: ollama is not installed or not on PATH" >&2
  exit 1
fi

create_alias() {
  local alias_name="$1"
  local modelfile_path="$2"
  echo "==> ollama create ${alias_name}"
  ollama create "${alias_name}" -f "${modelfile_path}"
}

create_alias "ori-gemma4-26b-32k" "$MODELFILE_DIR/ori-gemma4-26b-32k.Modelfile"
create_alias "ori-gemma4-26b-64k" "$MODELFILE_DIR/ori-gemma4-26b-64k.Modelfile"
create_alias "ori-gemma4-e4b-32k" "$MODELFILE_DIR/ori-gemma4-e4b-32k.Modelfile"
create_alias "ori-gemma4-e4b-64k" "$MODELFILE_DIR/ori-gemma4-e4b-64k.Modelfile"
create_alias "ori-gpt-oss-20b-32k" "$MODELFILE_DIR/ori-gpt-oss-20b-32k.Modelfile"
create_alias "ori-gpt-oss-20b-64k" "$MODELFILE_DIR/ori-gpt-oss-20b-64k.Modelfile"
create_alias "ori-qwen3-14b-32k" "$MODELFILE_DIR/ori-qwen3-14b-32k.Modelfile"
create_alias "ori-qwen3-14b-64k" "$MODELFILE_DIR/ori-qwen3-14b-64k.Modelfile"
create_alias "ori-qwen3-coder-30b-32k" "$MODELFILE_DIR/ori-qwen3-coder-30b-32k.Modelfile"
create_alias "ori-qwen3-coder-30b-64k" "$MODELFILE_DIR/ori-qwen3-coder-30b-64k.Modelfile"
create_alias "ori-qwen35-27b-32k" "$MODELFILE_DIR/ori-qwen35-27b-32k.Modelfile"
create_alias "ori-qwen35-27b-64k" "$MODELFILE_DIR/ori-qwen35-27b-64k.Modelfile"
create_alias "ori-qwen35-9b-32k" "$MODELFILE_DIR/ori-qwen35-9b-32k.Modelfile"
create_alias "ori-qwen35-9b-64k" "$MODELFILE_DIR/ori-qwen35-9b-64k.Modelfile"

echo
echo "Created all Phase 3 M4 Ollama aliases."
echo "Verify with: ollama list | grep '^ori-'"
