import sys
from pathlib import Path


HOOKS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from _common import (  # noqa: E402
    hook_session_id,
    record_session_start_injected,
    session_start_already_injected,
)


def test_session_start_marker_skips_same_session(branch_dir):
    assets = {
        "repo_root": branch_dir["repo_root"],
        "repo_key": branch_dir["branch_dir"].parents[1].name,
        "repo_dir": branch_dir["branch_dir"].parents[1],
        "branch_name": branch_dir["branch_name"],
        "branch_key": branch_dir["branch_dir"].name,
    }
    hook_input = {"session_id": "session-1"}

    assert session_start_already_injected(assets, hook_input) is False

    marker = record_session_start_injected(assets, hook_input)

    assert marker is not None
    assert marker.exists()
    assert session_start_already_injected(assets, hook_input) is True
    assert session_start_already_injected(assets, {"session_id": "session-2"}) is False


def test_session_start_marker_requires_session_id(branch_dir):
    assets = {
        "repo_root": branch_dir["repo_root"],
        "repo_key": branch_dir["branch_dir"].parents[1].name,
        "repo_dir": branch_dir["branch_dir"].parents[1],
        "branch_name": branch_dir["branch_name"],
        "branch_key": branch_dir["branch_dir"].name,
    }

    assert hook_session_id({"payload": {"sessionId": "nested-session"}}) == "nested-session"
    assert session_start_already_injected(assets, {}) is False
    assert record_session_start_injected(assets, {}) is None


def test_session_start_marker_can_fallback_to_transcript_path(branch_dir):
    assets = {
        "repo_root": branch_dir["repo_root"],
        "repo_key": branch_dir["branch_dir"].parents[1].name,
        "repo_dir": branch_dir["branch_dir"].parents[1],
        "branch_name": branch_dir["branch_name"],
        "branch_key": branch_dir["branch_dir"].name,
    }
    hook_input = {"transcript_path": "/tmp/session.jsonl"}

    assert session_start_already_injected(assets, hook_input) is False

    record_session_start_injected(assets, hook_input)

    assert session_start_already_injected(assets, hook_input) is True
