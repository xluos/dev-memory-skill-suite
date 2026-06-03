#!/usr/bin/env python3

import json
import sys

from _common import (
    build_session_start_context,
    build_workspace_start_context,
    is_workspace_mode,
    read_hook_input,
    record_session_start_injected,
    resolve_assets,
    session_start_already_injected,
    log,
)


def _resolve_context():
    if is_workspace_mode():
        ctx = build_workspace_start_context()
        if ctx:
            return ctx
        return "dev-memory workspace 模式：当前 workspace 下未发现已初始化的仓库记忆。"
    return build_session_start_context()


def main():
    try:
        hook_input = read_hook_input()
        assets = None
        if not is_workspace_mode():
            assets = resolve_assets()
            if session_start_already_injected(assets, hook_input):
                additional_context = (
                    "dev-memory SessionStart 已在当前 session 注入过，"
                    "本次 resume 跳过重复上下文注入。"
                )
            else:
                additional_context = _resolve_context()
                record_session_start_injected(assets, hook_input)
        else:
            additional_context = _resolve_context()
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": additional_context,
            }
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        log(f"[dev-memory][SessionStart] skipped: {exc}")
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "dev-memory SessionStart hook 未能加载上下文，本轮按普通会话继续。",
                    }
                },
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
