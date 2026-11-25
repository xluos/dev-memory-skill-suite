#!/usr/bin/env python3

import json

from _common import (
    is_antigravity_hook,
    is_no_git_mode,
    is_workspace_mode,
    log,
    maybe_record_head,
    read_hook_input,
    record_head_all_repos,
    register_session_scan_candidate,
    resolve_assets,
    set_repo_root_from_hook_input,
)


def main():
    hook_input = read_hook_input()
    antigravity = is_antigravity_hook(hook_input)
    try:
        set_repo_root_from_hook_input(hook_input)
        candidate = register_session_scan_candidate(hook_input)
        if candidate:
            log(f"[dev-memory][Stop] registered session scan candidate {candidate}")
        if is_no_git_mode():
            log("[dev-memory][Stop] no-git mode: nothing to record (no HEAD)")
        elif is_workspace_mode():
            results = record_head_all_repos()
            if not results:
                log("[dev-memory][Stop] workspace mode: no initialized repos recorded")
        else:
            assets = resolve_assets()
            if not assets["branch_dir"].exists():
                log("[dev-memory][Stop] branch memory not initialized, skip")
            else:
                payload = maybe_record_head()
                log(f"[dev-memory][Stop] recorded HEAD {payload['last_seen_head']} for {payload['branch']}")
    except Exception as exc:
        log(f"[dev-memory][Stop] skipped: {exc}")
    if antigravity:
        print(json.dumps({"decision": "allow"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
