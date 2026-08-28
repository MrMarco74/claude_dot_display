#!/usr/bin/env bash
# Install claude-dot-display as a systemd user service.
#
# Deliberately a user service, not a system one: the board reads the user's
# session state directory and needs no privileges.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${HOME}/.local/share/dotdisplay"
CONFIG_DIR="${HOME}/.config/dotdisplay"
UNIT_DIR="${HOME}/.config/systemd/user"
BIN_DIR="${HOME}/.local/bin"

# Only one process may own the radio. Two owners produce failures that look
# exactly like protocol bugs, so refuse rather than let that happen.
if systemctl --user is-active --quiet sensmonlight-idotmatrix-agent.service; then
    echo "ERROR: sensmonlight-idotmatrix-agent.service is running." >&2
    echo "It owns the Bluetooth radio. Stop it first:" >&2
    echo "  systemctl --user disable --now sensmonlight-idotmatrix-agent.service" >&2
    exit 1
fi

echo "==> venv at ${PREFIX}/venv"
mkdir -p "${PREFIX}"
python3 -m venv "${PREFIX}/venv"
"${PREFIX}/venv/bin/python" -m pip install --quiet --upgrade pip
"${PREFIX}/venv/bin/python" -m pip install --quiet "${REPO}"

# The service runs the venv binary by absolute path, so the daemon never
# needed this. Everything else does: the report-status skill, the status
# line and the user's own shell all call a bare `dotdisplay`. Without this
# link they get "command not found" -- and the skill is told to swallow
# errors, so that failure is silent and the board never leaves "running".
echo "==> link at ${BIN_DIR}/dotdisplay"
mkdir -p "${BIN_DIR}"
ln -sfn "${PREFIX}/venv/bin/dotdisplay" "${BIN_DIR}/dotdisplay"
case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) echo "    NOTE: ${BIN_DIR} is not on your PATH; add it or the" >&2
       echo "          board's status reporting stays silent." >&2 ;;
esac

echo "==> configuration at ${CONFIG_DIR}/env"
mkdir -p "${CONFIG_DIR}"
if [[ ! -f "${CONFIG_DIR}/env" ]]; then
    MAC="${DOTDISPLAY_MAC:-}"
    if [[ -z "${MAC}" ]]; then
        read -r -p "Panel Bluetooth address (e.g. AA:BB:CC:DD:EE:FF): " MAC
    fi
    # umask first: this file can hold the hwmon setup key.
    ( umask 077
      cat > "${CONFIG_DIR}/env" <<ENVEOF
DOTDISPLAY_MAC=${MAC}
# Optional: show sessions from other hosts and serve hwmon's command queue.
#DOTDISPLAY_HWMON_URL=
#DOTDISPLAY_HWMON_SETUP_KEY=
ENVEOF
    )
    chmod 600 "${CONFIG_DIR}/env"
else
    echo "    keeping existing ${CONFIG_DIR}/env"
fi

echo "==> unit at ${UNIT_DIR}/dotdisplay.service"
mkdir -p "${UNIT_DIR}"
install -m 644 "${REPO}/packaging/dotdisplay.service" "${UNIT_DIR}/dotdisplay.service"

systemctl --user daemon-reload
systemctl --user enable --now dotdisplay.service
systemctl --user --no-pager status dotdisplay.service || true

cat <<'NOTE'

To keep the board running when you are not logged in:
    loginctl enable-linger $USER
NOTE
