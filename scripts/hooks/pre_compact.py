#!/usr/bin/env python3

from _common import (
    is_no_git_mode,
    is_workspace_mode,
    log,
    maybe_sync_working_tree,
    resolve_assets,
    sync_working_tree_all_repos,
)


def main():
    try:
        if is_no_git_mode():
            log("[dev-assets][PreCompact] no-git mode: nothing to refresh (no working tree)")
            return 0
        if is_workspace_mode():
            results = sync_working_tree_all_repos()
            if not results:
                log("[dev-assets][PreCompact] workspace mode: no initialized repos refreshed")
            return 0
        assets = resolve_assets()
        if not assets["branch_dir"].exists():
            log("[dev-assets][PreCompact] branch memory not initialized, skip")
            return 0
        payload = maybe_sync_working_tree()
        log(
            "[dev-assets][PreCompact] refreshed working-tree navigation for "
            f"{payload['branch']} ({payload['files_considered']} files)"
        )
    except Exception as exc:
        log(f"[dev-assets][PreCompact] skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
