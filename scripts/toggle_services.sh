#!/usr/bin/env bash
# Toggle only the Midnight Chats service; retain the application and its data.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: ./scripts/toggle_services.sh

Running/starting service: stop, disable, and remove the installed systemd unit.
Absent/stopped/failed service: install, enable, and start it immediately.

The application, .venv, .env, saved chats, logs, and source service file are kept.
An existing manual Gunicorn instance for this checkout is gracefully stopped
when switching to systemd. Run as your normal user; sudo is requested as needed.
EOF
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

if (( $# > 0 )); then
    if (( $# == 1 )) && [[ "$1" == --help || "$1" == -h ]]; then
        usage
        exit 0
    fi
    usage >&2
    fail "This script takes no arguments other than --help."
fi

script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
project_dir=$(cd -- "$(dirname -- "$script_path")/.." && pwd -P)
service_name=midnight-chats.service
installed_unit="/etc/systemd/system/$service_name"
command -v systemctl >/dev/null || fail "systemctl is required."
if (( EUID != 0 )); then
    command -v sudo >/dev/null || fail "sudo is required to manage the system service."
fi
trap 'printf "Service toggle failed at line %s. Check the reported error before retrying.\n" "$LINENO" >&2' ERR

as_root() {
    if (( EUID == 0 )); then
        "$@"
    else
        sudo -- "$@"
    fi
}

service_state=$(systemctl show "$service_name" --property=ActiveState --value)
case "$service_state" in
    active|activating|reloading)
        printf 'Stopping and removing %s.\n' "$service_name"
        as_root systemctl stop "$service_name"
        as_root systemctl disable "$service_name"
        as_root rm -f -- "$installed_unit"
        as_root systemctl daemon-reload
        # The unit may already be unloaded after removal.
        as_root systemctl reset-failed "$service_name" >/dev/null 2>&1 || true
        printf 'Service stopped, disabled, and removed. Application files and data are preserved.\n'
        exit 0
        ;;
    deactivating)
        # Wait for an earlier stop job before installing/starting again.
        as_root systemctl stop "$service_name"
        ;;
esac

[[ -x "$project_dir/scripts/install.sh" ]] || fail "Missing executable scripts/install.sh."
if [[ $(systemctl show "$service_name" --property=LoadState --value) == masked ]]; then
    as_root systemctl unmask "$service_name"
fi

printf 'Installing and starting %s.\n' "$service_name"
# Prepare and validate everything before interrupting a manually running app.
"$project_dir/scripts/install.sh"

as_root "$project_dir/.venv/bin/python" - "$project_dir" <<'PY'
import os
import signal
import sys
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
pidfile = root / "instance/gunicorn.pid"
if not pidfile.exists():
    raise SystemExit(0)
value = pidfile.read_text().strip()
if not value.isdecimal() or int(value) <= 1:
    raise SystemExit("Invalid instance/gunicorn.pid; refusing to signal an unknown process.")
pid = int(value)
process = Path(f"/proc/{pid}")


def identity():
    try:
        # Fields after the process name start with state (3); starttime is 22.
        fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
        return fields[0], fields[19]
    except FileNotFoundError:
        return None


def clear_old_pidfile():
    if pidfile.exists() and pidfile.read_text().strip() == value:
        pidfile.unlink()


original = identity()
if original is None or original[0] == "Z":
    clear_old_pidfile()
    raise SystemExit(0)
try:
    command = [os.fsdecode(part) for part in (process / "cmdline").read_bytes().split(b"\0") if part]
    cwd = (process / "cwd").resolve(strict=True)
except FileNotFoundError:
    clear_old_pidfile()
    raise SystemExit(0)
is_gunicorn = (len(command) > 1 and Path(command[1]).name == "gunicorn") or command[1:3] == ["-m", "gunicorn"]
if cwd != root or not is_gunicorn or "main:create_app()" not in command:
    raise SystemExit(f"PID {pid} is not a Midnight Chats Gunicorn process for this checkout; it was left untouched.")
current = identity()
if current is None or current[0] == "Z":
    clear_old_pidfile()
    raise SystemExit(0)
if current[1] != original[1]:
    raise SystemExit("The PID file process changed during verification; rerun the toggle.")
print(f"Stopping manual Midnight Chats instance (PID {pid}).", flush=True)
try:
    os.kill(pid, signal.SIGTERM)
except ProcessLookupError:
    pass
deadline = time.monotonic() + 45
while True:
    current = identity()
    if current is None or current[0] == "Z" or current[1] != original[1]:
        clear_old_pidfile()
        break
    if time.monotonic() >= deadline:
        raise SystemExit("Manual instance has not stopped; the systemd unit is installed but was not started. Check the app logs and retry.")
    time.sleep(0.1)
PY

as_root systemctl reset-failed "$service_name" >/dev/null 2>&1 || true
as_root systemctl start "$service_name"
systemctl --no-pager --full status "$service_name"
printf '\n%s is running and enabled at boot.\n' "$service_name"
