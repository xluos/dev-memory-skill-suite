"""Tests for the `delete-block` action in `tidy apply`. Validates that a
single block_id removes the whole semantic unit (top-level bullet +
sub-bullets + absorbed orphan paragraphs), that interactions with
delete-entries / delete-section / reset-file follow the declared priority
(reset > section > block > entry), and that out-of-range block_ids land
in the `invalid` list without aborting the rest of the plan."""

import json
import subprocess
import sys
from pathlib import Path


LIB = Path(__file__).resolve().parent.parent / "lib"


def _run_apply(repo, storage, plan):
    """Persist `plan` to a tmp file under `repo` and run `tidy apply`."""
    plan_path = Path(repo) / "_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    cmd = [
        sys.executable, str(LIB / "dev_memory_tidy.py"), "apply",
        "--repo", str(repo), "--context-dir", str(storage),
        "--plan-file", str(plan_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"apply failed: stderr={proc.stderr!r} stdout={proc.stdout!r}"
    return json.loads(proc.stdout)


def _seed_decisions_with_three_blocks(seed_branch_files):
    """Block 0: 1 top-level + 2 sub-bullets + Why/How orphan paragraphs.
    Block 1: simple bullet. Block 2: simple bullet."""
    body = "\n".join([
        "# decisions",
        "",
        "## 关键决策与原因",
        "",
        "- **前端分工**",
        "  - 徐帅武 owns FE-1 + FE-3",
        "  - 湛憬禧 owns FE-3.4",
        "**Why:** 2026-05-14 会议拍板",
        "**How to apply:** 见 progress.md",
        "",
        "- 决策二: 切到 feature flag",
        "",
        "- 决策三: 弃用旧接口",
        "",
    ])
    return seed_branch_files({"decisions": body})


def test_delete_block_removes_subtree_and_orphans(seed_branch_files, tmp_repo, tmp_storage_root):
    branch = _seed_decisions_with_three_blocks(seed_branch_files)
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {"type": "delete-block", "block_id": "decisions::0::block-0"},
        ],
    }
    result = _run_apply(tmp_repo, tmp_storage_root, plan)
    assert result["invalid"] == []
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    # Block 0 contents all gone.
    assert "前端分工" not in text
    assert "徐帅武" not in text
    assert "**Why:**" not in text
    assert "**How to apply:**" not in text
    # Other blocks remain.
    assert "决策二" in text
    assert "决策三" in text


def test_delete_block_multiple_blocks(seed_branch_files, tmp_repo, tmp_storage_root):
    branch = _seed_decisions_with_three_blocks(seed_branch_files)
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {"type": "delete-block", "block_id": "decisions::0::block-0"},
            {"type": "delete-block", "block_id": "decisions::0::block-2"},
        ],
    }
    result = _run_apply(tmp_repo, tmp_storage_root, plan)
    assert result["invalid"] == []
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    assert "前端分工" not in text
    assert "决策三" not in text
    assert "决策二" in text


def test_delete_block_invalid_block_idx(seed_branch_files, tmp_repo, tmp_storage_root):
    branch = _seed_decisions_with_three_blocks(seed_branch_files)
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {"type": "delete-block", "block_id": "decisions::0::block-99"},
            {"type": "delete-block", "block_id": "decisions::0::block-0"},
        ],
    }
    result = _run_apply(tmp_repo, tmp_storage_root, plan)
    # block-99 should land in invalid; block-0 should still go through.
    reasons = [str(v) for v in result["invalid"]]
    assert any("out of range" in r for r in reasons), reasons
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    assert "前端分工" not in text
    assert "决策二" in text


def test_delete_block_plus_entry_delete_no_double_touch(seed_branch_files, tmp_repo, tmp_storage_root):
    branch = _seed_decisions_with_three_blocks(seed_branch_files)
    # block-0 contains entries 0,1,2 (top + 2 sub-bullets). Add an entry-level
    # delete on entry 1 — the block delete should win and the entry delete
    # should be silently pruned (no double-touch / no spurious invalid).
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {"type": "delete-block", "block_id": "decisions::0::block-0"},
            {"type": "delete-entries", "ids": ["decisions::0::1"]},
        ],
    }
    result = _run_apply(tmp_repo, tmp_storage_root, plan)
    assert result["invalid"] == []
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    assert "前端分工" not in text
    assert "徐帅武" not in text  # was entry 1, removed via block delete
    assert "决策二" in text


def test_reset_file_beats_delete_block_on_same_file(seed_branch_files, tmp_repo, tmp_storage_root):
    branch = _seed_decisions_with_three_blocks(seed_branch_files)
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {"type": "reset-file", "file_key": "decisions"},
            {"type": "delete-block", "block_id": "decisions::0::block-0"},
        ],
    }
    result = _run_apply(tmp_repo, tmp_storage_root, plan)
    assert result["invalid"] == []
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    # All seeded content gone — file should be the v2 template now.
    assert "前端分工" not in text
    assert "决策二" not in text
    # Reset-file repopulates the canonical template; sanity-check it ran.
    assert "决策" in text  # template has a header containing 决策


def test_delete_block_all_blocks_leaves_placeholder(seed_branch_files, tmp_repo, tmp_storage_root):
    branch = _seed_decisions_with_three_blocks(seed_branch_files)
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {"type": "delete-block", "block_id": "decisions::0::block-0"},
            {"type": "delete-block", "block_id": "decisions::0::block-1"},
            {"type": "delete-block", "block_id": "decisions::0::block-2"},
        ],
    }
    _run_apply(tmp_repo, tmp_storage_root, plan)
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    # Section emptied — should fall back to placeholder.
    assert "- 待补充" in text
