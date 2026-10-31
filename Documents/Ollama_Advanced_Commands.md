# Advanced Ollama Commands

A companion to [Ollama Commands: Beginner to Intermediate](Ollama_Commands.md). Frequently useful advanced tasks come first, followed by deployment and model publishing.

Examples use Bash on Linux/macOS, `curl`, and, where shown, `jq`. Replace model names, paths, and hostnames with your own. Download each model with `ollama pull` before calling it through the API. The examples below are instructions to run yourself; creating this guide does not change your server.

## 1. Control model loading and memory

```bash
# Preload a model without asking a question; retain it for 30 minutes.
ollama run llama3.2:3b --keepalive 30m ""

# Inspect memory placement, then explicitly unload the model.
ollama ps
ollama stop llama3.2:3b
```

Using the API, a negative `keep_alive` requests indefinite residency until the model is stopped, the server restarts, or scheduling needs require eviction:

```bash
curl -fsS http://localhost:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:3b","keep_alive":-1,"stream":false}'

# Empty prompt plus zero keep-alive unloads the model.
curl -fsS http://localhost:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:3b","keep_alive":0,"stream":false}'
```

CLI `--keepalive` and API `keep_alive` apply to a request. `OLLAMA_KEEP_ALIVE` sets a server default. See the [loading and keep-alive FAQ](https://docs.ollama.com/faq).

## 2. Make scripted requests with generation controls

Use `/api/generate` for a prompt and `/api/chat` for explicit conversation messages. Disable streaming when a script needs one complete JSON response:

```bash
curl -fsS http://localhost:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "llama3.2:3b",
    "prompt": "Explain why database indexes speed up lookups.",
    "system": "Use precise language and one short example.",
    "stream": false,
    "keep_alive": "10m",
    "options": {
      "temperature": 0.2,
      "seed": 42,
      "num_ctx": 8192,
      "num_predict": 512
    }
  }' | jq -r '.response'
```

A fixed seed helps compare runs, but identical output is not guaranteed across model versions, hardware, or backends. Context size must fit the model and available memory. `num_predict` caps output tokens. Enable `set -o pipefail` in automation so a failed `curl` is not hidden by a successful downstream command.

For streaming text, keep streaming enabled and process each JSON line:

```bash
curl -fsSN http://localhost:11434/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:3b","prompt":"Explain recursion.","stream":true}' \
  | jq --unbuffered -rj '.response // empty'
```

`-N` disables curl buffering. Streaming responses are newline-delimited JSON, so parse each object individually. Reference: [generate API](https://docs.ollama.com/api/generate).

## 3. Send conversation history explicitly

```bash
curl -fsS http://localhost:11434/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "llama3.2:3b",
    "stream": false,
    "messages": [
      {"role":"system","content":"You are a concise programming tutor."},
      {"role":"user","content":"What is a Python list?"},
      {"role":"assistant","content":"An ordered, mutable collection of items."},
      {"role":"user","content":"How does it differ from a tuple?"}
    ]
  }' | jq -r '.message.content'
```

For later turns, send the history again, including the previous assistant response. Keeping a model loaded does not preserve a separate API conversation. Reference: [chat API](https://docs.ollama.com/api/chat).

## 4. Require structured JSON output

Provide a JSON schema through `format` when extracting data for another program:

```bash
curl -fsS http://localhost:11434/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "llama3.2:3b",
    "stream": false,
    "messages": [{
      "role": "user",
      "content": "Extract the name and age as JSON: Maya is 28 years old."
    }],
    "format": {
      "type": "object",
      "properties": {
        "name": {"type":"string"},
        "age": {"type":"integer"}
      },
      "required": ["name","age"],
      "additionalProperties": false
    },
    "options": {"temperature":0}
  }' | jq '.message.content | fromjson'
```

The API envelope contains the generated JSON as a string, which `fromjson` parses. Validate the result in your application: correct JSON structure does not establish factual accuracy. This example targets a local model. Reference: [structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

## 5. Process text files safely in scripts

Use `jq --rawfile` to JSON-encode file contents, including quotes and newlines. Create `notes.txt` first:

```bash
set -o pipefail

jq -n --rawfile document notes.txt '{
  model: "llama3.2:3b",
  prompt: ("Summarize these notes:\n\n" + $document),
  stream: false,
  options: {num_predict: 512}
}' | curl -fsS http://localhost:11434/api/generate \
      -H 'Content-Type: application/json' \
      --data-binary @- \
  | jq -er '.response' > summary.txt
```

`>` overwrites the destination file. For a batch, repeat this workflow per input file with distinct output names; split documents that exceed the usable context window. Avoid building JSON by concatenating unescaped file contents.

## 6. Tune the server

Environment settings must reach the **server process**. Setting them on `ollama run` does not reconfigure an already running server.

For a manual foreground server, stop the existing instance first or choose another port:

```bash
OLLAMA_HOST=127.0.0.1:11435 \
OLLAMA_CONTEXT_LENGTH=8192 \
OLLAMA_KEEP_ALIVE=15m \
OLLAMA_NUM_PARALLEL=2 \
OLLAMA_MAX_LOADED_MODELS=1 \
OLLAMA_MAX_QUEUE=32 \
ollama serve
```

In a second terminal, target that server:

```bash
OLLAMA_HOST=http://127.0.0.1:11435 ollama list
OLLAMA_HOST=http://127.0.0.1:11435 ollama run llama3.2:3b
```

| Setting | What to consider |
| --- | --- |
| `OLLAMA_CONTEXT_LENGTH` | Default context size; larger windows consume more memory. |
| `OLLAMA_KEEP_ALIVE` | Default idle residency duration. |
| `OLLAMA_NUM_PARALLEL` | Concurrent requests per model; more parallel contexts need more memory. |
| `OLLAMA_MAX_LOADED_MODELS` | Limits loaded models, subject to available memory. |
| `OLLAMA_MAX_QUEUE` | Limits waiting requests; overload can return HTTP 503. |
| `OLLAMA_LOAD_TIMEOUT` | Allows longer stalled loads before giving up, for example `10m`. |
| `OLLAMA_NO_CLOUD=1` | Disables Ollama cloud features on supporting versions. |
| `OLLAMA_MODELS` | Changes the model storage directory; does not migrate existing files. |

These values are examples, not universal performance targets. Inspect supported settings with `ollama serve --help`. See the [configuration and concurrency FAQ](https://docs.ollama.com/faq).

### Persist settings with systemd on Linux

For an existing `ollama.service`, open an override:

```bash
sudo systemctl edit ollama.service
```

Add the settings you need:

```ini
[Service]
Environment="OLLAMA_CONTEXT_LENGTH=8192"
Environment="OLLAMA_KEEP_ALIVE=15m"
Environment="OLLAMA_NUM_PARALLEL=2"
```

Apply and inspect them:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama.service
systemctl show ollama.service -p Environment
journalctl -u ollama.service -n 100 --no-pager
```

Restarting interrupts active requests. Remove specific lines from the override to undo those settings, then reload and restart again. If changing `OLLAMA_MODELS`, ensure the service user can read and write the destination; move existing models separately if you want to retain them. Reference: [Linux service configuration](https://docs.ollama.com/linux).

## 7. Store and run models on an external drive

Set **`OLLAMA_MODELS` on the Ollama server** to an absolute directory on the external drive. Models are loaded from that directory using ordinary `ollama run` commands. Extra disk capacity does not replace the RAM/VRAM needed for inference; a slow drive or USB connection can increase loading time.

An existing Ollama store contains both `blobs/` and `manifests/`. Copy both directories together, preserving their layout. A standalone GGUF file requires the import workflow in section 10 instead. Changing the storage path does not automatically copy models or combine the old and new stores. See [Ollama model storage](https://docs.ollama.com/faq#where-are-models-stored).

### Linux: move an existing service's store

The example assumes the drive is already mounted at `/mnt/ollama-drive`, uses a Linux filesystem with Unix ownership support, and the service runs as `ollama`. Substitute your actual paths and service user. Use a stable mount configured by UUID through your system's disk utility or `/etc/fstab`; desktop mounts beneath a user's private media directory may be inaccessible to the service.

Inspect the drive, free space, and service settings first:

```bash
lsblk -f
findmnt --mountpoint /mnt/ollama-drive
df -h /mnt/ollama-drive
systemctl show ollama.service -p User -p Group -p Environment
```

**Continue only after confirming the external filesystem is mounted at that exact path.** The usual Linux service store is `/usr/share/ollama/.ollama/models`; if the service already overrides `OLLAMA_MODELS`, use that source instead. Stop the service before copying so downloads and model changes cannot modify the source during migration. Requires `rsync`:

```bash
sudo systemctl stop ollama.service

# The guard prevents copying onto the internal disk beneath an empty mountpoint.
if mountpoint -q /mnt/ollama-drive; then
  sudo install -d -o ollama -g ollama /mnt/ollama-drive/models
  sudo rsync -a --info=progress2 \
    /usr/share/ollama/.ollama/models/ /mnt/ollama-drive/models/
else
  printf 'External drive is not mounted; stop here.\n' >&2
fi
```

The trailing slash on the source copies its contents into `models/`. Check that the copy completed successfully before continuing. Keep the original store until the new setup is verified. If the drive already contains a complete store you want to use, skip the copy and select that directory.

Give the service ownership of only its dedicated model directory and check access:

```bash
sudo chown -R ollama:ollama /mnt/ollama-drive/models
sudo -u ollama test -r /mnt/ollama-drive/models
sudo -u ollama test -w /mnt/ollama-drive/models
sudo -u ollama test -x /mnt/ollama-drive/models
```

Each `test` should exit successfully; inspect its exit status with `echo $?` immediately afterward if needed. All parent directories must allow the service to traverse them. On exFAT/NTFS, ownership may be determined by mount options instead of `chown`; configure access for the service's UID/GID through the filesystem's mount settings.

Open `sudo systemctl edit ollama.service` and merge this into the existing override:

```ini
[Unit]
RequiresMountsFor=/mnt/ollama-drive
ConditionPathIsMountPoint=/mnt/ollama-drive

[Service]
Environment="OLLAMA_MODELS=/mnt/ollama-drive/models"
```

`RequiresMountsFor` orders startup after the configured mount; it does not create a mount configuration. The condition skips startup if the path is not a mountpoint, avoiding accidental downloads to the internal disk. See [systemd mount dependencies and conditions](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html#RequiresMountsFor=).

Apply the override and verify the models:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama.service
systemctl show ollama.service -p Environment
ollama list
ollama run llama3.2:3b "Confirm you can answer a short question."
```

Choose an already copied model from `ollama list` for that test. If you want a new download, `ollama pull llama3.2:3b` now stores it on the external drive. Inspect the external store's `manifests/` and `blobs/` directories and service logs if the list is empty. Service configuration reference: [Ollama on Linux](https://docs.ollama.com/linux).

Before disconnecting the drive, stop the service with `sudo systemctl stop ollama.service`, then unmount/eject the drive through your OS. After reconnecting, verify the mount and start the service again. To revert, stop the service, remove the external-drive settings from the override, restore any previous storage setting, reload systemd, and start the service with the original store still in place.

### Manual server, macOS, and Windows

For a manually started server, use a writable directory on the mounted drive. Replace the example path and create the directory first. Stop any existing server using port 11434:

```bash
mkdir -p "/path/to/external-drive/ollama-models"
OLLAMA_MODELS="/path/to/external-drive/ollama-models" ollama serve
```

In another terminal, run `ollama list`, `ollama pull MODEL`, and `ollama run MODEL` normally. The server's operating-system user must have access to the directory. A setting on the CLI client alone does not change server storage.

| Platform | Persistent app setup |
| --- | --- |
| macOS | Quit Ollama, run `launchctl setenv OLLAMA_MODELS "/Volumes/YourDrive/ollama-models"`, then reopen Ollama. |
| Windows | Quit Ollama from the system tray. Set a user environment variable named `OLLAMA_MODELS`, such as `E:\Ollama\models`, through Windows environment-variable settings, then reopen Ollama. Keep the drive letter stable. |

Mount the drive before opening the app. For migration, quit the app and copy the original store's contents first: normally `~/.ollama/models` on macOS or `%USERPROFILE%\.ollama\models` on Windows. The destination should directly contain `blobs/` and `manifests/`. See [Ollama environment configuration](https://docs.ollama.com/faq#how-do-i-configure-ollama-server).

## 8. Tune GPU and context-cache behavior

On supported backends, experiment with Flash Attention and an 8-bit context cache:

```bash
OLLAMA_HOST=127.0.0.1:11435 \
OLLAMA_FLASH_ATTENTION=1 \
OLLAMA_KV_CACHE_TYPE=q8_0 \
ollama serve
```

Cache quantization reduces context-cache memory; it does not quantize model weights. `f16`, `q8_0`, and `q4_0` trade memory against precision, subject to backend support. Flash Attention may already be selected automatically. Check logs and compare response quality. Reference: [Flash Attention and cache settings](https://docs.ollama.com/faq).

For a CUDA/NVIDIA server, restrict visible devices before starting the server:

```bash
nvidia-smi -L
CUDA_VISIBLE_DEVICES=0 OLLAMA_HOST=127.0.0.1:11435 ollama serve
```

GPU UUIDs can avoid index changes. GPU selection settings vary by backend: ROCm uses `ROCR_VISIBLE_DEVICES`; Vulkan uses `GGML_VK_VISIBLE_DEVICES`. Check placement with `ollama ps` against the same server. Reference: [hardware support](https://docs.ollama.com/gpu).

## 9. Compare generation performance

```bash
# Run the same request again after the model has loaded for a warm comparison.
ollama run llama3.2:3b --verbose "Explain a database index in 100 words."
```

The API also returns token counts and nanosecond timings. Save a non-streaming `/api/generate` response to `response.json`, then calculate generation throughput:

```bash
jq '{
  load_seconds: (.load_duration / 1000000000),
  generation_tokens: .eval_count,
  tokens_per_second: (
    if .eval_duration > 0
    then .eval_count / (.eval_duration / 1000000000)
    else null end
  )
}' response.json
```

Compare the same model, prompt, context settings, and output limits. Cold loads and concurrent requests affect timings. Generation throughput excludes prompt processing and loading. Reference: [generate response fields](https://docs.ollama.com/api/generate).

## 10. Import local weights and inspect templates

Create `Modelfile.gguf` containing a compatible model path:

```dockerfile
FROM /absolute/path/to/model.gguf
PARAMETER num_ctx 8192
PARAMETER temperature 0.2
SYSTEM """You are a technical assistant. State assumptions clearly."""
```

Then import and inspect:

```bash
ollama create imported-assistant -f ./Modelfile.gguf
ollama show imported-assistant --modelfile
ollama show imported-assistant --template
ollama run imported-assistant
```

For compatible Safetensors weights, use a directory instead:

```dockerfile
FROM /absolute/path/to/safetensors-directory
```

Save that as a separate Modelfile and build it with `ollama create NAME -f FILE`. Architecture, tokenizer, and template support matter; arbitrary weight files are not automatically compatible.

Check `ollama create --help` for `--quantize` targets supported by your version and backend. Ollama does not quantize GGUF files during import; prepare GGUF quantization beforehand with a suitable tool. Reference: [model imports](https://docs.ollama.com/import).

## 11. Control thinking on supported models

These examples use `qwen3`; download it first if you want to run them:

```bash
ollama pull qwen3

# Discover supported controls on versions that expose thinking metadata.
curl -fsS http://localhost:11434/api/show \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3"}' | jq '.thinking'

ollama run qwen3 --think=true "What is 17 times 23?"
ollama run qwen3 --think=false "What is 17 times 23?"
ollama run qwen3 --hidethinking "What is 17 times 23?"
```

`--hidethinking` hides displayed reasoning; it does not disable the computation. Some models support named effort levels instead of booleans. Use the values supported by the selected model and your installed version. Reference: [thinking controls](https://docs.ollama.com/capabilities/thinking).

## 12. Generate embeddings in batches

```bash
ollama pull embeddinggemma

curl -fsS http://localhost:11434/api/embed \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"embeddinggemma",
    "input":["A guide to Python lists.","An introduction to SQL indexes."],
    "truncate":false
  }' > embeddings.json
```

The `embeddings` array contains one vector per input. Use the same embedding model and dimensionality for stored documents and search queries. Setting `truncate:false` makes oversized input fail instead of silently losing its end. Reference: [embedding API](https://docs.ollama.com/api/embed).

## 13. Access another server

Point the CLI at an existing reachable server:

```bash
OLLAMA_HOST=http://192.168.1.50:11434 ollama list
```

A private SSH tunnel lets you keep the remote server bound to loopback:

```bash
ssh -N -L 11435:127.0.0.1:11434 user@remote-host
```

While the tunnel is open, use another terminal:

```bash
OLLAMA_HOST=http://127.0.0.1:11435 ollama list
```

For intentional LAN access, the server can bind to `0.0.0.0:11434` through `OLLAMA_HOST`. The local API has no built-in authentication; restrict access with network controls or an authenticated proxy before exposing it. `OLLAMA_ORIGINS` controls browser origins, not authentication. Reference: [API authentication](https://docs.ollama.com/api/authentication).

## 14. Run an isolated Docker server

Requires Docker. This maps host port 11435 to the container and keeps the endpoint on localhost:

```bash
docker run -d --name ollama-advanced \
  -p 127.0.0.1:11435:11434 \
  -v ollama-advanced-data:/root/.ollama \
  ollama/ollama

docker exec ollama-advanced ollama pull llama3.2:3b
docker exec -it ollama-advanced ollama run llama3.2:3b
docker logs --tail 100 -f ollama-advanced
docker stop ollama-advanced
docker start ollama-advanced
```

The named volume retains downloaded models. For NVIDIA acceleration, add `--gpus all` to `docker run` after installing the NVIDIA Container Toolkit; omit it for the basic container above. ARM/Jetson and other GPU backends have additional requirements. Reference: [Docker setup](https://docs.ollama.com/docker).

## 15. Publish a model to your namespace

These commands upload a model. Replace `YOUR_USERNAME` with your Ollama username, and publish only models and saved conversations you intend to share:

```bash
ollama signin
ollama cp imported-assistant YOUR_USERNAME/imported-assistant
ollama push YOUR_USERNAME/imported-assistant
```

Check the model license and any embedded prompts or message history before uploading. Signing in must authorize the server performing the push. Reference: [sharing imported models](https://docs.ollama.com/import).

## 16. Capture diagnostics

```bash
ollama --version
ollama serve --help
ollama create --help
ollama ps

# Linux service logs, including live updates; Ctrl+C stops following.
journalctl -u ollama.service -n 100 -f

# Alternative manual server with debug logging on another port.
OLLAMA_DEBUG=1 OLLAMA_HOST=127.0.0.1:11435 ollama serve
```

When investigating failures, record the version, exact model tag, backend/GPU, context size, parallelism settings, and the relevant error. Check logs for sensitive content before sharing. Reference: [troubleshooting](https://docs.ollama.com/troubleshooting).

Reference checked: October 24, 2026. CLI flags and server environment variables were checked against the installed command help. API, import, Docker, and hardware examples were reviewed against the linked official documentation. Examples were not executed against models or services.
