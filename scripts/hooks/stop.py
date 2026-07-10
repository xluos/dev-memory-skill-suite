#!/usr/bin/env python3

from _common import (
    is_no_git_mode,
    is_workspace_mode,
    log,
    maybe_record_head,
    read_hook_input,
    record_head_all_repos,
    register_session_scan_candidate,
    resolve_assets,
)


def main():
    hook_input = read_hook_input()
    try:
        candidate = register_session_scan_candidate(hook_input)
        if candidate:
            log(f"[dev-memory][Stop] registered session scan candidate {candidate}")
        if is_no_git_mode():
            log("[dev-memory][Stop] no-git mode: nothing to record (no HEAD)")
            return 0
        if is_workspace_mode():
            results = record_head_all_repos()
            if not results:
                log("[dev-memory][Stop] workspace mode: no initialized repos recorded")
            return 0
        assets = resolve_assets()
        if not assets["branch_dir"].exists():
            log("[dev-memory][Stop] branch memory not initialized, skip")
            return 0
        payload = maybe_record_head()
        log(f"[dev-memory][Stop] recorded HEAD {payload['last_seen_head']} for {payload['branch']}")
    except Exception as exc:
        log(f"[dev-memory][Stop] skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
