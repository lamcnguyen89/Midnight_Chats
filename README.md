# Midnight Chats

A simple Flask chat interface for Ollama on your AGX Thor. Choose an installed
model, send messages, and watch replies stream into a responsive chat interface.
No JavaScript build tools, external fonts, CDN assets, or cloud services are needed.

- Start a new conversation or continue one from the sidebar.
- Select or refresh the models installed in Ollama.
- Live used/total GPU memory card, refreshed every five seconds.
- Changing models unloads the previous model from memory before the switch completes.
- Clear all loaded Ollama models with the button below the connection status.
- Stop generation; partial replies are marked as interrupted.
- Explicitly save chats with a **name you provide**. Names are never generated.
- Reopen, rename, update, or delete saved conversations.
- See each conversation's creation date and last saved time in your browser's timezone.
- View fenced code blocks safely; other reply text is displayed as plain text.

Chats stay in memory in the current browser tab until you click **Save conversation**.
Continuing a saved chat requires saving again to retain the new messages. Starting
another chat or leaving the page prompts you if you have unsaved changes.

When switching models, the app asks Ollama to unload the previous selection and
checks `/api/ps` until it is no longer listed. Sending is disabled while this
finishes. A failed unload keeps the previous selection and shows an error.
Opening a saved conversation with a different model follows the same rule.
The newly selected model loads when you send its first message; chat history
and model files on disk are preserved. Other clients using the same Ollama
service can load a model again independently.

## Project layout

```text
main.py                 Flask application and entry point
web/
  templates/index.html  Chat page
  static/app.js         Browser chat behavior
  static/style.css      Interface styles
  config.py             .env configuration
  network.py            Optional .local hostname publication
  hardware.py           Read-only GPU/shared-memory telemetry
  logging_config.py     Structured logging, rotation, and request tracing
  gunicorn.conf.py      Gunicorn startup and alias lifecycle
.env.example            Example local configuration
tests/                  Backend tests
deploy/                 Example systemd service
systemd/                Device startup service configured for this Thor
scripts/install.sh      Virtual environment, configuration, and service installer
scripts/toggle_services.sh  Install/start or stop/remove the systemd service
instance/               Local database and server PID (ignored by Git)
logs/                   Application, access, and server logs (ignored by Git)
```

Web templates and browser assets live in `web/`. Dependency declarations, tests,
deployment configuration, and project documentation remain at the project level.

## Run on the Thor

Use Python 3.10 or later and an existing Ollama installation with at least one chat model.
The Flask process runs on the CPU; Ollama handles inference and GPU use.

For an automatic installation, run as your normal Linux user:

```bash
./scripts/install.sh
```

The installer works from any working directory. It creates or reuses `.venv`,
installs `requirements.txt`, creates `.env` from `.env.example` only when missing,
validates the app configuration, and installs and enables the systemd service
for the next boot. It renders the unit for the actual checkout path and service
user. Existing `.env`, saved chats, and logs are preserved. Administrator access
is requested through `sudo` only for the systemd installation commands.

Python 3.10+ with venv support, sudo, and a Linux systemd installation are
required. On Ubuntu, `sudo apt install python3-venv` provides venv support for
the default Python. Ollama and Avahi must already be installed for local
inference and the friendly hostname. Set `MIDNIGHT_PYTHON=/path/to/python3`
when invoking the installer to select a different Python executable.

For a fresh installation that should also start immediately:

```bash
./scripts/install.sh --start
```

`--start` restarts an existing systemd-managed instance. It reports an error if
a manual process referenced by `instance/gunicorn.pid` is still running, so
stop that instance first. Run `./scripts/install.sh --help` for usage.

To toggle the service immediately:

```bash
./scripts/toggle_services.sh
```

If the systemd service is running or starting, this stops and disables it,
removes `/etc/systemd/system/midnight-chats.service`, and reloads systemd. If
it is absent, stopped, or failed, this runs the installer and starts it now,
with startup at boot enabled. A masked unit is unmasked for installation.
An installed unit that is stopping finishes that stop before being started.

The toggle works from any directory and requests sudo when needed. It keeps
the source unit in `systemd/`, `.venv`, `.env`, saved chats, and logs. When
switching from a manual background instance to systemd, it verifies the PID
belongs to Gunicorn running this checkout before gracefully stopping it;
unrelated processes are left untouched. Other services, including Ollama and
Avahi, are not stopped or removed by the toggle.

To set up and run the development server manually:

```bash
cd /path/to/Midnight_Chats
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Confirm Ollama is running and has installed models:
ollama list

python main.py
```

With the example configuration, open **http://midnightchats.local:5001** from the Thor
or another computer on the same LAN. The IP address also works:
**http://192.168.1.81:5001** on this Thor. If Ollama is not already running as a
service, run `ollama serve` in another terminal. If you need a first model, install
one with `ollama pull llama3.2:3b` and refresh the model picker.

For a persistent service process, use Gunicorn instead of the development server:

```bash
source .venv/bin/activate
gunicorn --config web/gunicorn.conf.py 'main:create_app()'
```

## Configuration

Edit `.env` beside `main.py` to change the friendly hostname and port:

```dotenv
MIDNIGHT_HOST=0.0.0.0
MIDNIGHT_ALIAS=midnight.local
PORT=5001
OLLAMA_URL=http://127.0.0.1:11434
```

For example, `MIDNIGHT_ALIAS=thorchat.local` and `PORT=5050` give you
`http://thorchat.local:5050`. Restart the app after changing `.env`. Both
`python main.py` and the Gunicorn command above read the file automatically,
even when launched from another working directory (use absolute paths for the
Gunicorn config and entry module search path in that case). Exported environment
variables take precedence over `.env`; Gunicorn command-line settings take
precedence over its configuration file. Avoid a hardcoded `--bind` argument if
you want `.env` to control the port.

For the background instance on this Thor, reload settings after editing `.env`
from the project directory:

```bash
kill -HUP "$(cat instance/gunicorn.pid)"
```

Gunicorn reopens the configured port, replaces the published alias, and starts
fresh workers. For a foreground `python main.py` process, stop it with Ctrl+C
and start it again.

The friendly hostname uses the Thor's existing Avahi daemon to publish a local
mDNS address mapping. It does not rename the Thor or change router settings.
Only `.local` aliases are supported; `midnight` is also accepted and becomes
`midnight.local`. Set `MIDNIGHT_ALIAS=` to disable publication. The publisher
stops when the application stops. If Avahi is missing on another Ubuntu machine:

```bash
sudo apt install avahi-daemon avahi-utils
sudo systemctl enable --now avahi-daemon
```

Client computers must support mDNS, and the LAN must allow multicast. Use
`http://<thor-ip>:<port>` if a client cannot resolve the alias. Always include the
configured port and use HTTP. `127.0.0.1` and `localhost` on another computer
refer to that computer itself. The Thor's IP may change with DHCP; restart the
app after an address change to publish the new IP. With multiple network
interfaces, set `MIDNIGHT_ADDRESS` to the Thor's desired LAN IPv4 address.

This is a single-user application without authentication: everyone who can
reach it shares the saved chat library. Keep it on a trusted private network,
or disable the alias and set `MIDNIGHT_HOST=127.0.0.1` for localhost access.

The following defaults apply when settings are absent. `.env.example` selects
LAN access with alias `midnightchats.local` and port `5001` to avoid the Image
Generator app already using port `5000` on this Thor.

| Variable | Default without `.env` | Purpose |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama base URL, without `/api` |
| `MIDNIGHT_DATABASE` | `instance/chats.sqlite3` in the checkout | SQLite file path; use an absolute path when overriding |
| `OLLAMA_TIMEOUT` | `600` | Maximum seconds waiting between chunks from Ollama |
| `OLLAMA_UNLOAD_TIMEOUT` | `30` | Seconds to wait for memory-release verification after an unload acknowledgement |
| `MIDNIGHT_HOST` | `127.0.0.1` | Interface to listen on; `0.0.0.0` enables LAN access |
| `MIDNIGHT_ALIAS` | Empty (disabled) | Friendly `.local` hostname |
| `PORT` | `5000` | HTTP port for Flask and the supplied Gunicorn config |
| `MIDNIGHT_ADDRESS` | Automatically detected | LAN IPv4 address advertised for the alias |
| `LOG_DIR` | `logs` | Log directory; relative paths resolve from the project root |
| `LOG_LEVEL` | `INFO` | Application/server severity: DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `LOG_MAX_BYTES` | `10485760` | Approximate size limit per log file before rotation (10 MiB) |
| `LOG_BACKUP_COUNT` | `5` | Number of rotated backups kept per log file |

The browser only contacts Flask. Flask calls Ollama's
[model list](https://docs.ollama.com/api/tags) and
[streaming chat](https://docs.ollama.com/api/chat) endpoints, so no Ollama CORS
configuration is needed. Run a local model to keep inference on the device.
Long model loads can take time before the first reply appears. The UI displays
thinking activity for models that emit it, without storing their internal thinking.

The SQLite database is created automatically and is ignored by Git. To back up
saved conversations, stop the app and copy `instance/chats.sqlite3`. Names are
limited to 120 characters, messages to 100,000 characters, and conversations to
1,000 messages with a 4 MiB request limit.

## Memory usage

The header's memory card refreshes every five seconds and after generation or
model unloading. On Jetson Thor it is labeled **Shared VRAM / CPU + GPU shared
memory**: CPU, GPU, and the operating system use the same physical pool. Used
memory is `MemTotal - MemAvailable`, with reclaimable caches treated as available;
the total is the usable capacity reported by Linux, not the module's nominal
capacity. This is shared-pool usage, not a GPU-only allocation counter.
[NVIDIA documents](https://developer.nvidia.com/blog/whats-new-in-cuda-toolkit-13-0-for-jetson-thor-unified-arm-ecosystem-and-more)
that SoC memory monitoring is unavailable through some Thor NVML interfaces.
Dedicated NVIDIA GPUs use `nvidia-smi` memory counters instead. Unsupported or
failed reporting shows **Unavailable** while chat remains usable. The read-only
`/api/system/vram` endpoint allocates no CUDA context; hidden tabs pause polling.

**Clear model memory**, below the Ollama connection status, unloads every model
reported by `/api/ps` and verifies that the list is empty. Controls are disabled
while clearing, and the memory card refreshes afterward. Chats, the selected
model, drafts, and downloaded model files are kept; the next message reloads
the selected model. A partial failure reports that models remain loaded so
you can retry. Other clients sharing Ollama may load models again. The card
also includes CPU/OS memory and other GPU applications, so clearing Ollama does
not make the entire shared-memory usage drop to zero.

## Logging

The app creates `logs/` automatically for both Flask and the supplied Gunicorn
configuration. Logs use one JSON object per line with UTC timestamps, severity,
logger name, process ID, and thread name:

- `logs/application.log`: app startup, database activity, model discovery/unloading,
  conversation save/update/delete events, alias lifecycle, validation failures,
  Ollama errors, and generation summaries.
- `logs/access.log`: one record per HTTP request, including method, path, client
  IP, HTTP status, response bytes, duration, and completion/disconnection outcome.
  Streaming requests are timed until the response finishes or closes.
- `logs/server.log`: Gunicorn lifecycle, worker events and failures, and
  Werkzeug warnings/errors when using `python main.py`.

Each response has an `X-Request-ID` header that matches its access record and
related application events. Generation summaries include the model, history
length, input/output character counts, chunk count, time to first reply text,
total duration, and completion outcome. A stream can return HTTP 200 and then
fail during generation; check its application outcome as well as the HTTP status.
Unexpected application exceptions include tracebacks.

Prompts, replies, internal thinking, conversation titles, request bodies, query
strings, authorization headers, and cookies are not intentionally logged.
Expected Ollama/network failures record their type/status rather than raw error
payloads. Set `LOG_LEVEL=DEBUG` in `.env` for request-start events, database
connection activity, conversation reads, and Ollama token/timing metrics.
Reload Gunicorn or restart Flask after changing logging settings.

```dotenv
LOG_DIR=logs
LOG_LEVEL=INFO
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
```

Files rotate at approximately 10 MiB by default, retaining `.1` through `.5`
for each log family. Rotation uses process-safe file locks for concurrent
Gunicorn workers and threads. Lock files in `logs/` are managed automatically.
The folder and its backups are ignored by Git. Historical files moved from
`instance/` are retained separately as `logs/legacy-access.log` and
`logs/legacy-error.log`; these archives are not part of the rotating log families.

To watch current activity:

```bash
tail -f logs/application.log logs/access.log logs/server.log
```

## Start automatically with systemd (optional)

`./scripts/install.sh` automates the setup and service installation described
below; `--start` also starts/restarts the service immediately.

`systemd/midnight-chats.service` is configured for this Thor's existing user
`malneyugnfl` and checkout at `/home/malneyugnfl/SoftwareDev/Midnight_Chats`.
Update its username and absolute paths if moving the checkout. The generic
template in `deploy/` is also available for other installations.

The startup service runs Gunicorn in the foreground under systemd, reads the
project's `.env` for its alias, port, and logging settings, and writes logs to
`logs/`. It starts after the network, Ollama, and Avahi services and restarts
automatically if the application crashes. Create the virtual environment and
install `requirements.txt` before enabling it.

Install the unit and enable startup on the next boot:

```bash
sudo cp systemd/midnight-chats.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable midnight-chats.service
```

The currently running background instance can remain active until the next
reboot. To switch to systemd immediately, stop that manual instance first so it
releases the configured port and `instance/gunicorn.pid`, then run
`sudo systemctl start midnight-chats.service`.

Once systemd manages the application:

```bash
sudo systemctl status midnight-chats.service
sudo systemctl reload midnight-chats.service   # Apply .env changes
sudo systemctl restart midnight-chats.service  # Restart the app
sudo systemctl stop midnight-chats.service
```

Application logs are written to `logs/`; use `journalctl -u midnight-chats -f`
for service-manager diagnostics. The device unit requests `ollama.service` and
`avahi-daemon.service` at startup; they must already be installed on the device.

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Tests use a temporary SQLite database and mocked Ollama responses, covering
manual naming, persistence across app restarts, updating and deleting chats,
streaming, interrupted responses, invalid requests, and offline errors.
Configuration tests also cover `.env` precedence, alias and port validation,
LAN address selection, and publisher cleanup.
Logging tests cover request correlation, privacy, streamed disconnects, error
tracebacks, bounded rotation, and concurrent processes writing the same log.
Model-unload tests verify zero keep-alive, confirmed memory release, timeout
handling, unavailable models, and upstream failure responses.
Installer tests cover fresh/repeated installation, configuration and data
preservation, generated service paths, dependency failures, and manual-process
conflicts using isolated package/systemd installation commands. Generated units
are checked with `systemd-analyze verify` without installing a real service.
Toggle tests cover immediate start/removal, failed and transitional service
states, manual-process handover, stale PID files, and unrelated-process safety.
