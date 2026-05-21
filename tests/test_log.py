"""Tests for the append-only log.md timeline added in v2.

Coverage goals:
- log.md is seeded by lazy-init at both layers (branch + repo) and has the
  expected H2-grep-friendly skeleton.
- capture record writes one event row; shared-* kind also mirrors into the
  repo log.
- rewrite-entry writes its own row carrying the entry id.
- tidy apply writes one row with accepted/rewritten counts.
- graduate apply writes a row into the repo log (the branch log is gone
  once the branch dir is archived).
- The header format always starts with ``## [`` so ``grep '^## \\['`` slices
  cleanly — that's the whole point of the log file.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dev_memory_common import append_log_event, template_log


LIB = Path(__file__).resolve().parent.parent / "lib"


def _run_capture(cmd_args, repo, storage_root):
    """Invoke dev_memory_capture.py with the supplied subcommand args."""
    proc = subprocess.run(
        [sys.executable, str(LIB / "dev_memory_capture.py"), *cmd_args,
         "--repo", str(repo), "--context-dir", str(storage_root),
         "--branch", "test-branch"],
        capture_output=True, text=True,
    )
    return proc


def _run_tidy(cmd_args, repo, storage_root):
    proc = subprocess.run(
        [sys.executable, str(LIB / "dev_memory_tidy.py"), *cmd_args,
         "--repo", str(repo), "--context-dir", str(storage_root),
         "--branch", "test-branch"],
        capture_output=True, text=True,
    )
    return proc


def _run_graduate(cmd_args, repo, storage_root):
    proc = subprocess.run(
        [sys.executable, str(LIB / "dev_memory_graduate.py"), *cmd_args,
         "--repo", str(repo), "--context-dir", str(storage_root),
         "--branch", "test-branch"],
        capture_output=True, text=True,
    )
    return proc


def _read_log(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _log_event_headers(text):
    return [ln for ln in text.splitlines() if ln.startswith("## [")]


def test_log_seeded_on_lazy_init(branch_dir):
    """Both branch log and repo log are present after lazy-init."""
    paths = branch_dir["paths"]
    assert paths["log"].exists()
    assert paths["repo_log"].exists()
    branch_content = paths["log"].read_text(encoding="utf-8")
    repo_content = paths["repo_log"].read_text(encoding="utf-8")
    # Skeleton sanity: hint about grep, no event headers yet.
    assert "grep" in branch_content
    assert "scope: branch:test-branch" in branch_content
    assert _log_event_headers(branch_content) == []
    assert _log_event_headers(repo_content) == []


def test_append_log_event_format():
    """The helper writes a single H2 + optional `- key: value` lines."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "log.md"
        p.write_text(template_log("branch:test"), encoding="utf-8")
        append_log_event(
            p, "capture", kind="decision",
            summary="不补 index、可能补 log",
            details=[("targets", "branch/decisions.md(append)"), ("blocked", 0)],
        )
        text = p.read_text(encoding="utf-8")
        headers = _log_event_headers(text)
        assert len(headers) == 1
        # Header always starts with ISO timestamp inside brackets.
        h = headers[0]
        assert h.startswith("## [") and "] capture" in h
        assert " · decision" in h
        assert "| 不补 index、可能补 log" in h
        assert "- targets: branch/decisions.md(append)" in text
        # blocked=0 still emitted (value is int, not empty)
        assert "- blocked: 0" in text


def test_append_log_event_skips_empty_details():
    """None and empty-string values are dropped from the detail list."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "log.md"
        p.write_text(template_log("branch:test"), encoding="utf-8")
        append_log_event(
            p, "capture", kind=None, summary="",
            details=[("a", "x"), ("b", None), ("c", "")],
        )
        text = p.read_text(encoding="utf-8")
        assert "- a: x" in text
        assert "- b:" not in text
        assert "- c:" not in text


def test_append_log_event_auto_creates_missing_file(tmp_path):
    """If log.md doesn't exist yet, the helper seeds it before appending."""
    p = tmp_path / "log.md"
    assert not p.exists()
    append_log_event(p, "test-action", summary="hello")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# 事件日志")
    assert "## [" in text and "test-action" in text


def test_append_log_event_long_summary_truncated(tmp_path):
    p = tmp_path / "log.md"
    long_text = "x" * 500
    append_log_event(p, "capture", summary=long_text)
    headers = _log_event_headers(p.read_text(encoding="utf-8"))
    assert len(headers) == 1
    # Header line is shorter than the raw 500-char input + must end with the
    # truncation marker.
    assert "…" in headers[0]
    assert len(headers[0]) < 250


def test_capture_record_appends_event(branch_dir, tmp_repo, tmp_storage_root):
    """A successful capture record adds exactly one row to branch log."""
    proc = _run_capture(
        ["record", "--kind", "decision", "--content", "不补 index、可能补 log"],
        tmp_repo, tmp_storage_root,
    )
    assert proc.returncode == 0, proc.stderr
    log_text = _read_log(branch_dir["paths"]["log"])
    headers = _log_event_headers(log_text)
    assert len(headers) == 1
    assert " capture" in headers[0]
    assert "不补 index" in headers[0]
    # decisions targets show up in details.
    assert "branch/decisions.md" in log_text


def test_capture_record_shared_kind_mirrors_to_repo_log(branch_dir, tmp_repo, tmp_storage_root):
    """Writing to a repo-shared kind also writes the same row into repo log."""
    proc = _run_capture(
        ["record", "--kind", "shared-decision", "--content", "全 repo 通用的决策"],
        tmp_repo, tmp_storage_root,
    )
    assert proc.returncode == 0, proc.stderr
    branch_headers = _log_event_headers(_read_log(branch_dir["paths"]["log"]))
    repo_headers = _log_event_headers(_read_log(branch_dir["paths"]["repo_log"]))
    # Mirrored: branch log captures the action AND repo log gets its own row.
    assert len(branch_headers) == 1
    assert len(repo_headers) == 1
    assert "全 repo 通用的决策" in repo_headers[0]


def test_capture_record_branch_only_kind_skips_repo_log(branch_dir, tmp_repo, tmp_storage_root):
    """Branch-scope writes must NOT pollute the repo log."""
    proc = _run_capture(
        ["record", "--kind", "decision", "--content", "branch-only thing"],
        tmp_repo, tmp_storage_root,
    )
    assert proc.returncode == 0
    branch_headers = _log_event_headers(_read_log(branch_dir["paths"]["log"]))
    repo_headers = _log_event_headers(_read_log(branch_dir["paths"]["repo_log"]))
    assert len(branch_headers) == 1
    assert repo_headers == []


def test_rewrite_entry_appends_event(branch_dir, tmp_repo, tmp_storage_root):
    """rewrite-entry writes its own log row with the entry id in details."""
    # Seed an entry to rewrite.
    proc = _run_capture(
        ["record", "--kind", "decision", "--content", "old decision text"],
        tmp_repo, tmp_storage_root,
    )
    assert proc.returncode == 0
    # Resolve the entry id by reading the decisions.md content.
    text = branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
    # The new entry is at section idx of "关键决策与原因"; with the template's
    # initial sections, that's index 1 (after "分支"). Entry idx 0 since the
    # placeholder was replaced.
    # Use capture's `show` to be deterministic-ish — but simpler: just call
    # rewrite-entry with the conventional id.
    eid = "decisions::1::0"

    rewrite = _run_capture(
        ["rewrite-entry", "--id", eid, "--content", "rewritten decision"],
        tmp_repo, tmp_storage_root,
    )
    assert rewrite.returncode == 0, rewrite.stderr
    log_text = _read_log(branch_dir["paths"]["log"])
    headers = _log_event_headers(log_text)
    # capture record + rewrite-entry → 2 rows.
    assert len(headers) == 2
    rewrite_row = [h for h in headers if "rewrite-entry" in h]
    assert len(rewrite_row) == 1
    assert f"- id: {eid}" in log_text
    assert "- previous: old decision text" in log_text


def test_tidy_apply_appends_event(branch_dir, tmp_repo, tmp_storage_root, tmp_path):
    """tidy apply writes a row with accepted/rewritten/backup details."""
    # Seed something to act on so apply has work.
    _run_capture(
        ["record", "--kind", "decision", "--content", "to be tidied"],
        tmp_repo, tmp_storage_root,
    )

    # Build a minimal plan that keeps everything (no-op apply).
    plan = {
        "scope": {"include_repo": False},
        "actions": [],
        "accepted_proposals": [],
        "rejected_proposals": [],
        "custom_proposals": [],
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")

    proc = _run_tidy(["apply", "--plan-file", str(plan_file)], tmp_repo, tmp_storage_root)
    assert proc.returncode == 0, proc.stderr

    log_text = _read_log(branch_dir["paths"]["log"])
    tidy_rows = [h for h in _log_event_headers(log_text) if "tidy" in h]
    assert len(tidy_rows) == 1
    assert " · apply" in tidy_rows[0]
    assert "- backup: tidy_backup_" in log_text


def test_graduate_apply_writes_repo_log(branch_dir, tmp_repo, tmp_storage_root, tmp_path):
    """graduate apply writes into the repo log (branch log is archived away)."""
    repo_log = branch_dir["paths"]["repo_log"]

    harvest = {
        "repo_decisions": [
            {"section": "跨分支通用决策", "body": "- harvested item", "mode": "append"}
        ],
        "notes": "归档测试",
        "archive": True,
    }
    harvest_file = tmp_path / "harvest.json"
    harvest_file.write_text(json.dumps(harvest), encoding="utf-8")

    proc = _run_graduate(
        ["apply", "--harvest-file", str(harvest_file)],
        tmp_repo, tmp_storage_root,
    )
    assert proc.returncode == 0, proc.stderr

    repo_log_text = _read_log(repo_log)
    headers = _log_event_headers(repo_log_text)
    grad_rows = [h for h in headers if "graduate" in h]
    assert len(grad_rows) == 1
    assert "harvested=1" in grad_rows[0]
    assert "- archived_to: test-branch__" in repo_log_text


def test_log_headers_are_grep_friendly(branch_dir, tmp_repo, tmp_storage_root):
    """Every event header must start with `## [` so grep slicing works."""
    _run_capture(["record", "--kind", "decision", "--content", "A"], tmp_repo, tmp_storage_root)
    _run_capture(["record", "--kind", "risk", "--content", "B"], tmp_repo, tmp_storage_root)
    log_text = _read_log(branch_dir["paths"]["log"])
    headers = _log_event_headers(log_text)
    assert len(headers) == 2
    for h in headers:
        # ISO timestamp pattern after the bracket.
        assert h.startswith("## [")
        # Must contain a closing bracket within first 30 chars (ISO is 25).
        close_idx = h.find("]")
        assert 10 < close_idx < 30
