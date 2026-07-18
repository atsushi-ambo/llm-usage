#!/usr/bin/env bash
# Install llm-usage so `llm-usage` works from any directory.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${HOME}/.local/bin"

echo "Installing llm-usage from ${ROOT} …"

if command -v uv >/dev/null 2>&1; then
  # Preferred: isolated tool env via uv
  uv tool install --force -e "${ROOT}"
  echo "Installed via: uv tool install -e"
elif [[ -x "${HOME}/.local/bin/python3.13" ]]; then
  PY="${HOME}/.local/bin/python3.13"
  "${PY}" -m pip install --user -e "${ROOT}"
  echo "Installed via: python3.13 -m pip install --user -e"
else
  python3 -m pip install --user -e "${ROOT}"
  echo "Installed via: python3 -m pip install --user -e"
fi

mkdir -p "${BIN_DIR}"

if ! command -v llm-usage >/dev/null 2>&1; then
  # Ensure ~/.local/bin is on PATH for this shell session
  export PATH="${BIN_DIR}:${PATH}"
fi

if command -v llm-usage >/dev/null 2>&1; then
  echo
  echo "OK — available as: $(command -v llm-usage)"
  llm-usage --version
  echo
  echo "Try from anywhere:"
  echo "  llm-usage status"
  echo "  llm-usage"
  echo "  llm-usage dashboard"
  echo "  llm-usage menubar          # macOS menu bar (quota % near the clock)"
  echo
  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "Optional — start menu bar at login:"
    echo "  ${ROOT}/scripts/install-menubar-launchagent.sh"
  fi
else
  echo
  echo "Binary installed but not on PATH yet."
  echo "Add this to ~/.zshrc (or ~/.bashrc):"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo "Then open a new terminal."
  exit 1
fi
