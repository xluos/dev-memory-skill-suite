import argparse
import io
import json
from contextlib import redirect_stderr, redirect_stdout

import dev_memory_capture as cap


def _run_apply(branch_info, payload, *, force=False):
    args = argparse.Namespace(
        repo=str(branch_info["repo_root"]),
        context_dir=str(branch_info["storage_root"]),
        branch=branch_info["branch_name"],
        json=json.dumps(payload, ensure_ascii=False),
        json_file=None,
        force=force,
    )
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        try:
            code = cap.command_apply_summary_output(args)
        except RuntimeError as exc:
            return 1, {"error": str(exc)}
    return code, json.loads(out_buf.getvalue())


def test_apply_summary_output_upserts_and_appends(branch_dir):
    payload = {
        "title": "summary checkpoint",
        "progress": "当前进展来自 summary output",
        "next": "下一步来自 summary output",
        "decisions": [
            {"summary": "结构化输出由代码落盘", "reason": "agent 不应拼 CLI", "impact": "SessionEnd"},
        ],
        "risks": ["保留一个新风险"],
        "glossary": ["summary_output.json 是 agent 输出 schema"],
    }

    code, out = _run_apply(branch_dir, payload)

    assert code == 0
    assert out["mode"] == "apply-summary-output"
    touched = {(item["file"], item["section"], item["mode"]) for item in out["touched_targets"]}
    assert ("branch/progress.md", "当前进展", "upsert") in touched
    assert ("branch/progress.md", "下一步", "upsert") in touched
    assert ("branch/decisions.md", "关键决策与原因", "append") in touched
    assert ("branch/risks.md", "阻塞与注意点", "append") in touched
    assert ("branch/glossary.md", "当前有效上下文", "append") in touched

    progress = branch_dir["paths"]["progress"].read_text(encoding="utf-8")
    assert "当前进展来自 summary output" in progress
    assert "下一步来自 summary output" in progress
    decisions = branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
    assert "结构化输出由代码落盘" in decisions


def test_apply_summary_output_rewrites_and_deletes(seed_branch_files):
    branch = seed_branch_files({
        "decisions": (
            "# 分支决策\n\n"
            "## 关键决策与原因\n\n"
            "- 旧决策 A\n"
            "- 旧决策 B\n"
        ),
        "risks": (
            "# 风险\n\n"
            "## 阻塞与注意点\n\n"
            "- 已解决风险\n"
            "- 仍有效风险\n"
        ),
    })
    payload = {
        "rewrites": [
            {"id": "decisions::0::1", "content": "新决策 B", "reason": "旧结论失效"},
        ],
        "deletes": [
            {"id": "risks::0::0", "reason": "风险已解除"},
        ],
    }

    code, out = _run_apply(branch, payload)

    assert code == 0
    actions = {item["op"]: item for item in out["actions"]}
    assert actions["rewrite-entry"]["previous_first_line"] == "旧决策 B"
    assert actions["delete-entry"]["deleted_first_line"] == "已解决风险"
    decisions = branch["paths"]["decisions"].read_text(encoding="utf-8")
    risks = branch["paths"]["risks"].read_text(encoding="utf-8")
    assert "新决策 B" in decisions
    assert "旧决策 B" not in decisions
    assert "已解决风险" not in risks
    assert "仍有效风险" in risks


def test_apply_summary_output_blocks_duplicate_appends(branch_dir):
    _run_apply(branch_dir, {"decisions": [{"summary": "重复决策", "reason": "seed"}]})

    code, out = _run_apply(branch_dir, {"decisions": [{"summary": "重复决策", "reason": "again"}]})

    assert code == 2
    assert out["dedup_blocked"][0]["kind"] == "decision"
    decisions = branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
    assert decisions.count("重复决策") == 1


def test_apply_summary_output_preflights_targeted_edits(seed_branch_files):
    branch = seed_branch_files({
        "progress": (
            "# 当前进展\n\n"
            "## 当前进展\n\n"
            "旧进展\n"
        ),
        "decisions": (
            "# 分支决策\n\n"
            "## 关键决策与原因\n\n"
            "- 决策 A\n"
        ),
    })
    payload = {
        "progress": "不应落盘的新进展",
        "rewrites": [
            {"id": "decisions::0::99", "content": "不存在的 rewrite"},
        ],
    }

    code, out = _run_apply(branch, payload)

    assert code == 1
    assert "entry_idx 99" in out["error"]
    progress = branch["paths"]["progress"].read_text(encoding="utf-8")
    assert "旧进展" in progress
    assert "不应落盘的新进展" not in progress
