#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  tbrowse — Uninstaller (macOS & Linux)
#  Usage: bash uninstall_tbrowse.sh
# ─────────────────────────────────────────────────────────────

SCRIPT_NAME="tbrowse"
LAUNCHER="$HOME/bin/${SCRIPT_NAME}"

BOLD="\033[1m"; CYAN="\033[96m"; GREEN="\033[92m"
YELLOW="\033[93m"; DIM="\033[2m"; RESET="\033[0m"

info()    { echo -e "${CYAN}${BOLD}  →${RESET}  $*"; }
success() { echo -e "${GREEN}${BOLD}  ✓${RESET}  $*"; }
warn()    { echo -e "${YELLOW}${BOLD}  ⚠${RESET}  $*"; }

echo ""
echo -e "${CYAN}${BOLD}  ╔══════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}  ║   tbrowse — Uninstaller          ║${RESET}"
echo -e "${CYAN}${BOLD}  ╚══════════════════════════════════╝${RESET}"
echo ""

# Remove launcher
if [ -f "$LAUNCHER" ]; then
  rm -f "$LAUNCHER"
  success "Removed $LAUNCHER"
else
  warn "Launcher not found at $LAUNCHER — already removed?"
fi

# Clean shell configs
for RC in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
  if grep -q "tbrowse" "$RC" 2>/dev/null; then
    warn "Found tbrowse entry in ${RC}"
    read -rp "  Remove it? [y/N]: " CONFIRM
    if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
      TMP=$(mktemp)
      grep -v "tbrowse" "$RC" | grep -v 'HOME/bin' > "$TMP"
      mv "$TMP" "$RC"
      success "Cleaned $RC"
    fi
  fi
done

# Optional package removal
PYTHON=""
for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  command -v "$c" &>/dev/null && PYTHON="$(command -v "$c")" && break
done

if [ -n "$PYTHON" ]; then
  echo ""
  read -rp "  Remove Python packages (ddgs, html2text, beautifulsoup4)? [y/N]: " CONFIRM
  if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
    $PYTHON -m pip uninstall -y ddgs html2text beautifulsoup4 2>/dev/null \
      || $PYTHON -m pip uninstall -y --break-system-packages ddgs html2text beautifulsoup4 2>/dev/null \
      || true
    success "Packages removed"
  fi
fi

echo ""
echo -e "${GREEN}${BOLD}  ✓ Uninstalled.${RESET}"
echo -e "${DIM}  To reinstall: bash install_tbrowse.sh${RESET}"
echo ""
