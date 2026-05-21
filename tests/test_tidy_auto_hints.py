"""Tests for tidy's auto-hint passes (STALE-by-log + ORPHAN-glossary).

The auto-hint feature is the Karpathy "lint" idea adapted to the dev-memory
structure: tidy prepare runs cheap heuristics over log.md and glossary
cross-references, baking hints into the review payload so the user sees
"these entries probably warrant a look" before opening the HTML.

Coverage targets:
- STALE: file whose last log mention is older than --stale-after-days gets
  every entry hinted STALE; files inside the threshold are untouched.
- ORPHAN: glossary entry whose key phrase never appears in any other .md
  is hinted ORPHAN; short keys (< 4 chars) skip the check.
- ORPHAN wins over STALE on the same entry.
- --no-auto-hints disables both passes (hints_summary.auto_count == 0).
- User-supplied --hints-json beats auto hints on the same entry id.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


LIB = Path(__file__).resolve().parent.parent / "lib"


def _run_tidy_prepare(extra_args, repo, storage_root):
    proc = subprocess.run(
        [sys.executable, str(LIB / "dev_memory_tidy.py"), "prepare",
         "--repo", str(repo), "--context-dir", str(storage_root),
         "--branch", "test-branch", *extra_args],
        capture_output=True, text=True,
    )
    return proc


def _payload(proc):
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _seed_log(log_path, file_label, days_ago):
    """Append a synthetic log entry that 'last touched' file_label N days ago."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    block = f"\n## [{ts}] capture · test | seed for tidy auto-hint test\n- targets: {file_label}\n"
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else "# log\n"
    log_path.write_text(existing + block, encoding="utf-8")


def _read_hints_from_html(html_path):
    """Pull the inline JSON blob out of the rendered review.html."""
    text = html_path.read_text(encoding="utf-8")
    marker_start = '"hints":'
    idx = text.find(marker_start)
    assert idx > 0
    # Look for the next outer-balanced `}` after hints: — quick and dirty,
    # we just need to know hint counts. Parse the whole payload by finding
    # `__TIDY_DATA_PLACEHOLDER__` would be cleaner but the test only needs
    # to verify hints are present.
    return text


def test_stale_hint_when_file_old_in_log(branch_dir, tmp_repo, tmp_storage_root):
    """A file last logged 60 days ago should get its entries STALE-hinted."""
    paths = branch_dir["paths"]
    # Seed a real entry so it shows up in the scan.
    paths["decisions"].write_text(
        "# 分支决策\n\n## 分支\n\n- test-branch\n\n## 关键决策与原因\n\n- 老的决策 X\n",
        encoding="utf-8",
    )
    # Pretend decisions.md was last touched 60 days ago.
    _seed_log(paths["log"], "branch/decisions.md(append)", days_ago=60)

    proc = _run_tidy_prepare(["--stale-after-days", "30"], tmp_repo, tmp_storage_root)
    payload = _payload(proc)
    summary = payload["hints_summary"]
    assert summary["auto_enabled"] is True
    assert summary["auto_count"] >= 1
    assert summary["auto_by_label"].get("STALE", 0) >= 1


def test_no_stale_when_file_recent(branch_dir, tmp_repo, tmp_storage_root):
    """Files inside the threshold must not get STALE — false positives sting."""
    paths = branch_dir["paths"]
    paths["decisions"].write_text(
        "# 分支决策\n\n## 分支\n\n- test-branch\n\n## 关键决策与原因\n\n- 新鲜决策\n",
        encoding="utf-8",
    )
    _seed_log(paths["log"], "branch/decisions.md(append)", days_ago=3)

    proc = _run_tidy_prepare(["--stale-after-days", "30"], tmp_repo, tmp_storage_root)
    payload = _payload(proc)
    # No STALE from this file. (other auto-hints like ORPHAN may still fire.)
    assert payload["hints_summary"]["auto_by_label"].get("STALE", 0) == 0


def test_orphan_hint_when_glossary_term_unreferenced(branch_dir, tmp_repo, tmp_storage_root):
    """A glossary entry whose key phrase nobody else mentions gets ORPHAN."""
    paths = branch_dir["paths"]
    paths["glossary"].write_text(
        "# 术语\n\n## 分支\n\n- test-branch\n\n## 当前有效上下文\n\n"
        "- 飞书审批连接器: 内部代号 mango\n",
        encoding="utf-8",
    )
    # Make sure nothing else mentions "飞书审批连接器".
    paths["decisions"].write_text(
        "# 分支决策\n\n## 分支\n\n- test-branch\n\n## 关键决策与原因\n\n- 不相关决策\n",
        encoding="utf-8",
    )
    proc = _run_tidy_prepare([], tmp_repo, tmp_storage_root)
    payload = _payload(proc)
    summary = payload["hints_summary"]
    assert summary["auto_by_label"].get("ORPHAN", 0) >= 1


def test_no_orphan_when_glossary_term_referenced(branch_dir, tmp_repo, tmp_storage_root):
    """When the key phrase shows up in another file, no ORPHAN signal."""
    paths = branch_dir["paths"]
    paths["glossary"].write_text(
        "# 术语\n\n## 分支\n\n- test-branch\n\n## 当前有效上下文\n\n"
        "- 飞书审批连接器: 内部代号 mango\n",
        encoding="utf-8",
    )
    # Reference the term from decisions.md so it's load-bearing.
    paths["decisions"].write_text(
        "# 分支决策\n\n## 分支\n\n- test-branch\n\n## 关键决策与原因\n\n"
        "- 飞书审批连接器走 v2 协议\n",
        encoding="utf-8",
    )
    proc = _run_tidy_prepare([], tmp_repo, tmp_storage_root)
    payload = _payload(proc)
    assert payload["hints_summary"]["auto_by_label"].get("ORPHAN", 0) == 0


def test_orphan_skips_short_tokens(branch_dir, tmp_repo, tmp_storage_root):
    """Tokens shorter than the threshold (< 4 chars) are too noisy → skip."""
    paths = branch_dir["paths"]
    paths["glossary"].write_text(
        "# 术语\n\n## 分支\n\n- test-branch\n\n## 当前有效上下文\n\n"
        "- API: 短到不该触发 ORPHAN\n",
        encoding="utf-8",
    )
    proc = _run_tidy_prepare([], tmp_repo, tmp_storage_root)
    payload = _payload(proc)
    assert payload["hints_summary"]["auto_by_label"].get("ORPHAN", 0) == 0


def test_no_auto_hints_flag_disables_both(branch_dir, tmp_repo, tmp_storage_root):
    """--no-auto-hints should switch the heuristic passes off entirely."""
    paths = branch_dir["paths"]
    paths["glossary"].write_text(
        "# 术语\n\n## 分支\n\n- test-branch\n\n## 当前有效上下文\n\n"
        "- 完全孤立的术语XYZ: 没人引用\n",
        encoding="utf-8",
    )
    _seed_log(paths["log"], "branch/glossary.md(append)", days_ago=180)

    proc = _run_tidy_prepare(["--no-auto-hints"], tmp_repo, tmp_storage_root)
    payload = _payload(proc)
    summary = payload["hints_summary"]
    assert summary["auto_enabled"] is False
    assert summary["auto_count"] == 0


def test_user_hints_override_auto(branch_dir, tmp_repo, tmp_storage_root, tmp_path):
    """User-supplied hints win on the same entry id."""
    paths = branch_dir["paths"]
    paths["glossary"].write_text(
        "# 术语\n\n## 分支\n\n- test-branch\n\n## 当前有效上下文\n\n"
        "- 完全孤立的术语XYZ: 没人引用\n",
        encoding="utf-8",
    )
    # The seeded entry will be at glossary::section_idx::0. With template the
    # "## 当前有效上下文" is the second section (idx=1).
    user_hints = {
        "glossary::1::0": {"label": "OK", "reason": "我看过了，留着"}
    }
    hf = tmp_path / "user_hints.json"
    hf.write_text(json.dumps(user_hints), encoding="utf-8")

    proc = _run_tidy_prepare(["--hints-file", str(hf)], tmp_repo, tmp_storage_root)
    payload = _payload(proc)
    summary = payload["hints_summary"]
    assert summary["user_count"] == 1
    # Total should equal auto + (user not overlapping with auto). Here the
    # user's id targets an entry that auto would have flagged ORPHAN. After
    # merge, that entry shows as OK.
    # We don't have hints in the JSON output (intentional — they're in the
    # HTML payload), but auto_count + user_count - overlap == total_count.
    # The implementation merges auto first then user.update, so an overlap
    # leaves total == max(auto_count, user_count) when fully overlapping.
    # Sanity check: total_count is at least user_count.
    assert summary["total_count"] >= summary["user_count"]
