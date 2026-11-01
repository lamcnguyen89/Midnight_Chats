#!/usr/bin/env bash
# Register the seven variants using installed base weights. Does not run inference.
# Run again after editing a Modelfile to update the registered variants.
set -euo pipefail

model_files_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v ollama >/dev/null 2>&1; then
    printf '%s\n' 'Ollama is not installed or is not on PATH.' >&2
    exit 1
fi

ollama create deepseek-r1-abliterated-uncensored:70b -f "$model_files_dir/deepseek-r1-abliterated-70b-uncensored.Modelfile"
ollama create deepseek-v4.1-flash-1017-uncensored:27b -f "$model_files_dir/deepseek-v4.1-flash-1017-27b-uncensored.Modelfile"
ollama create dolphin-llama3-uncensored:70b -f "$model_files_dir/dolphin-llama3-70b-uncensored.Modelfile"
ollama create qwen3.8-uncensored:27b -f "$model_files_dir/qwen3.8-27b-uncensored.Modelfile"
ollama create llama3.2-uncensored:3b -f "$model_files_dir/llama3.2-3b-uncensored.Modelfile"
ollama create dolphin-mixtral-uncensored:8x22b -f "$model_files_dir/dolphin-mixtral-8x22b-uncensored.Modelfile"
ollama create devstral-98k:latest -f "$model_files_dir/devstral-98k-uncensored.Modelfile"

printf '%s\n' 'All seven variants registered. Refresh the Midnight Chats model list.'
