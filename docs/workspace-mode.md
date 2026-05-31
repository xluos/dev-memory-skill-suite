# Workspace Mode Design

Add a multi-repo mode to dev-memory: when cwd is not a git repo but contains git repos in first-level subdirectories, treat cwd as a workspace and operate on all repos within it. Single-repo behavior is unchanged.

## Motivation

Tools like agentara host an LLM session whose cwd is a "workspace" directory containing several cloned repos. The agent reads/edits across these repos in one session. Today, dev-memory hooks fail at workspace cwd because `git` commands return no repo and storage paths can't be derived.

## Goals

- Multi-repo memory in one session (read + write)
- Zero impact on existing single-repo usage (purely additive)
- Codex + Claude equal-citizen support (both runners' lifecycle hooks work)
- Optional "primary repo" focus hint via env var to keep session-start context lean

## Non-Goals

- Per-tool-call repo routing (would need PreToolUse, which Codex lacks)
- Recursive scanning beyond first-level subdirectories
- Submodule traversal (submodules don't form an independent memory unit)
- New storage layout — keying remains `(repo_identity, branch)`

## Detection

Add to `lib/dev_memory_common.py` (siblings of existing `detect_repo_root`, lines 58–59):

```python
def detect_workspace_mode(cwd: Path | None = None) -> bool:
    """True if cwd itself is not a git repo but at least one first-level
    subdirectory is. Cwd defaults to Path.cwd()."""

def list_repos_in_workspace(cwd: Path | None = None) -> list[Path]:
    """Return absolute paths of first-level subdirs under cwd that are git repos.
    Sorted by directory name. Empty list if not workspace mode."""
```

Detection rules:

- `cwd/.git` exists (dir or file) → return False (existing single-repo mode wins)
- `cwd` has zero first-level subdirs with `.git` → return False (dev-memory no-ops, as today for non-repo cwd)
- Otherwise → True

`.git` may be a directory (regular repo) or a file (worktree pointer). Both count.

## Batch identity helper

```python
def get_all_branch_paths(cwd: Path | None = None) -> list[BranchPaths]:
    """Per-repo BranchPaths for every repo in workspace. Skips repos with detached
    HEAD (logs warning, does not raise). Empty list if not workspace mode."""
```

`BranchPaths` is the existing tuple from `get_branch_paths()` (line 165). No schema change.

## Primary repo hint

Env vars consulted by hook scripts:

- `DEV_ASSETS_PRIMARY_REPO` — directory **basename** of focus repo (e.g. `myproject`, not absolute path). Matches the basename of one entry in `list_repos_in_workspace()`.
- `DEV_ASSETS_PRIMARY_BRANCH` — branch name. Informational only; actual branch is read from `git` per repo.

If env unset in workspace mode: no primary, all repos treated equally (verbose injection).

## Hook Behavior

### `scripts/hooks/session_start.py`

Current shape (lines 9–33):

```python
context = build_session_start_context()  # single repo
emit(context)
```

New shape:

```python
if detect_workspace_mode():
    primary = os.environ.get("DEV_ASSETS_PRIMARY_REPO")
    sections = []
    for repo_path in list_repos_in_workspace():
        is_primary = (primary is None) or (repo_path.name == primary)
        sections.append(build_context_for_repo(repo_path, full=is_primary))
    emit("\n\n---\n\n".join(sections))
else:
    emit(build_session_start_context())  # original
```

`build_context_for_repo(repo_path, full)` — new helper in `scripts/hooks/_common.py`:

- `full=True`: inject overview + development + context + sources + recent HEAD
- `full=False`: inject only manifest summary + branches/<branch>/overview.md (1-paragraph orientation)

### `scripts/hooks/stop.py`

Current shape (lines 6–15) records lightweight HEAD for the single detected repo. New shape iterates:

```python
if detect_workspace_mode():
    for repo_path in list_repos_in_workspace():
        try:
            record_head_for_repo(repo_path)
        except DetachedHeadError:
            continue
else:
    maybe_record_head()  # original
```

`record_head_for_repo(repo_path)` is a thin extraction of the existing single-repo code parameterized by repo root.

### `scripts/hooks/pre_compact.py`, `scripts/hooks/session_end.py`

Mirror the iteration pattern. Both are Claude-only hooks (no Codex equivalent), but the workspace-mode branch applies for Claude users in workspaces.

## Skill / CLI Behavior

### `dev-memory-sync` (CLI: `dev-memory-cli record-session`)

Add optional `--repo <basename>` flag:

- Single-repo mode: ignored (or warn if specified)
- Workspace mode + `--repo` set: write to that repo's branch dir
- Workspace mode + `--repo` unset:
  - If `DEV_ASSETS_PRIMARY_REPO` set → write to primary
  - Else → error: `"workspace mode: --repo required when no primary"`

`SKILL.md` updated: in workspace mode, the LLM should pass `--repo <basename>` to disambiguate. Default-to-primary is the convenience path.

### `dev-memory-context` (CLI: `dev-memory-cli recover-context`)

Add `--repo <basename>` for explicit cross-repo loading mid-session. Useful when LLM realizes it needs full memory for a non-primary repo.

### `dev-memory-update` (CLI: `dev-memory-cli update-section`)

Add `--repo <basename>`. Same disambiguation rules as sync.

### `dev-memory-setup` (CLI: `dev-memory-cli init`)

Workspace mode + no `--repo`: refuse with message `"workspace mode: setup must target a single repo, pass --repo <basename> for one of: a, b, c"`. Per-repo init is intentional — each repo's first-time setup is a deliberate user decision.

## CLI dispatch

`bin/dev-memory.js` (Node entry point) gains `--repo` parsing for the four subcommands above. Workspace-mode detection itself is internal to Python — Node just forwards env + args.

## Storage Layout (unchanged)

```
~/.dev-memory/
  repos/
    <repo-key>/
      repo/
      branches/<branch-key>/
```

Workspace mode does not change storage. Keying remains `(repo_identity_from_origin_url_or_path, branch)`. Two workspaces (or two agentara groups) holding the same `(repo, branch)` automatically share memory.

## Configuration

No new config files. Behavior is fully controlled by:

- Whether cwd is a git repo (auto-detected)
- `DEV_ASSETS_PRIMARY_REPO` / `DEV_ASSETS_PRIMARY_BRANCH` env vars (optional, hint only)
- Existing `DEV_ASSETS_ROOT` env var or git config `dev-memory.root` (storage location, unchanged)

## Codex Compatibility

Confirmed via `hooks/codex-hooks.json` (lines 3–31):

| Hook | Claude | Codex | Workspace mode applies |
|------|--------|-------|------------------------|
| SessionStart | ✅ | ✅ | yes — multi-repo context injection |
| Stop | ✅ | ✅ | yes — multi-repo HEAD recording |
| PreCompact | ✅ | ❌ | yes (Claude only) |
| SessionEnd | ✅ | ❌ | yes (Claude only) |

Heavy write (`dev-memory-sync` skill) is LLM-driven CLI invocation — both runners can call it identically. Codex is fully supported in workspace mode for read + write; only PreCompact / SessionEnd refinements are Claude-exclusive (and unrelated to multi-repo correctness).

## Backwards Compatibility

- Single-repo cwd: byte-identical behavior. New functions are not called.
- Workspace-mode env vars: only consulted when workspace mode is active.
- Storage layout: unchanged.
- CLI: existing invocations work unchanged. `--repo` is opt-in.
- Hook execution: single-repo branch is the same as today.

## Files Touched

| File | Change | Lines |
|------|--------|-------|
| `lib/dev_memory_common.py` | Add `detect_workspace_mode`, `list_repos_in_workspace`, `get_all_branch_paths` | ~50 |
| `scripts/hooks/_common.py` | Add `build_context_for_repo(repo_path, full)`, `record_head_for_repo(repo_path)` | ~40 |
| `scripts/hooks/session_start.py` | Workspace branch | ~15 |
| `scripts/hooks/stop.py` | Workspace branch | ~10 |
| `scripts/hooks/pre_compact.py` | Workspace branch | ~10 |
| `scripts/hooks/session_end.py` | Workspace branch | ~10 |
| `bin/dev-memory.js` | `--repo` parsing for sync / context / update / init | ~20 |
| `skills/dev-memory-sync/SKILL.md` | Document `--repo` in workspace mode | docs |
| `skills/dev-memory-context/SKILL.md` | Same | docs |
| `skills/dev-memory-update/SKILL.md` | Same | docs |
| `skills/dev-memory-setup/SKILL.md` | Note per-repo setup requirement | docs |
| `README.md` | "Workspace Mode" section | docs |
| `AGENTS.md` | Brief mention in lifecycle table | docs |

Total: ~150 lines of code + docs.

## Implementation Order

1. `lib/dev_memory_common.py` — three new functions + unit tests
2. `scripts/hooks/_common.py` — extract per-repo helpers from existing single-repo logic
3. `scripts/hooks/session_start.py` — most user-visible change, validate end-to-end first
4. `scripts/hooks/stop.py`
5. `scripts/hooks/pre_compact.py`, `session_end.py` (Claude-only)
6. CLI `--repo` flag wiring in `bin/dev-memory.js`
7. SKILL.md doc updates (sync, context, update, setup)
8. README "Workspace Mode" section + AGENTS.md mention

## Open Questions

- **`dev-memory-setup` in workspace mode without `--repo`**: refuse and instruct user, or auto-init all? Proposal: refuse. First-time setup is per-repo by intent.
- **HEAD recording for inactive repos in `Stop`**: skip if no recent commits, or always overwrite the manifest HEAD field? Proposal: always overwrite — it's a single timestamp + SHA, cheap and idempotent.
- **`DEV_ASSETS_PRIMARY_REPO` value form**: basename vs absolute path? Proposal: basename, matches the user-facing `--repo` flag and is robust to renames of the workspace dir.
- **Detection cost on large workspaces**: `os.scandir()` over first-level subdirs is O(N) and called per hook invocation. If N grows large (>100 repos), cache or limit. Proposal: defer until measured.
