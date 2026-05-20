"""Tests for delete-block content_hash fingerprint guard.

Covers two pieces:
  1. `prepare` emits each block with a 16-char hex `content_hash`, and the
     hash is deterministic over identical block content and distinct over
     different content.
  2. `apply` honors an optional `expected_content_hash` on delete-block
     actions — match deletes normally, mismatch lands in `invalid` with
     reason `content_hash_mismatch` and leaves the file untouched, while
     absence (the legacy form) still works exactly as before.

The 7th test re-verifies that block_idx out-of-range still goes through
the same invalid path even when `expected_content_hash` is supplied —
content_hash never short-circuits the boundary check.
"""

import json
import re
import subprocess
import sys
from pathlib import Path


LIB = Path(__file__).resolve().parent.parent / "lib"


def _run_prepare(repo, storage):
    cmd = [
        sys.executable, str(LIB / "dev_memory_tidy.py"), "prepare",
        "--repo", str(repo), "--context-dir", str(storage),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"prepare failed: stderr={proc.stderr!r} stdout={proc.stdout!r}"
    return json.loads(proc.stdout)


def _run_apply(repo, storage, plan):
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


def _seed_three_blocks(seed_branch_files):
    """Three distinct top-level blocks under one section. Block 0 carries
    sub-bullets + an orphan paragraph so we exercise multi-line hashing."""
    body = "\n".join([
        "# decisions",
        "",
        "## 关键决策与原因",
        "",
        "- **前端分工**",
        "  - 徐帅武 owns FE-1 + FE-3",
        "  - 湛憬禧 owns FE-3.4",
        "**Why:** 2026-05-14 会议拍板",
        "",
        "- 决策二: 切到 feature flag",
        "",
        "- 决策三: 弃用旧接口",
        "",
    ])
    return seed_branch_files({"decisions": body})


_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _hash_for(blocks, block_id):
    for b in blocks:
        if b["id"] == block_id:
            return b["content_hash"]
    raise AssertionError(f"no block with id {block_id!r} in {[b['id'] for b in blocks]}")


def test_prepare_emits_16char_hex_content_hash(seed_branch_files, tmp_repo, tmp_storage_root):
    _seed_three_blocks(seed_branch_files)
    out = _run_prepare(tmp_repo, tmp_storage_root)
    assert out["block_count"] >= 3
    for b in out["blocks"]:
        assert "content_hash" in b, f"block missing content_hash: {b}"
        assert _HEX16.match(b["content_hash"]), f"bad hash shape: {b['content_hash']!r}"


def test_content_hash_is_deterministic(seed_branch_files, tmp_repo, tmp_storage_root):
    _seed_three_blocks(seed_branch_files)
    a = _run_prepare(tmp_repo, tmp_storage_root)
    b = _run_prepare(tmp_repo, tmp_storage_root)
    # Same source content + same parser → same hash for every block id.
    a_map = {x["id"]: x["content_hash"] for x in a["blocks"]}
    b_map = {x["id"]: x["content_hash"] for x in b["blocks"]}
    assert a_map == b_map


def test_content_hash_differs_across_distinct_blocks(seed_branch_files, tmp_repo, tmp_storage_root):
    _seed_three_blocks(seed_branch_files)
    out = _run_prepare(tmp_repo, tmp_storage_root)
    # Only look at the three blocks we seeded under decisions.md — the
    # other branch files are populated with the lazy-init `- 待补充`
    # placeholder, which is *meant* to hash identically across files
    # (same source bytes, same hash — that's the contract, not a bug).
    seeded = [
        b["content_hash"]
        for b in out["blocks"]
        if b["file_key"] == "decisions"
    ]
    assert len(seeded) == 3, f"expected 3 decisions blocks, got {seeded}"
    assert len(set(seeded)) == 3, f"unexpected hash collision among seeded blocks: {seeded}"


def test_apply_delete_block_with_matching_hash_deletes(seed_branch_files, tmp_repo, tmp_storage_root):
    branch = _seed_three_blocks(seed_branch_files)
    prep = _run_prepare(tmp_repo, tmp_storage_root)
    target_id = "decisions::0::block-0"
    expected = _hash_for(prep["blocks"], target_id)
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {
                "type": "delete-block",
                "block_id": target_id,
                "expected_content_hash": expected,
            },
        ],
    }
    result = _run_apply(tmp_repo, tmp_storage_root, plan)
    assert result["invalid"] == []
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    assert "前端分工" not in text
    assert "决策二" in text
    assert "决策三" in text


def test_apply_delete_block_with_mismatched_hash_skips_and_marks_invalid(seed_branch_files, tmp_repo, tmp_storage_root):
    branch = _seed_three_blocks(seed_branch_files)
    before = branch["paths"]["decisions"].read_text(encoding="utf-8")
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {
                "type": "delete-block",
                "block_id": "decisions::0::block-0",
                # 16 hex chars but deliberately not the real hash.
                "expected_content_hash": "deadbeefdeadbeef",
            },
        ],
    }
    result = _run_apply(tmp_repo, tmp_storage_root, plan)
    # Mismatch must surface in invalid with the dedicated reason and the
    # expected/actual hash fields for debugging.
    assert len(result["invalid"]) == 1, result["invalid"]
    entry = result["invalid"][0]
    assert entry["reason"] == "content_hash_mismatch"
    assert entry["expected"] == "deadbeefdeadbeef"
    assert _HEX16.match(entry["actual"]), entry
    assert entry["block_idx"] == 0
    assert entry["section_idx"] == 0
    assert entry["file_key"] == "decisions"
    # File must be untouched.
    after = branch["paths"]["decisions"].read_text(encoding="utf-8")
    assert after == before


def test_apply_delete_block_without_expected_hash_is_back_compat(seed_branch_files, tmp_repo, tmp_storage_root):
    """A plan without expected_content_hash must behave exactly like the
    pre-guard era — block_idx alone drives the delete."""
    branch = _seed_three_blocks(seed_branch_files)
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {"type": "delete-block", "block_id": "decisions::0::block-0"},
        ],
    }
    result = _run_apply(tmp_repo, tmp_storage_root, plan)
    assert result["invalid"] == []
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    assert "前端分工" not in text
    assert "决策二" in text


def test_apply_delete_block_out_of_range_still_invalid_with_hash(seed_branch_files, tmp_repo, tmp_storage_root):
    """Boundary check fires first — content_hash never papers over an
    out-of-range block_idx."""
    branch = _seed_three_blocks(seed_branch_files)
    plan = {
        "scope": {"include_repo": False},
        "actions": [
            {
                "type": "delete-block",
                "block_id": "decisions::0::block-99",
                # Provide a hash; it's irrelevant once the index is bad.
                "expected_content_hash": "deadbeefdeadbeef",
            },
        ],
    }
    result = _run_apply(tmp_repo, tmp_storage_root, plan)
    assert len(result["invalid"]) == 1
    entry = result["invalid"][0]
    assert entry["reason"] == "block_idx out of range after re-parse"
    assert entry["block_idx"] == 99
    # And the file is untouched.
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    assert "前端分工" in text
    assert "决策二" in text
    assert "决策三" in text
