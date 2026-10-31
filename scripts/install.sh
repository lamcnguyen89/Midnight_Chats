#!/usr/bin/env bash
# Install Midnight Chats and its boot service without replacing local settings.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: ./scripts/install.sh [--start]

Create/reuse .venv, install requirements, create .env if missing, and install
and enable midnight-chats.service at boot. Existing .env and chats are kept.

  --start    Also start/restart the systemd service immediately.
  -h, --help Show this help.

Run as your normal Linux user; sudo is used for systemd installation only.
MIDNIGHT_PYTHON may select a Python 3.10+ executable (default: python3).
EOF
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

start_now=false
for argument in "$@"; do
    case "$argument" in
        --start) start_now=true ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "Unknown option: $argument" ;;
    esac
done

script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
project_dir=$(cd -- "$(dirname -- "$script_path")/.." && pwd -P)
venv_dir="$project_dir/.venv"
service_name=midnight-chats.service
service_destination="/etc/systemd/system/$service_name"
temporary_dir=""

cleanup() {
    if [[ -n "$temporary_dir" ]]; then
        rm -rf -- "$temporary_dir"
    fi
}
trap cleanup EXIT
trap 'printf "Installation failed at line %s. Fix the reported error and rerun the installer.\n" "$LINENO" >&2' ERR

for required in systemctl systemd-analyze install id mktemp; do
    command -v "$required" >/dev/null || fail "Required command not found: $required"
done
for required in requirements.txt .env.example systemd/midnight-chats.service web/gunicorn.conf.py main.py; do
    [[ -f "$project_dir/$required" ]] || fail "Missing project file: $required"
done

if (( EUID == 0 )); then
    service_user=${SUDO_USER:-$(stat -c '%U' -- "$project_dir")}
else
    service_user=$(id -un)
    command -v sudo >/dev/null || fail "sudo is required to install the system service."
fi
id "$service_user" >/dev/null || fail "Unknown service user: $service_user"
if (( EUID == 0 )) && [[ "$service_user" != root ]]; then
    command -v runuser >/dev/null || fail "runuser is required when installing with sudo."
fi

as_user() {
    if (( EUID == 0 )) && [[ "$service_user" != root ]]; then
        runuser -u "$service_user" -- "$@"
    else
        "$@"
    fi
}

as_root() {
    if (( EUID == 0 )); then
        "$@"
    else
        sudo -- "$@"
    fi
}

python_bin=$(command -v -- "${MIDNIGHT_PYTHON:-python3}") || fail "Python 3.10 or later is required."
check_python='import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10 or later is required.")'
as_user "$python_bin" -c "$check_python"
as_user test -w "$project_dir" || fail "The service user cannot write to $project_dir"

printf 'Installing Midnight Chats in %s for %s\n' "$project_dir" "$service_user"
if [[ -e "$venv_dir" || -L "$venv_dir" ]]; then
    [[ -x "$venv_dir/bin/python" ]] || fail "Existing .venv has no working Python. Repair it or move it aside, then rerun."
    printf 'Reusing existing virtual environment.\n'
else
    printf 'Creating virtual environment.\n'
    if ! as_user "$python_bin" -m venv "$venv_dir"; then
        fail "Could not create .venv. On Ubuntu, install Python venv support with: sudo apt install python3-venv"
    fi
fi
as_user "$venv_dir/bin/python" -c "$check_python"
as_user "$venv_dir/bin/python" -m pip install -r "$project_dir/requirements.txt"
as_user "$venv_dir/bin/python" -m pip check

if [[ -e "$project_dir/.env" || -L "$project_dir/.env" ]]; then
    [[ -f "$project_dir/.env" ]] || fail ".env exists but is not a regular file."
    printf 'Keeping existing .env.\n'
else
    as_user install -m 600 -- "$project_dir/.env.example" "$project_dir/.env"
    printf 'Created .env from .env.example.\n'
fi
as_user mkdir -p -- "$project_dir/instance" "$project_dir/logs"

# Validate .env, dependencies, database/log access, and app imports as the user
# who will run the service. Never source .env as shell code.
(
    cd -- "$project_dir"
    as_user "$venv_dir/bin/python" -m gunicorn --check-config --config "$project_dir/web/gunicorn.conf.py" 'main:create_app()'
)

temporary_dir=$(as_user mktemp -d)
temporary_unit="$temporary_dir/$service_name"
as_user "$venv_dir/bin/python" - "$project_dir" "$service_user" "$temporary_unit" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
user = sys.argv[2]
destination = Path(sys.argv[3])
if any(character in str(root) for character in "\n\r\t"):
    raise SystemExit("Systemd installation requires a checkout path without control characters.")


def argument(value):
    # systemd command lines have quoting, percent specifiers, and $ expansion.
    value = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + value.replace("%", "%%").replace("$", "$$") + '"'


settings = {
    "User": user,
    "WorkingDirectory": str(root).replace("%", "%%"),
    "ExecStartPre": "/usr/bin/mkdir -p " + argument(root / "instance") + " " + argument(root / "logs"),
    "ExecStart": " ".join([
        "/usr/bin/env", argument(root / ".venv/bin/python"), "-m", "gunicorn",
        "--config", argument(root / "web/gunicorn.conf.py"),
        "--pid", argument(root / "instance/gunicorn.pid"), "main:create_app()",
    ]),
}
lines = (root / "systemd/midnight-chats.service").read_text().splitlines()
found = set()
for index, line in enumerate(lines):
    key, separator, _ = line.partition("=")
    if separator and key in settings:
        lines[index] = key + "=" + settings[key]
        found.add(key)
if found != set(settings):
    raise SystemExit("The systemd template is missing required service settings.")
destination.write_text("\n".join(lines) + "\n")
PY

systemd-analyze verify "$temporary_unit"
printf 'Installing the systemd unit (administrator privileges may be requested).\n'
as_root install -m 644 -- "$temporary_unit" "$service_destination"
as_root systemctl daemon-reload
as_root systemctl enable "$service_name"

if [[ "$start_now" == true ]]; then
    # Do not stop a manually launched process or take over an unrelated port.
    if ! systemctl is-active --quiet "$service_name" && [[ -f "$project_dir/instance/gunicorn.pid" ]]; then
        read -r previous_pid < "$project_dir/instance/gunicorn.pid" || true
        if [[ "${previous_pid:-}" =~ ^[1-9][0-9]*$ ]] && kill -0 "$previous_pid" 2>/dev/null; then
            fail "Service installed and enabled, but PID $previous_pid is already running outside systemd. Stop the manual Midnight Chats instance before starting the service."
        fi
    fi
    as_root systemctl restart "$service_name"
    systemctl --no-pager --full status "$service_name"
else
    printf '\nInstalled and enabled %s for the next boot.\n' "$service_name"
    printf 'To start it now after stopping any manual instance: sudo systemctl start %s\n' "$service_name"
fi
printf '\nConfiguration: %s/.env\nLogs: %s/logs/ (unless LOG_DIR overrides it)\n' "$project_dir" "$project_dir"
