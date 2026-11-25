import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import dev_memory_daily as daily
import dev_memory_session_scan as scanner


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def test_session_failure_backoff_quarantine_and_growth_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "SCAN_ROOT", tmp_path / "scan")
    cfg = {
        **scanner.default_scan_config(),
        "max_session_failures": 3,
        "failure_backoff_minutes": [30, 120],
    }
    session = {"session_id": "s-fail", "path": "/tmp/s-fail.jsonl", "size": 500}

    st1 = scanner._record_session_failure(session, "boom-1", cfg)
    assert st1["consecutive_failures"] == 1
    assert scanner._session_failure_gate(st1, 500, cfg) == "failure_backoff"
    # Transcript growth immediately lifts the cooldown.
    assert scanner._session_failure_gate(st1, 600, cfg) is None

    scanner._record_session_failure(session, "boom-2", cfg)
    st3 = scanner._record_session_failure(session, "boom-3", cfg)
    assert st3["consecutive_failures"] == 3
    assert scanner._session_failure_gate(st3, 500, cfg) == "failure_quarantined"
    assert scanner._session_failure_gate(st3, 501, cfg) is None


def test_run_circuit_breaker_stops_after_consecutive_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "SCAN_ROOT", tmp_path / "scan")
    calls = []

    def fake_choose(_cfg):
        return "traecli", {"command": "traecli"}

    def fake_run_retries(*_args, **_kwargs):
        calls.append(1)
        return None, [{"invocation": "x", "error": "executor failed", "usage": {"total_tokens": 10}}]

    monkeypatch.setattr(scanner, "choose_executor", fake_choose)
    monkeypatch.setattr(scanner, "run_executor_with_retries", fake_run_retries)

    sessions = []
    for i in range(5):
        sessions.append({
            "session_id": f"s-{i}",
            "path": str(tmp_path / f"s-{i}.jsonl"),
            "size": 100,
            "end_offset": 100,
            "cursor_before": 0,
            "new_bytes": 100,
            "meta": {"cwd": str(tmp_path)},
            "messages": [{"role": "user", "text": f"msg {i}", "timestamp": "2026-11-25T00:00:00Z"}],
            "session_usage": None,
            "internal_marker": False,
            "target": {"repo_root": str(tmp_path), "repo_key": "repo-1", "branch": "main", "repo_dir": str(tmp_path), "branch_dir": str(tmp_path)},
            "status": "candidate",
            "reason": None,
            "candidate_paths": [],
        })

    cfg = {**scanner.default_scan_config(), "circuit_breaker_threshold": 2}
    args = argparse.Namespace(dry_run=False, json=True, scheduled=True, executor=None)
    code = scanner._execute_sessions(cfg, args, sessions, "run-cb", time.time())
    assert code == 1
    assert len(calls) == 2

    saved = json.loads((tmp_path / "scan" / "runs" / "run-cb.json").read_text(encoding="utf-8"))
    assert saved["circuit_breaker"]["triggered"] is True
    assert saved["circuit_breaker"]["skipped_candidates"] == 3
    skipped_reasons = [s["reason"] for s in saved["sessions"] if s["status"] == "skipped"]
    assert skipped_reasons == ["circuit_breaker_open"] * 3


def test_daily_prune_empty_repos_and_drift_detection(tmp_path):
    storage = tmp_path / "repos"
    # 1. Old empty repo -> should be pruned
    empty_old = storage / "junk-old-1234"
    (empty_old / "repo").mkdir(parents=True)
    (empty_old / "repo" / "overview.md").write_text("# 仓库概览\n\n## 目标\n\n（待补充）\n", encoding="utf-8")
    old_t = time.time() - 48 * 3600
    os.utime(empty_old / "repo" / "overview.md", (old_t, old_t))
    os.utime(empty_old / "repo", (old_t, old_t))
    os.utime(empty_old, (old_t, old_t))

    # 2. Fresh empty repo -> protected by grace period
    empty_new = storage / "junk-new-5678"
    (empty_new / "repo").mkdir(parents=True)

    # 3. Drifted repo: same repo_root under two repo keys
    real_repo = _init_git_repo(tmp_path / "my-repo")
    for key, text in (("my-repo-aaaa", "- 结论A\n"), ("my-repo-bbbb", "- 结论B\n\n- 结论C\n")):
        rdir = storage / key
        (rdir / "repo").mkdir(parents=True)
        (rdir / "branches" / "main").mkdir(parents=True)
        (rdir / "repo" / "manifest.json").write_text(json.dumps({"repo_root": str(real_repo)}), encoding="utf-8")
        (rdir / "branches" / "main" / "decisions.md").write_text(f"# 分支决策\n\n## 关键决策与原因\n\n{text}", encoding="utf-8")

    dry = daily.prune_empty_repos(storage, grace_hours=24, dry_run=True)
    assert dry["pruned_count"] == 1
    assert dry["pruned"][0]["repo_key"] == "junk-old-1234"
    assert dry["skipped_grace_count"] == 1
    assert empty_old.exists()

    real = daily.prune_empty_repos(storage, grace_hours=24, dry_run=False)
    assert real["pruned_count"] == 1
    assert not empty_old.exists()
    assert empty_new.exists()

    drifts = daily.detect_repo_key_drift(storage)
    assert len(drifts) == 1
    assert drifts[0]["repo_root"] == str(real_repo)
    assert len(drifts[0]["repo_keys"]) == 2


def test_daily_only_tidies_changed_branches(tmp_path):
    storage = tmp_path / "repos"
    repo = _init_git_repo(tmp_path / "active-repo")
    rdir = storage / "active-repo-1111"
    bdir = rdir / "branches" / "main"
    (rdir / "repo").mkdir(parents=True)
    bdir.mkdir(parents=True)
    (rdir / "repo" / "manifest.json").write_text(json.dumps({"repo_root": str(repo)}), encoding="utf-8")
    (bdir / "manifest.json").write_text(json.dumps({"branch": "main"}), encoding="utf-8")
    entries = "\n\n".join(f"- 有效决策条目 {i}" for i in range(10))
    (bdir / "decisions.md").write_text(f"# 分支决策\n\n## 关键决策与原因\n\n{entries}\n", encoding="utf-8")

    targets1, skipped1 = daily.discover_changed_tidy_targets(storage, {}, lookback_hours=24, min_blocks=8)
    assert len(targets1) == 1
    assert skipped1 == 0

    # Once fingerprint is recorded, unchanged branch is skipped.
    state = {targets1[0]["target_key"]: {"fingerprint": targets1[0]["fingerprint"]}}
    targets2, skipped2 = daily.discover_changed_tidy_targets(storage, state, lookback_hours=24, min_blocks=8)
    assert targets2 == []
    assert skipped2 == 1

    # After modifying the branch memory, it becomes eligible again.
    (bdir / "decisions.md").write_text(
        f"# 分支决策\n\n## 关键决策与原因\n\n{entries}\n\n- 新增决策条目 11\n",
        encoding="utf-8",
    )
    targets3, skipped3 = daily.discover_changed_tidy_targets(storage, state, lookback_hours=24, min_blocks=8)
    assert len(targets3) == 1
    assert skipped3 == 0


def test_unquote_git_path_and_placeholder_cleanup(tmp_path):
    from dev_memory_common import _unquote_git_path, merged_focus_areas

    quoted = '"research_notes/\\344\\270\\255\\346\\226\\207.md"'
    assert _unquote_git_path(quoted) == "research_notes/中文.md"
    cleaned_focus = merged_focus_areas(
        ["research_notes/中文.md", "reports/周报.md"],
        ['"research_notes/foo', '"reports/bar'],
    )
    assert all(not item.startswith('"') for item in cleaned_focus)

    storage = tmp_path / "repos"
    bdir = storage / "repo-x" / "branches" / "main"
    bdir.mkdir(parents=True)
    dec = bdir / "decisions.md"
    dec.write_text(
        "# 分支决策\n\n## 关键决策与原因\n\n- 可独立理解的完整结论\n\n- 真实决策内容\n",
        encoding="utf-8",
    )
    res = daily.cleanup_placeholder_entries(storage, dry_run=False)
    assert res["cleaned_count"] == 1
    text = dec.read_text(encoding="utf-8")
    assert "可独立理解的完整结论" not in text
    assert "真实决策内容" in text
