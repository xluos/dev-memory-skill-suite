import json
import os
import subprocess
from pathlib import Path


UI_SERVER = Path(__file__).resolve().parent.parent / "lib" / "ui-server.js"


def _run_node(scan_root, expression):
    env = {**os.environ, "DEV_MEMORY_SCAN_ROOT": str(scan_root)}
    script = (
        f"const ui = require({json.dumps(str(UI_SERVER))});"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(result.stdout)


def _write_run(scan_root):
    runs = scan_root / "runs"
    runs.mkdir(parents=True)
    payload = {
        "run_id": "run-1",
        "run_kind": "scan",
        "started_at": "2026-07-24T01:00:00+00:00",
        "finished_at": "2026-07-24T01:00:05+00:00",
        "duration_ms": 5000,
        "scheduled": True,
        "session_count": 3,
        "candidate_count": 2,
        "done_count": 1,
        "summary_skipped_count": 0,
        "failed_count": 1,
        "discovery_skipped_count": 1,
        "observed_new_bytes": 4096,
        "eligible_new_bytes": 2048,
        "summary_usage": {"total_tokens": 123},
        "sessions": [
            {
                "session_id": "done-session",
                "repo_key": "repo-key",
                "branch": "main",
                "status": "done",
                "new_bytes": 1024,
                "semantic_messages": 4,
                "cursor_before": 0,
                "cursor_after": 1024,
                "apply_result": {"touched_targets": [{"file": "decisions.md"}]},
            },
            {
                "session_id": "failed-session",
                "repo_key": "repo-key",
                "branch": "main",
                "status": "failed",
                "new_bytes": 1024,
                "semantic_messages": 3,
                "cursor_before": 0,
                "error": "executor failed",
            },
            {
                "session_id": "discovery-session",
                "status": "skipped",
                "reason": "not_stopped",
                "new_bytes": 2048,
            },
        ],
    }
    (runs / "run-1.json").write_text(json.dumps(payload), encoding="utf-8")


def test_ui_scan_overview_returns_compact_run_summaries(tmp_path):
    scan_root = tmp_path / "session-scan"
    _write_run(scan_root)

    data = _run_node(scan_root, "ui.buildSessionScanData()")

    assert data["run_count"] == 1
    assert data["totals"]["done"] == 1
    assert data["totals"]["failed"] == 1
    assert data["totals"]["attention_runs"] == 1
    assert data["runs"][0]["outcome"] == "attention"
    assert data["runs"][0]["reason_counts"] == {"not_stopped": 1}
    assert "sessions" not in data["runs"][0]
    assert data["offset"] == 0
    assert data["has_more"] is False


def test_ui_run_detail_lists_tasks_and_aggregates_discovery_skips(tmp_path):
    scan_root = tmp_path / "session-scan"
    _write_run(scan_root)

    detail = _run_node(scan_root, 'ui.buildSessionScanRunDetail("run-1")')

    assert [task["session_id"] for task in detail["tasks"]] == [
        "done-session",
        "failed-session",
    ]
    assert detail["tasks"][0]["touched_targets"] == ["decisions.md"]
    assert detail["tasks"][1]["error"] == "executor failed"
    assert detail["discovery"]["reason_counts"] == {"not_stopped": 1}


def test_ui_scan_overview_supports_run_pagination(tmp_path):
    scan_root = tmp_path / "session-scan"
    _write_run(scan_root)
    original = json.loads((scan_root / "runs" / "run-1.json").read_text(encoding="utf-8"))
    original.update({
        "run_id": "run-2",
        "started_at": "2026-07-24T02:00:00+00:00",
    })
    (scan_root / "runs" / "run-2.json").write_text(json.dumps(original), encoding="utf-8")

    first = _run_node(scan_root, "ui.buildSessionScanData({limit: 1, offset: 0})")
    second = _run_node(scan_root, "ui.buildSessionScanData({limit: 1, offset: 1})")

    assert [run["run_id"] for run in first["runs"]] == ["run-2"]
    assert first["has_more"] is True
    assert [run["run_id"] for run in second["runs"]] == ["run-1"]
    assert second["has_more"] is False


def test_ui_run_detail_rejects_unsafe_run_id(tmp_path):
    data = _run_node(tmp_path / "session-scan", 'ui.buildSessionScanRunDetail("../secret")')
    assert data is None
