#!/usr/bin/env bash
# Install a macOS LaunchAgent so llm-usage menubar starts at login.
set -euo pipefail

LABEL="com.llm-usage.menubar"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
BIN="$(command -v llm-usage || true)"

if [[ -z "${BIN}" ]]; then
  echo "llm-usage not on PATH. Run ./install.sh from the repo root first."
  exit 1
fi

mkdir -p "${HOME}/Library/LaunchAgents"
mkdir -p "${HOME}/Library/Logs/llm-usage"

cat > "${PLIST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${BIN}</string>
    <string>menubar</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${HOME}/Library/Logs/llm-usage/menubar.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/Library/Logs/llm-usage/menubar.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
EOF

launchctl unload "${PLIST}" 2>/dev/null || true
launchctl load "${PLIST}"
echo "Installed and started: ${PLIST}"
echo "Menu bar title looks like: C41 · G63 · O29"
echo "Unload with: launchctl unload ${PLIST}"
