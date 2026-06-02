#!/usr/bin/env python3

from _common import (
    enqueue_session_summary_job,
    is_no_git_mode,
    is_workspace_mode,
    log,
    maybe_record_head,
    read_hook_input,
    record_head_all_repos,
    resolve_assets,
)


def main():
    try:
        hook_input = read_hook_input()
        if is_no_git_mode():
            log("[dev-memory][SessionEnd] no-git mode: nothing to finalize (no HEAD)")
            return 0
        if is_workspace_mode():
            results = record_head_all_repos()
            if not results:
                log("[dev-memory][SessionEnd] workspace mode: no initialized repos finalized")
            for _, payload in results:
                try:
                    queued = enqueue_session_summary_job(payload, hook_input, event_name="SessionEnd")
                    log(f"[dev-memory][SessionEnd] queued summary job {queued['job_id']}")
                except Exception as exc:
                    log(f"[dev-memory][SessionEnd] summary enqueue skipped: {exc}")
            return 0
        assets = resolve_assets()
        if not assets["branch_dir"].exists():
            log("[dev-memory][SessionEnd] branch memory not initialized, skip")
            return 0
        payload = maybe_record_head()
        log(f"[dev-memory][SessionEnd] finalized HEAD marker {payload['last_seen_head']} for {payload['branch']}")
        try:
            queued = enqueue_session_summary_job(payload, hook_input, event_name="SessionEnd")
            log(f"[dev-memory][SessionEnd] queued summary job {queued['job_id']}")
        except Exception as exc:
            log(f"[dev-memory][SessionEnd] summary enqueue skipped: {exc}")
    except Exception as exc:
        log(f"[dev-memory][SessionEnd] skipped: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
