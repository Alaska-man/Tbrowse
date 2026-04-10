#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  tbrowse — Installer (macOS & Linux)
#  Usage: bash install_tbrowse.sh
# ─────────────────────────────────────────────────────────────
set -e

CLI_FILE="tbrowse.py"
SCRIPT_NAME="tbrowse"

BOLD="\033[1m"; CYAN="\033[96m"; GREEN="\033[92m"
YELLOW="\033[93m"; RED="\033[91m"; DIM="\033[2m"; RESET="\033[0m"

info()    { echo -e "${CYAN}${BOLD}  →${RESET}  $*"; }
success() { echo -e "${GREEN}${BOLD}  ✓${RESET}  $*"; }
warn()    { echo -e "${YELLOW}${BOLD}  ⚠${RESET}  $*"; }
error()   { echo -e "${RED}${BOLD}  ✗${RESET}  $*"; exit 1; }

echo ""
echo -e "${CYAN}${BOLD}  ╔══════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}  ║   tbrowse — Terminal Browser     ║${RESET}"
echo -e "${CYAN}${BOLD}  ╚══════════════════════════════════╝${RESET}"
echo ""

# ── OS & arch ────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Linux*)  PLATFORM="Linux" ;;
  Darwin*) PLATFORM="macOS" ;;
  *)       error "Unsupported OS: $OS" ;;
esac
info "Platform: ${BOLD}$PLATFORM $(uname -m)${RESET}"

# ── Find python3 ─────────────────────────────────────────────
PYTHON=""
for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" &>/dev/null 2>&1; then
    PYTHON="$(command -v "$c")"; break
  fi
done
[ -z "$PYTHON" ] && error "python3 not found. Install with: brew install python3"
success "$($PYTHON --version) at $PYTHON"

# ── pip helper ───────────────────────────────────────────────
pip_install() {
  local pkg="$1"
  $PYTHON -m pip install --quiet "$pkg" 2>/dev/null \
    || $PYTHON -m pip install --quiet --break-system-packages "$pkg" 2>/dev/null \
    || $PYTHON -m pip install --quiet --user "$pkg" 2>/dev/null \
    || error "Failed to install $pkg"
}

info "Installing dependencies..."
pip_install "urllib3<2"
pip_install requests
pip_install beautifulsoup4
pip_install html2text
pip_install "googlesearch-python"   # real Google search results
pip_install ddgs                    # DuckDuckGo fallback (renamed from duckduckgo-search)
success "Dependencies ready"

# ── Source file ───────────────────────────────────────────────
SCRIPT_ABS="$(cd "$(dirname "$0")" && pwd)/${CLI_FILE}"
[ ! -f "$SCRIPT_ABS" ] && error "Cannot find ${CLI_FILE} in $(dirname "$SCRIPT_ABS")"

# ── Install to ~/bin ──────────────────────────────────────────
INSTALL_DIR="$HOME/bin"
mkdir -p "$INSTALL_DIR"
LAUNCHER="${INSTALL_DIR}/${SCRIPT_NAME}"

cat > "$LAUNCHER" <<WRAP
#!/usr/bin/env bash
exec "${PYTHON}" "${SCRIPT_ABS}" "\$@"
WRAP
chmod +x "$LAUNCHER"
success "Installed → ${LAUNCHER}"

# ── PATH setup ────────────────────────────────────────────────
SHELL_RC=""
case "$(basename "$SHELL")" in
  zsh)  SHELL_RC="$HOME/.zshrc" ;;
  bash) [ "$PLATFORM" = "macOS" ] && SHELL_RC="$HOME/.bash_profile" || SHELL_RC="$HOME/.bashrc" ;;
  *)    SHELL_RC="$HOME/.profile" ;;
esac

if [[ ":$PATH:" != *":$HOME/bin:"* ]]; then
  if [ -n "$SHELL_RC" ] && ! grep -qF 'HOME/bin' "$SHELL_RC" 2>/dev/null; then
    echo '' >> "$SHELL_RC"
    echo '# tbrowse' >> "$SHELL_RC"
    echo 'export PATH="$HOME/bin:$PATH"' >> "$SHELL_RC"
    success "Added ~/bin to PATH in ${SHELL_RC}"
    warn "Run: ${BOLD}source ${SHELL_RC}${RESET}  (or open a new terminal)"
  fi
  export PATH="$HOME/bin:$PATH"
fi

echo ""
echo -e "${GREEN}${BOLD}  ✓ Done! Open a new terminal (or run: source ${SHELL_RC})${RESET}"
echo ""
echo -e "${BOLD}  Usage:${RESET}"
echo -e "${DIM}    tbrowse                          # Open browser${RESET}"
echo -e "${DIM}    tbrowse \"search query\"           # Search Google${RESET}"
echo -e "${DIM}    tbrowse https://example.com      # Open URL${RESET}"
echo ""
echo -e "${BOLD}  Keys:${RESET}"
echo -e "${DIM}    j/k or ↑↓ = scroll   Tab/n = next link   Enter = open link${RESET}"
echo -e "${DIM}    o = edit URL bar     b = back   h = history   q = quit${RESET}"
echo ""
