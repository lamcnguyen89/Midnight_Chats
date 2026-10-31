# Ollama Commands: Beginner to Intermediate

Commands are ordered roughly by how often you will need them, with everyday tasks first. Run terminal commands in your shell; type slash commands inside an active Ollama chat.

Examples use `llama3.2:3b`. Replace it with the exact model name and tag you want to use. Browse the [Ollama model library](https://ollama.com/library) for available models. A tag such as `:3b` identifies a model variant; larger models generally require more memory.

## Everyday commands

| Task | Command | What it does |
| --- | --- | --- |
| Start chatting | `ollama run llama3.2:3b` | Opens an interactive chat; downloads the model first if needed. |
| List downloaded models | `ollama list` | Shows locally available models and their disk sizes. `ollama ls` is an alias. |
| Download or update a model | `ollama pull llama3.2:3b` | Downloads the model, or fetches updates for that exact name and tag. |
| Ask one question | `ollama run llama3.2:3b "Explain DNS in simple terms."` | Prints a response and returns to your shell. |
| See loaded models | `ollama ps` | Shows models currently loaded in memory. |
| Unload a model | `ollama stop llama3.2:3b` | Frees the memory used by that model without deleting its downloaded files. |
| Remove a downloaded model | `ollama rm llama3.2:3b` | Deletes the local model entry and frees storage no longer shared with other models. You can download it again later. |
| Inspect a model | `ollama show llama3.2:3b` | Shows details such as architecture, parameters, and capabilities. |
| Show command help | `ollama --help` | Lists the commands supported by your installed version. |
| Show help for one command | `ollama run --help` | Lists options and usage examples for that command. |
| Check the version | `ollama --version` | Displays Ollama version information. |
| Start the server manually | `ollama serve` | Starts the server in the foreground when an app or background service is not already running it. |

**Memory versus disk:** `ollama list` shows stored models; `ollama ps` shows loaded models. Exiting a chat with `/bye` can leave the model loaded temporarily. Use `ollama stop` when you want to unload it immediately.

## Inside an interactive chat

Start with `ollama run llama3.2:3b`, then enter these at the `>>>` prompt:

| Command or shortcut | Purpose |
| --- | --- |
| `/bye` | Exit to your terminal. |
| `Ctrl+C` | Interrupt the current response. |
| `/clear` | Clear the conversation context and start fresh. |
| `/?` or `/help` | Display chat commands. |
| `/show info` | Inspect the current model. |
| `/show parameters` | View the model's configured parameters. |
| `/set system Answer briefly and use plain language.` | Set a system instruction for this session. |
| `/set parameter temperature 0.3` | Reduce randomness; higher values generally produce more varied answers. |
| `/set parameter num_ctx 8192` | Request an 8,192-token context window, subject to model support and available memory. |
| `/set parameter num_predict 512` | Limit generated output to approximately 512 tokens. |
| `/set verbose` | Display response timing and generation statistics. |
| `/set quiet` | Turn those statistics off. |
| `/save my-session` | Save the current conversation as a local model/session. |
| `/load my-session` | Load that saved session. |

For a multiline prompt, put triple double quotes on the opening and closing lines:

```text
"""
Summarize the following notes:
First point...
Second point...
"""
```

Session settings are temporary unless saved. Larger context windows use more memory. Tokens are pieces of text, not necessarily whole words. See the [interactive command implementation](https://github.com/ollama/ollama/blob/main/cmd/interactive.go) for the current slash-command reference.

## Useful options and file workflows

These file examples use Bash or a similar shell on Linux/macOS. Create `notes.txt` first and replace example paths with your own. The `>` operator overwrites an existing output file; use `>>` to append instead.

```bash
# Show response timing and token-generation statistics.
ollama run llama3.2:3b --verbose "Explain what a GPU does."

# Request JSON output; also tell the model what JSON you want.
ollama run llama3.2:3b --format json "Return a JSON object with a title and three study tips."

# Keep the model loaded for 10 minutes after the request.
ollama run llama3.2:3b --keepalive 10m "Hello!"

# Unload the model after this request finishes.
ollama run llama3.2:3b --keepalive 0 "Hello!"

# Send a prompt followed by a text file as input.
{ printf 'Summarize these notes:\n\n'; cat notes.txt; } | ollama run llama3.2:3b

# Save the response to a text file.
ollama run llama3.2:3b "Give me a five-step study plan." > study-plan.txt
```

For an image, choose a vision-capable model and include the image's local path in the prompt. Text-only models cannot interpret images. Check supported options with `ollama run --help`.

## Inspect, copy, and customize models

```bash
# Inspect configuration, instructions, or license information.
ollama show llama3.2:3b --modelfile
ollama show llama3.2:3b --parameters
ollama show llama3.2:3b --system
ollama show llama3.2:3b --license

# Create another local name for an existing model.
ollama cp llama3.2:3b my-assistant
```

To give an assistant reusable instructions, save this text in a file named `Modelfile`:

```dockerfile
FROM llama3.2:3b
PARAMETER temperature 0.3
PARAMETER num_ctx 4096
SYSTEM """You are a helpful tutor. Explain concepts clearly and include short examples."""
```

From the directory containing that file, run:

```bash
ollama pull llama3.2:3b
ollama create my-tutor -f ./Modelfile
ollama run my-tutor
```

This saves a configuration based on the existing model; it does not train new model weights. See the [Modelfile reference](https://docs.ollama.com/modelfile) for more customization options.

## Optional account and integration commands

| Command | Purpose |
| --- | --- |
| `ollama signin` | Sign in for features that require an Ollama account. |
| `ollama signout` | Sign out. |
| `ollama launch` | Open the integration/menu workflow on versions that support it. |

An account is not required to chat with downloaded local models. Consult the [CLI reference](https://docs.ollama.com/cli) for current integration and account features.

## Basic troubleshooting

| Problem | What to try |
| --- | --- |
| Cannot connect to Ollama | Start the Ollama app or service. For a manual foreground server, run `ollama serve` in another terminal. |
| `ollama serve` says the address is already in use | Ollama may already be running. Try `ollama list` before starting another server. |
| Model name is not found | Check spelling and the tag in the model library, then run `ollama pull` with that exact name. |
| Responses are slow or memory is low | Check `ollama ps`, unload unused models, try a smaller model, or reduce the context window. |
| Unsure whether the GPU is being used | Inspect the `PROCESSOR` column of `ollama ps` for CPU/GPU allocation. |
| An option is not recognized | Check `ollama --version` and that command's `--help`; options vary by version. |

On Linux installations managed by systemd, these commands can help:

```bash
systemctl status ollama
sudo systemctl start ollama
sudo systemctl restart ollama
journalctl -u ollama -n 50 --no-pager
```

Restarting the service interrupts active requests. For a manually started foreground server, press `Ctrl+C` in its terminal to stop it. See the [Ollama FAQ](https://docs.ollama.com/faq) and [troubleshooting guide](https://docs.ollama.com/troubleshooting) for service and resource issues.

Reference checked: October 24, 2026. Core command syntax and flags were also checked against the locally installed CLI's help output.
