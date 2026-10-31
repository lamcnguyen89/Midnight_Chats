# Set Ollama Context and Create a Quick Alias

The model's download size describes storage on disk. Running it also needs memory for context and inference, so download size alone cannot tell you whether it will fit in GPU VRAM.

Examples use `llama3.2:3b`, an 8,192-token context, and Bash on Linux. Replace the model name and context size as needed. Ollama must be installed and its app or server running. These are instructions to run yourself; this document does not change your server or shell settings.

## Why download size does not equal VRAM required

Think of the memory budget for running a model entirely on the GPU as:

```text
Required free VRAM ≈ model weights + context cache + inference working memory
```

| Component | Why it matters |
| --- | --- |
| Model weights | The learned parameters account for much of the download. Weight quantization reduces their size, but the disk file is not a measurement of all runtime allocations. |
| Context cache (KV cache) | Stores attention keys and values for tokens. Its size depends on context length, model architecture, cache precision, and parallel requests. |
| Inference working memory | Computation buffers, temporary activations, and backend overhead need additional space. Their size varies with the model and workload. |
| Other GPU use | Your desktop and other applications reduce the free VRAM available to Ollama. Compare against free memory, not just the GPU's advertised capacity. |

For example, **a hypothetical** model needing 6 GiB for weights, 2 GiB for its context cache, and 1 GiB for working memory needs about 9 GiB of free VRAM. A download around 6 GB therefore does not guarantee it will run entirely on an 8 GB GPU. These numbers illustrate the budget; they are not measurements of `llama3.2:3b`.

For a conventional transformer with the same cache format and parallelism, increasing context from 8,192 to 32,768 tokens makes the KV cache roughly four times as large. It does not multiply the weights by four. A model quantized to 4-bit weights can still use a higher-precision KV cache. Ollama documents cache quantization and parallel-request memory growth in its [memory configuration FAQ](https://docs.ollama.com/faq#how-can-i-set-the-quantization-type-for-the-kv-cache).

There is no universal “download size × multiplier” that gives an exact VRAM requirement. Ollama can use CPU RAM for some or all of the model, usually with lower performance. On systems with unified memory, such as Apple Silicon, CPU and GPU share the memory pool.

## What context length means

Context is the amount of tokenized information the model can consider at once: instructions, conversation history, supplied documents, and generated text. Tokens are pieces of text, not necessarily whole words. Leave room for an answer when budgeting input.

`num_ctx` sets the context window; `num_predict` limits generated output. Increasing context does not train the model or increase its supported maximum. Inspect the model with `ollama show` and choose a value within its supported context length and your memory budget.

Ollama defaults can vary by version and hardware, so set the value explicitly for repeatable behavior. Larger contexts require more memory; see Ollama's [context length guide](https://docs.ollama.com/context-length).

## Set context before loading a large model

`ollama run` can load a model before you can type `/set parameter num_ctx`. If the default context needs too much memory, changing it interactively comes too late. Use a Modelfile to set the context **before the first load**.

This matters for `huihui_ai/devstral-2-abliterated:123b` on a Jetson AGX Thor. The inspected Q4_K_M model occupies about 69.8 GiB on disk and supports 262,144 context tokens. A conventional FP16 KV cache at that context needs approximately another 88 GiB, exceeding this machine's roughly 122 GiB of usable shared memory before working buffers. Ollama's [documented automatic defaults](https://docs.ollama.com/context-length) can select 256K on systems reporting at least 48 GiB of VRAM; this is a possible explanation for a startup crash, not a confirmed diagnosis.

Start with an explicit 8,192-token context while investigating. Create and inspect the configuration first; these commands reuse the downloaded weights without starting inference:

```bash
cat > Modelfile.devstral-8k <<'EOF'
FROM huihui_ai/devstral-2-abliterated:123b
PARAMETER num_ctx 8192
EOF

ollama create devstral-8k -f ./Modelfile.devstral-8k
ollama show devstral-8k --parameters
```

The command writes `Modelfile.devstral-8k` in the current directory, replacing it if present. Confirm that `num_ctx` is `8192`. The FP16 cache estimate at this setting is 2.75 GiB, in addition to weights and runtime allocations; a lower context reduces memory demand but does not rule out other causes of a crash.

When ready to test inference, use the configured name:

```bash
ollama run devstral-8k
```

For a shortcut, add `alias dev8k='ollama run devstral-8k'` to `~/.bashrc` and reload with `source ~/.bashrc`. In an editor or API client, select `devstral-8k` too, and remove any conflicting context override. Increase context only after establishing that the smaller setting runs reliably. The [Modelfile reference](https://docs.ollama.com/modelfile) documents saved context parameters.

## 1. Change context interactively for a model that already loads reliably

For a large model or one that crashes during loading, use the preceding Modelfile method first. The following interactive example is for a model that already loads successfully.

Download and inspect the example model, then start a chat:

```bash
ollama pull llama3.2:3b
ollama show llama3.2:3b
ollama run llama3.2:3b
```

At Ollama's `>>>` prompt, enter:

```text
/set parameter num_ctx 8192
/show parameters
Explain why the sky is blue in one paragraph.
```

The setting applies to this session. Type `/bye` to return to your shell. The slash-command syntax is documented in the [context window FAQ](https://docs.ollama.com/faq#how-can-i-specify-the-context-window-size).

## 2. Save the context in a reusable model configuration

Create a dedicated Modelfile from your terminal. The following command writes `Modelfile.llama32-8k` in the current directory, replacing that file if it exists:

```bash
cat > Modelfile.llama32-8k <<'EOF'
FROM llama3.2:3b
PARAMETER num_ctx 8192
EOF

ollama create llama32-8k -f ./Modelfile.llama32-8k
ollama show llama32-8k --parameters
ollama run llama32-8k
```

`llama32-8k` is now a local model name with a saved context default. This reuses the base model's weights; it does not train a new model. To change the saved context later, edit `PARAMETER num_ctx` and rerun `ollama create`. See the [Modelfile reference](https://docs.ollama.com/modelfile).

## 3. Create a short shell alias

For Bash, open your shell startup file:

```bash
nano ~/.bashrc
```

Add this line once, then save and close the editor:

```bash
alias ai8k='ollama run llama32-8k'
```

Reload the file and use the shortcut:

```bash
source ~/.bashrc
ai8k
```

You can also send a single prompt:

```bash
ai8k "Explain Linux file permissions with an example."
```

The alias expands to `ollama run llama32-8k`; the saved model configuration supplies the context default. New interactive Bash terminals load the alias from `~/.bashrc`. For Zsh, put the same alias in `~/.zshrc` and reload it with `source ~/.zshrc`.

## 4. Verify the allocated context and GPU use

After sending a prompt, leave the chat open and run these commands in another terminal:

```bash
ollama ps

# NVIDIA GPUs only: inspect overall GPU memory use.
nvidia-smi
```

Check `CONTEXT` for `8192` and `PROCESSOR` for GPU placement. `100% GPU` indicates full GPU loading; a CPU/GPU split indicates partial offloading. `ollama ps` reports loaded models, while `ollama list` reports disk storage. Use the GPU monitor to assess actual device memory use. Ollama explains these columns in its [context verification guide](https://docs.ollama.com/context-length#check-allocated-context-length-and-model-offloading).

If memory is insufficient, try `4096`, rebuild the saved configuration, and retest. Unload an unused model with `ollama stop MODEL_NAME`, or choose a smaller model. Test a representative prompt before increasing context further.

## Optional: set a default for the whole server

For a manually started server, set the environment variable on **`ollama serve`**:

```bash
OLLAMA_CONTEXT_LENGTH=8192 ollama serve
```

Use this when no app or service is already listening on Ollama's port. Run chat commands in a second terminal.

For a Linux systemd installation, run:

```bash
sudo systemctl edit ollama.service
```

Add and save:

```ini
[Service]
Environment="OLLAMA_CONTEXT_LENGTH=8192"
```

Then apply it; restarting interrupts active requests:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama.service
```

Server variables must reach the server process. Setting `OLLAMA_CONTEXT_LENGTH` before `ollama run` does not reconfigure an existing server. Explicit model or request `num_ctx` values override this server default. For the `ai8k` shortcut, the Modelfile method above is sufficient. See the [server configuration FAQ](https://docs.ollama.com/faq#how-do-i-configure-ollama-server).

References checked: October 25, 2026. CLI command syntax was also checked against the installed Ollama help output.
