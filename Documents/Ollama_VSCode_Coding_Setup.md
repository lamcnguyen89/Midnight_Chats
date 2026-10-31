# Edit Code in VS Code with Downloaded Ollama Models

This guide connects Ollama coding models to VS Code so an assistant can read project files, propose changes, edit code, and run relevant checks. It uses **Devstral 2 Abliterated 123B** as the main example.

Start with the official Ollama extension and VS Code's Agent interface. Cline is an alternative if you prefer its separate coding-agent panel. Choose one workflow initially so you can tell which assistant is making changes.

Checked October 24, 2026. Extension interfaces change; the linked official documentation is the reference for newer versions.

## How the integration works

```text
Your request in VS Code
        |
        v
VS Code Agent or Cline ---- reads/edits files and runs project tools
        |
        v
Ollama HTTP server ---- runs the downloaded model
        |
        v
Devstral generates an answer or requests a tool action
```

The editor integration supplies file access and editing tools. Running `ollama run` alone gives you a terminal conversation; it does not connect the model to your VS Code workspace. Agent editing also requires working tool calling, not just the ability to generate source code. See [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling).

## 1. Identify the exact model and its requirements

The community model used here is:

```text
huihui_ai/devstral-2-abliterated:123b
```

Its [published tag](https://ollama.com/huihui_ai/devstral-2-abliterated:123b) is approximately **75 GB**, uses **Q4_K_M** quantization, and advertises tool support. The [tag listing](https://ollama.com/huihui_ai/devstral-2-abliterated/tags) reports a maximum context of **256K tokens**. This is a community modification of Mistral's Devstral 2; it is distinct from the original `devstral-2` model.

The download size is not the total runtime memory requirement. Model weights, the context cache, runtime allocations, the operating system, and your editor all need space. A 75 GB model will not fit entirely in a 24 GB GPU. CPU offloading may allow it to run with sufficient system RAM, but interactive edits can become much slower. Unified-memory systems still need headroom beyond the weights. Measure your actual configuration before expecting responsive repository-wide work.

The abliterated variant uses the same integration process as other Ollama models. The modification does not establish that it edits code or calls tools more reliably than the original.

In a terminal, check the installed models:

```bash
ollama --version
ollama list
```

Use the exact name and tag shown in `ollama list`, including the publisher prefix. If the model is already there, you can skip downloading it. Otherwise, this optional command downloads approximately 75 GB:

```bash
ollama pull huihui_ai/devstral-2-abliterated:123b
```

During preparation of this guide, that model was not listed on the local Ollama server. No model was downloaded for this document.

## 2. Verify Ollama before configuring VS Code

Start the Ollama app or existing service. On a Linux systemd installation:

```bash
systemctl status ollama
# Start it only if it is stopped.
sudo systemctl start ollama
```

For a manually managed installation, run `ollama serve` in a separate terminal instead. An address-in-use error often means the server is already running.

Inspect and test your model:

```bash
ollama show huihui_ai/devstral-2-abliterated:123b
ollama run huihui_ai/devstral-2-abliterated:123b "Write a short Python function that adds two integers."
```

Check the `Capabilities` section for `tools`. A successful text response confirms inference works, but does not yet prove tool calls work with an editor.

To check the HTTP endpoint without loading a model:

```bash
curl -fsS http://127.0.0.1:11434/api/tags
```

If these checks fail, fix Ollama first. The editor uses this same server. Command details are in the [basic guide](Ollama_Commands.md).

## 3. Give coding tasks enough context

Agent requests contain instructions, tool definitions, file contents, and conversation history. A model's advertised context limit can be much larger than the context Ollama actually allocates.

Ollama's [coding integration guidance](https://docs.ollama.com/context-length) calls for at least **64K context** for agent and coding workflows. Use **65536** tokens as a starting target if your hardware can support it. Loading 123B weights plus this context may exceed your available memory. A smaller context can support limited experiments, but may truncate instructions or prevent larger agent tasks from working well.

For an existing Linux service:

```bash
sudo systemctl edit ollama.service
```

Add these lines to the override, or merge them with existing settings:

```ini
[Service]
Environment="OLLAMA_CONTEXT_LENGTH=65536"
Environment="OLLAMA_NUM_PARALLEL=1"
```

Then apply them:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama.service
```

Restarting interrupts active requests. For a manual foreground server, stop the previous process and use:

```bash
OLLAMA_CONTEXT_LENGTH=65536 OLLAMA_NUM_PARALLEL=1 ollama serve
```

On installations with Ollama's graphical app, adjust context in its settings instead. After changing context, reload the VS Code window and start a new chat. Extension request settings can override server defaults; setting a parameter inside a terminal chat does not configure the editor's separate requests.

Once an editor request is active, run:

```bash
ollama ps
```

Inspect `CONTEXT` and `PROCESSOR`, when exposed by your version, to check actual allocation and CPU/GPU placement. More server tuning is in the [advanced guide](Ollama_Advanced_Commands.md).

## 4. Connect the official Ollama extension

The current [Ollama VS Code integration](https://docs.ollama.com/integrations/vscode) requires **VS Code 1.127 or newer**. Check **Help → About** or run `code --version` if the command is available.

1. Open VS Code's Extensions view with `Ctrl+Shift+X` on Windows/Linux or `Cmd+Shift+X` on macOS.
2. Install [Ollama, published by Ollama](https://marketplace.visualstudio.com/items?itemName=Ollama.ollama). Its extension ID is `Ollama.ollama`.
3. Open the project folder you want to edit.
4. Open VS Code's Chat view and its model picker.
5. Select `huihui_ai/devstral-2-abliterated:123b` from the **Ollama** group.

Alternatively, install the extension through VS Code's command-line tool:

```bash
code --install-extension Ollama.ollama
```

The extension discovers the server at `http://127.0.0.1:11434`. If the model is missing, open the Command Palette and run **Ollama: Refresh Models**. Use **Ollama: Diagnose Models** and the **Ollama** output channel to investigate discovery problems.

Current VS Code documentation deprecates its older built-in Ollama provider in favor of this extension. Local BYOK models can be used without a GitHub account or Copilot plan; organization policies can still restrict availability. See [VS Code model configuration](https://code.visualstudio.com/docs/agent-customization/language-models).

## 5. Use the model to make actual edits

In Chat, select **Agent** rather than a question-only mode. If your version displays a session target or harness, use the local **Copilot** harness, then explicitly choose your Ollama model. Here, the harness supplies editor tools while Ollama supplies inference. Select manual permissions while establishing the workflow.

For versions using the newer Agent Host, VS Code documents the experimental `chat.agentHost.byokModels.enabled` setting for BYOK models. Enable it if the model is available in ordinary Chat but absent from that harness. Older versions may expose a simpler Agent mode selector.

Follow the editor's [agent workflow](https://code.visualstudio.com/docs/agents/quickstart?agent-surface=chat-view), substituting your explicitly selected Ollama model for the quickstart's Auto model selection.

For an existing Git repository, first inspect your working tree. If appropriate, create a branch for the task:

```bash
git status --short
git switch -c local-ollama-edit
```

This example creates a branch; use a new name if it already exists. Preserve any work you already have in progress.

Start with a narrowly scoped task. Attach the relevant file using the chat's context/file picker, then ask:

```text
Read src/parser.py and its existing tests.
Update parse_port so it rejects values outside 1 through 65535.
Preserve the existing public interface and error style.
Add boundary tests using the project's current test framework.
Edit the files, run the relevant tests, and explain the diff.
```

Replace the filenames and task with ones from your project. Review the proposed tool actions, inspect the resulting changes, and check the reported test output. If the model only prints a code block, tell it to apply the change to the workspace and confirm that the session is in Agent mode with editing tools available.

Inspect the result through VS Code Source Control or:

```bash
git diff --stat
git diff
```

A useful first success is one small file edit plus a relevant passing check. Expand to multiple files after that works reliably.

## 6. Alternative: use Cline's VS Code extension

Cline provides its own workspace-reading, editing, and terminal workflow. Use this route if you prefer a separate agent interface or have compatibility issues with the built-in Chat route.

1. Install the official [Cline extension](https://cline.bot/install) and open its sidebar. See [installation instructions](https://docs.cline.bot/getting-started/installing-cline).
2. Open Cline Settings and choose **Ollama** as the provider.
3. Set the base URL to `http://127.0.0.1:11434`.
4. Select the exact model `huihui_ai/devstral-2-abliterated:123b`.
5. If Plan and Act have separate model settings, choose Ollama and the same model for both.
6. Enable **Use Compact Prompt** under Settings → Features if available; Cline recommends this for local models.

Use Cline's native Ollama provider with the server root URL above. Do not append `/v1` or `/api/chat` to that base URL. See [Cline local model configuration](https://docs.cline.bot/running-models-locally/overview).

In **Plan** mode, ask it to inspect the relevant files and explain the intended change. Switch to **Act** mode when ready for edits and testing. Review the diff and terminal actions through Cline. See [Plan and Act](https://docs.cline.bot/core-workflows/plan-and-act).

You configure VS Code Chat and Cline separately. Selecting Devstral in one does not select it in the other.

## 7. When Ollama runs on another computer

For a model this large, Ollama may run on a server while VS Code runs on your laptop. The editor does not need its own copy of the model; it needs access to the inference endpoint.

One option is to leave the server listening on its own loopback address and forward its port over SSH:

```bash
ssh -N -L 11435:127.0.0.1:11434 user@ollama-server
```

In another local terminal, test the tunnel:

```bash
curl -fsS http://127.0.0.1:11435/api/tags
```

For Cline running locally, use `http://127.0.0.1:11435` as its Ollama base URL. Keep the SSH session open. For the official Ollama extension, check its installed endpoint settings; the documented default is port 11434. If your version cannot change the endpoint, forward local port 11434 instead, provided a local Ollama instance is not already using it.

With Remote SSH, WSL, or Dev Containers, check where the extension executes. `127.0.0.1` refers to that environment, which may differ from the machine running Ollama. Test connectivity from the environment hosting the integration.

The Ollama local API does not require authentication. Keep it on loopback for this tunnel setup; a public listener requires appropriate access controls. See [Ollama authentication](https://docs.ollama.com/api/authentication).

## 8. Troubleshooting

| Symptom | Checks and next step |
| --- | --- |
| Model appears in `ollama list` but not VS Code | Refresh models, run Diagnose Models, and confirm the editor reaches the same server. |
| Model works in Chat but is missing from Agent | Check tool capability, the selected harness, and applicable BYOK settings. |
| Model writes prose or code blocks but makes no edits | Select Agent or Cline Act; provide file context and ask it to edit the workspace. |
| Tools are printed as plain text, rejected, or malformed | Update Ollama and the extension; inspect capabilities and the model's original template. Try a fresh small task to distinguish context issues from tool-format incompatibility. |
| First response takes a very long time | Check loading, RAM/VRAM use, and CPU offloading. Try the same model directly in the terminal. |
| Official extension times out before inference begins | Check its **Ollama: Inference Timeout Minutes** setting. Increase it only after checking that the server is making progress. |
| Agent loses instructions or reports a full context | Inspect actual `ollama ps` context, start a fresh conversation, and attach fewer files. |
| Out-of-memory error | Reduce context or concurrent model use, unload unused models, or choose a smaller model. |
| Connection refused in WSL/container/Remote SSH | Confirm the extension's host environment and route to the Ollama endpoint. |
| Editing works but inline Tab completion does not | Chat/agent integration and autocomplete use separate extension capabilities. |

For template inspection, use:

```bash
ollama show huihui_ai/devstral-2-abliterated:123b --template
ollama show huihui_ai/devstral-2-abliterated:123b --parameters
```

Avoid replacing the model's template with a generic chat template; doing so can break tool serialization. A `tools` capability flag is useful metadata, but successful tool use in your actual extension is the practical compatibility check.

For the official extension's discovery and timeout controls, see its [Marketplace documentation](https://marketplace.visualstudio.com/items?itemName=Ollama.ollama).

## 9. Separate code editing from autocomplete

Devstral 123B is a candidate for deliberate editing tasks, where each request can take time. Inline completion needs low latency and often a model trained for filling code between a prefix and suffix.

VS Code's local BYOK chat integration does not automatically provide local inline suggestions. If you want local Tab completion, use a dedicated completion extension and configure a smaller supported coding model separately. Confirm the extension's current configuration and model support before adding it. See [VS Code's local model limitations](https://code.visualstudio.com/docs/agent-customization/language-models).

## Confirm the setup works

You have a working integration when the model is selected in the editing interface, the agent reads a real project file, it changes that file through an editor tool, and you can inspect the resulting diff and relevant checks.

This document was checked against current official integration documentation and local CLI help. The local model list was inspected, but Devstral 123B was not downloaded or loaded, no extensions were installed, and an editor session was not exercised end to end.
