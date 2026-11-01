# Custom Ollama models

These seven Modelfiles derive variants from the installed base models. Each uses
an explicit uncensored system prompt and `PARAMETER num_predict -1` to remove the
explicit generated-token cap. Six use the largest context length reported by
their installed model's metadata; Devstral uses the requested 98,304-token window.

| Base model | Created model | Context tokens | Modelfile |
| --- | --- | ---: | --- |
| `huihui_ai/deepseek-r1-abliterated:70b` | `deepseek-r1-abliterated-uncensored:70b` | 131,072 | [File](deepseek-r1-abliterated-70b-uncensored.Modelfile) |
| `eriktonon/deepseek-v4.1-flash-1017:27b` | `deepseek-v4.1-flash-1017-uncensored:27b` | 262,144 | [File](deepseek-v4.1-flash-1017-27b-uncensored.Modelfile) |
| `dolphin-llama3:70b` | `dolphin-llama3-uncensored:70b` | 8,192 | [File](dolphin-llama3-70b-uncensored.Modelfile) |
| `orcarouter/Qwen3.8-27B-Uncensored:latest` | `qwen3.8-uncensored:27b` | 262,144 | [File](qwen3.8-27b-uncensored.Modelfile) |
| `llama3.2:3b` | `llama3.2-uncensored:3b` | 131,072 | [File](llama3.2-3b-uncensored.Modelfile) |
| `dolphin-mixtral:8x22b` | `dolphin-mixtral-uncensored:8x22b` | 65,536 | [File](dolphin-mixtral-8x22b-uncensored.Modelfile) |
| `huihui_ai/devstral-2-abliterated:123b` | `devstral-98k:latest` | 98,304 | [File](devstral-98k-uncensored.Modelfile) |

## Install

With Ollama running, execute from the Midnight Chats project directory:

```bash
bash model_files/create_models.sh
```

The script registers all seven variants and reuses the installed base weights.
It also updates the existing Dolphin Mixtral and Devstral variants if installed.
It does not start inference or modify the base models. You can run it again
after editing the files. The script resolves file paths relative to itself, so
it can also be invoked by its absolute path from another working directory.

To create just one variant:

```bash
ollama create qwen3.8-uncensored:27b -f model_files/qwen3.8-27b-uncensored.Modelfile
ollama run qwen3.8-uncensored:27b
```

In Midnight Chats, refresh the model picker, select a new variant, and start a
new conversation. Inspect a registered variant with:

```bash
ollama show --parameters qwen3.8-uncensored:27b
ollama show --system qwen3.8-uncensored:27b
```

## Recreate Devstral 98K

The Devstral file rebuilds the existing `devstral-98k:latest` variant directly
from `huihui_ai/devstral-2-abliterated:123b`, with 98,304 context tokens, no
explicit reply token cap, and an explicit uncensored system prompt. The base
model's chat formatting, tool support, and sampling defaults are inherited.

```bash
ollama create devstral-98k:latest -f model_files/devstral-98k-uncensored.Modelfile
ollama run devstral-98k:latest
```

Recreating the same name replaces its old registered configuration without
requiring a separate `ollama rm` or deleting the installed base weights.

## What the settings do

`num_ctx` sets how much context the model can attend to at once, including system
instructions, conversation history, and generated text. These values come from
the installed GGUF metadata, not assumptions based on model names. In particular,
Dolphin Llama 3 reports only 8,192 tokens, while the Erik Tonon model reports
262,144 despite its original system prompt advertising 128K. Both installed 27B
models report the `qwen35` architecture.

The metadata limit is not a guarantee that the model will load at that context
on the available hardware or remain equally accurate over long conversations.
Full context windows can require substantial additional memory. If loading fails
or performance becomes impractical, lower `num_ctx` and recreate that variant.

`num_predict -1` removes the explicit generated-token cap. It does not force a
long reply; the model can still stop naturally. Ask for thorough explanations
and examples when you want more detail. Reasoning models can generate thinking
as well as final answer text, and long generations take more time.

Each `SYSTEM` message asks for direct, candid answers without refusing merely
because a subject is controversial or sensitive. This steers behavior; it does
not retrain the model or guarantee zero refusals. In particular, the Llama 3.2
base is not converted into an uncensored fine-tune by these instructions.
The Erik Tonon variant replaces its specialized Portuguese system message with
general chat guidance. These files have not been evaluated for refusal behavior.

All variants inherit their base model's chat formatting, stop tokens, and other
generation settings. Qwen retains its native renderer/parser and vision support.
Midnight Chats currently sends no generation option overrides or system messages,
so it uses these defaults. Other clients can override them in requests.

Reference: [Ollama Modelfile documentation](https://docs.ollama.com/modelfile).
