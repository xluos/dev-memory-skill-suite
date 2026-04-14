#!/bin/sh

set -eu

REPO_ROOT="."
PACKAGE_SPEC="@xluos/dev-assets-cli"
AGENT="codex"
GLOBAL=0
ALL=0

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
    --global|-g)
      GLOBAL=1
      shift
      ;;
    --all)
      ALL=1
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: install_codex_hooks.sh [--repo PATH] [--package SPEC] [--agent codex|claude] [--global] [--all]

Thin wrapper that merges hooks for the requested agent via
`dev-assets install-hooks <agent> [--repo <repo>|--global]`.

Prefer installing the CLI globally once (e.g. `npm i -g @xluos/dev-assets-cli`)
and then running `dev-assets install-hooks <agent>` directly; this script exists
for environments where a one-shot shell entry is easier.

--global writes to the agent's user-level config (~/.codex/hooks.json or
~/.claude/settings.json); otherwise hooks are merged into the target repo.

--all installs hooks for both codex and claude in one shot.
EOF
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [ "$ALL" -ne 1 ]; then
  case "$AGENT" in
    codex|claude) ;;
    *)
      echo "ERROR: unsupported agent: $AGENT (expected codex|claude)" >&2
      exit 1
      ;;
  esac
fi

if [ "$ALL" -eq 1 ]; then
  set -- install-hooks --all
else
  set -- install-hooks "$AGENT"
fi

if [ "$GLOBAL" -eq 1 ]; then
  set -- "$@" --global
else
  TARGET_REPO=$(cd "$REPO_ROOT" && pwd)
  set -- "$@" --repo "$TARGET_REPO"
fi

if command -v dev-assets >/dev/null 2>&1; then
  dev-assets "$@"
else
  if ! command -v npx >/dev/null 2>&1; then
    echo "ERROR: need either a globally installed 'dev-assets' or 'npx' on PATH" >&2
    exit 1
  fi
  npx -y "$PACKAGE_SPEC" "$@"
fi
