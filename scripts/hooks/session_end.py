#!/usr/bin/env python3

from _common import (
    is_no_git_mode,
    is_workspace_mode,
    log,
    maybe_record_head,
    record_head_all_repos,
    resolve_assets,
)


def main():
    try:
        if is_no_git_mode():
            log("[dev-assets][SessionEnd] no-git mode: nothing to finalize (no HEAD)")
            return 0
        if is_workspace_mode():
            results = record_head_all_repos()
            if not results:
                log("[dev-assets][SessionEnd] workspace mode: no initialized repos finalized")
            return 0
        assets = resolve_assets()
        if not assets["branch_dir"].exists():
            log("[dev-assets][SessionEnd] branch memory not initialized, skip")
            return 0
        payload = maybe_record_head()
        log(f"[dev-assets][SessionEnd] finalized HEAD marker {payload['last_seen_head']} for {payload['branch']}")
    except Exception as exc:
        log(f"[dev-assets][SessionEnd] skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
