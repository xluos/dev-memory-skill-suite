# Dev Asset Skill Suite

Repo-aware and branch-coupled development memory skills for Codex and similar agent runtimes.

This repository packages a small skill suite for one job: keep development memory usable across sessions without turning the Git worktree into a second document store.

- `using-dev-assets` — entry router for Git-repository development conversations
- `dev-assets-setup` — initialize user-home repo+branch memory storage for the current repository
- `dev-assets-context` — recover current branch memory first, then pull repo-shared memory when needed
- `dev-assets-update` — rewrite current durable memory or shared source indexes when new understanding appears
- `dev-assets-sync` — treat commit-related moments as checkpoints and persist only durable memory

Detailed guide:

- [docs/dev-asset-skill-suite-guide.md](docs/dev-asset-skill-suite-guide.md)

## Install with `npx skills`

List available skills:

```bash
npx skills add xluos/dev-asset-skill-suite --list
```

Install the whole suite for Codex globally:

```bash
npx skills add xluos/dev-asset-skill-suite --skill '*' -a codex -g -y
```

Install the whole suite for all detected agents:

```bash
npx skills add xluos/dev-asset-skill-suite --all -g -y
```

## Repository Layout

```text
bin/
  dev-assets.js              # `npx dev-assets` CLI entry (hooks + install helpers)
hooks/
  hooks.json                 # Claude hook template (.claude/settings.local.json)
  codex-hooks.json           # Codex hook template (.codex/hooks.json)
  README.md
lib/
  dev_asset_common.py        # shared library used by hook scripts
scripts/
  hooks/                     # session_start/pre_compact/stop/session_end .py — invoked via `dev-assets hook ...`
  install_codex_hooks.sh     # one-shot installer; symlinked as install_claude_hooks.sh
  install_claude_hooks.sh -> install_codex_hooks.sh
  install_suite.py           # local symlink-based skill install (dev only)
  npm/                       # package check/build helpers
skills/
  using-dev-assets/
  dev-assets-setup/
  dev-assets-context/
  dev-assets-update/
  dev-assets-sync/
suite-manifest.json          # canonical list of suite + legacy skill names
```

## Storage Layout

By default the suite stores memory outside the repository:

```text
~/.dev-assets/repos/<repo-key>/
  repo/
    overview.md
    context.md
    sources.md
    manifest.json
  branches/
    <branch>/
      overview.md
      development.md
      context.md
      sources.md
      manifest.json
      artifacts/
        history/
```

- `repo/`: shared memory for the whole Git repository
- `branches/<branch>/`: branch-local working memory
- `repo-key`: derived from repository identity, not just the folder name
- `DEV_ASSETS_ROOT`: environment variable that overrides the default storage root (`~/.dev-assets/repos`); honored by the CLI and all bundled skill scripts

## Lifecycle Hooks

This suite now follows ECC-style lifecycle hooks instead of Git hooks, and it supports both Claude and Codex.

- Claude recommended repo-local target: `.claude/settings.local.json`
- Codex recommended repo-local target: `.codex/hooks.json`
- Claude reusable template: `hooks/hooks.json`
- Codex reusable template: `hooks/codex-hooks.json`
- Hook behavior guide: `hooks/README.md`

Current mapping by agent:

- Claude: `SessionStart`, `PreCompact`, `Stop`, `SessionEnd`
- Codex: `SessionStart`, `Stop`

Shared behavior:

- `SessionStart`: restore repo+branch memory into the new session
- `PreCompact`: refresh working-tree-derived navigation before compaction
- `Stop`: persist a lightweight HEAD marker after each response
- `SessionEnd`: persist the final HEAD marker at session end

Recommended: install the CLI once globally, then merge hooks per repo.

```bash
npm install -g @xluos/dev-assets-cli                 # once
dev-assets install-hooks codex                       # in the target repo (defaults to cwd)
dev-assets install-hooks claude
```

Or merge hooks into the agent's **user-level** config instead of per-repo:

```bash
dev-assets install-hooks codex --global              # writes ~/.codex/hooks.json
dev-assets install-hooks claude --global             # writes ~/.claude/settings.json
```

Install for both agents at once with `--all`:

```bash
dev-assets install-hooks --all                       # both agents, repo-scoped
dev-assets install-hooks --all --global              # both agents, user-level
```

Without a global CLI install, run via `npx` (downloads on demand):

```bash
npx -y @xluos/dev-assets-cli install-hooks codex
npx -y @xluos/dev-assets-cli install-hooks claude --global
```

`install-hooks <agent>` merges hooks into the target config. Repo scope writes `.codex/hooks.json` or `.claude/settings.local.json`; `--global` writes `~/.codex/hooks.json` or `~/.claude/settings.json`. Hooks call `dev-assets hook ...`, so the CLI must be reachable on PATH (global install) or resolvable via `npx`. `--repo` defaults to the current working directory when omitted.

Shell installers (`scripts/install_codex_hooks.sh`, `scripts/install_claude_hooks.sh`) are thin wrappers around the same command for environments that prefer a shell entry:

```bash
sh scripts/install_codex_hooks.sh                    # Codex, repo-scoped
sh scripts/install_codex_hooks.sh --agent claude     # Claude, repo-scoped
sh scripts/install_codex_hooks.sh --global           # Codex, user-level
sh scripts/install_claude_hooks.sh --global          # Claude, user-level
```

Boundary:

- This repository ships reusable hook templates and a reusable CLI, but the actual repo-local config files are environment-local
- In this clone, Codex can read `.codex/hooks.json` directly; Claude typically uses a local `.claude/settings.local.json` file that may be ignored by user-level Git rules
- Global skill installs do not auto-load hooks yet, because this project is a skill suite rather than a standalone plugin
- Hook execution is now expected to go through `dev-assets` CLI, not raw `python3 scripts/hooks/*.py`

## Two Invocation Tracks

This suite has two distinct entry surfaces. They look similar but should not be confused.

- Lifecycle hooks → `npx dev-assets hook <session-start|pre-compact|stop|session-end>`. These run automatically from `.codex/hooks.json` or `.claude/settings.local.json` and are the only place where the `dev-assets` CLI is the right entry point.
- In-conversation skill workflows (`dev-assets-setup`, `dev-assets-context`, `dev-assets-update`, `dev-assets-sync`) → invoke each skill's bundled Python script under `<skill-dir>/scripts/`. The CLI does not wrap these because they are interactive workflow steps with skill-specific arguments, not background hook actions.

Inside SKILL.md files these script paths appear as `python3 /absolute/path/to/<skill>/scripts/<name>.py`. The agent is expected to substitute the actual on-disk skill directory at call time (the path the harness loaded the skill from), not to pass the literal placeholder string.

## Notes

- The suite no longer stores its primary memory inside the Git worktree by default.
- Branch memory is still the main execution context. Repo memory is a shared supplement, not a replacement.
- Detailed implementation history should stay in Git. When the agent needs exact changes, it should read `git log` / `git show` instead of copying commit history into dev assets.
- Shared document entrances can live in repo-level `sources.md`; branch-specific progress and next-step live in branch files.
- `dev-assets-setup` can migrate legacy `.dev-assets/<branch>/` content into the new user-home branch directory.
- `npx skills` does not need `scripts/install_suite.py`; the repository already follows standard skill discovery rules.
- `scripts/install_suite.py` remains useful for local symlink-based installs during development. Example — symlink all suite skills into Codex's user-level skill directory and prune legacy aliases:

  ```bash
  python3 scripts/install_suite.py --target ~/.codex/skills --force --remove-legacy
  ```
