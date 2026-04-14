#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT="."
PACKAGE_SPEC=""
AGENT="codex"

# Default agent is derived from the script basename so this single script can
# back both install_codex_hooks.sh and install_claude_hooks.sh symlinks.
case "$(basename -- "$0")" in
  *claude*) AGENT="claude" ;;
  *codex*)  AGENT="codex" ;;
esac

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo)
      REPO_ROOT="$2"
      shift 2
      ;;
    --package)
      PACKAGE_SPEC="$2"
      shift 2
      ;;
    --agent)
      AGENT="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: install_codex_hooks.sh [--repo PATH] [--package SPEC] [--agent codex|claude]

Install the dev-assets CLI into the target repository and merge hooks for the
requested agent. Defaults to codex; pass --agent claude (or invoke this script
as install_claude_hooks.sh) to target Claude instead.
EOF
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

case "$AGENT" in
  codex|claude) ;;
  *)
    echo "ERROR: unsupported agent: $AGENT (expected codex|claude)" >&2
    exit 1
    ;;
esac

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm is required" >&2
  exit 1
fi

TARGET_REPO=$(cd "$REPO_ROOT" && pwd)

if [ -z "$PACKAGE_SPEC" ]; then
  if [ -f "$SCRIPT_DIR/../package.json" ]; then
    PACKAGE_SPEC="file:$SCRIPT_DIR/.."
  else
    PACKAGE_SPEC="@xluos/dev-assets-cli"
  fi
fi

(
  cd "$TARGET_REPO"
  npm install --save-dev "$PACKAGE_SPEC"
  npx dev-assets install-hooks "$AGENT" --repo "$TARGET_REPO"
)
