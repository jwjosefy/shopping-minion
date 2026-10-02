#!/usr/bin/env bash
# Sets up Shopping Minion on a new machine (Linux or macOS), without sudo:
#
#   curl -fsSL https://raw.githubusercontent.com/jwjosefy/shopping-minion/main/scripts/bootstrap.sh | bash
#
# Installs uv, just and dotenvx into ~/.local/bin when missing, clones the repo
# (unless run from inside it), then installs the Python deps and Playwright's
# Chromium. Safe to run again. SHOPPING_MINION_DIR sets where the repo goes.
set -euo pipefail

REPO_URL="https://github.com/jwjosefy/shopping-minion.git"
BIN="$HOME/.local/bin"
DOTENVX_VERSION="2.31.1"  # the version journal 0003 was checked with: keys go to the OS keyring

say() { printf '\n==> %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing $1. Install it and run this again." >&2; exit 1; }; }

ORIGINAL_PATH="$PATH"
need git
need curl
mkdir -p "$BIN"
export PATH="$BIN:$PATH"

if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if ! command -v just >/dev/null 2>&1; then
    say "Installing just"
    uv tool install rust-just
fi

if ! command -v dotenvx >/dev/null 2>&1; then
    say "Installing dotenvx $DOTENVX_VERSION"
    curl -sfS "https://dotenvx.sh?directory=$BIN&version=$DOTENVX_VERSION" | sh
fi

if [ -f pyproject.toml ] && grep -q '^name = "shopping-minion"' pyproject.toml; then
    DIR="$PWD"
else
    DIR="${SHOPPING_MINION_DIR:-$PWD/shopping-minion}"
    if [ ! -d "$DIR/.git" ]; then
        say "Cloning into $DIR"
        git clone "$REPO_URL" "$DIR"
    fi
fi
cd "$DIR"

say "Installing Python deps"
uv sync

say "Installing Playwright's Chromium"
uv run playwright install chromium

if ! command -v claude >/dev/null 2>&1; then
    say "Claude Code not found. The OCR runs 'claude -p': install it from https://claude.com/claude-code and log in."
fi

# The PATH export above only lasts for this script.
case ":$ORIGINAL_PATH:" in
    *":$BIN:"*) ;;
    *) say "Add $BIN to your PATH (e.g. in ~/.bashrc), or just, uv and dotenvx won't be found in a new shell." ;;
esac

cat <<EOF

Done. Next, in $DIR:
  just jev                          # your TYPESAFE_API_KEY, encrypted in .env.local
  just login                        # log in to the store by hand once
  just up                           # the web app; open the URL or scan the QR

If Chromium won't start on Linux, it may need system libraries:
  uv run playwright install-deps chromium   (asks for sudo)
EOF
