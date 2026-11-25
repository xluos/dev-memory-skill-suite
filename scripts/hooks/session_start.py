#!/usr/bin/env python3

import json
import sys

from _common import (
    build_session_start_context,
    build_workspace_start_context,
    is_antigravity_hook,
    is_workspace_mode,
    read_hook_input,
    record_session_start_injected,
    resolve_assets,
    session_start_already_injected,
    set_repo_root_from_hook_input,
    log,
)


def _format_output(additional_context, antigravity=False):
    if antigravity:
        if not additional_context:
            return {}
        return {
            "injectSteps": [
                {
                    "systemMessage": {
                        "systemMessage": additional_context,
                    }
                }
            ]
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }


def _resolve_context():
    if is_workspace_mode():
        ctx = build_workspace_start_context()
        if ctx:
            return ctx
        return "dev-memory workspace 模式：当前 workspace 下未发现已初始化的仓库记忆。"
    return build_session_start_context()


def main():
    antigravity = False
    try:
        hook_input = read_hook_input()
        antigravity = is_antigravity_hook(hook_input)
        set_repo_root_from_hook_input(hook_input)
        assets = None
        if not is_workspace_mode():
            assets = resolve_assets()
            if session_start_already_injected(assets, hook_input):
                log("[dev-memory][SessionStart] duplicate injection skipped")
                print(json.dumps({}, ensure_ascii=False))
                return 0
            else:
                additional_context = _resolve_context()
                record_session_start_injected(assets, hook_input)
        else:
            additional_context = _resolve_context()
        payload = _format_output(additional_context, antigravity=antigravity)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        log(f"[dev-memory][SessionStart] skipped: {exc}")
        print(
            json.dumps(
                _format_output(
                    "dev-memory SessionStart hook 未能加载上下文，本轮按普通会话继续。",
                    antigravity=antigravity,
                ),
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
