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
    assert ("branch/decisions.md", "关键决策与原因", "append") in touched
    assert ("branch/risks.md", "阻塞与注意点", "append") in touched
    assert ("branch/glossary.md", "当前有效上下文", "append") in touched

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


def test_apply_summary_output_noop_does_not_update_capture_manifest(branch_dir):
    manifest_path = branch_dir["paths"]["manifest"]
    before = json.loads(manifest_path.read_text(encoding="utf-8"))

    code, out = _run_apply(branch_dir, {"title": "无新增核心消息", "skip_reason": "没有新增有效内容"})

    assert code == 0
    assert out["touched_targets"] == []
    assert out["actions"] == []
    assert out["skip_reason"] == "没有新增有效内容"
    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after == before


def test_apply_summary_output_preflights_targeted_edits(seed_branch_files):
    branch = seed_branch_files({
        "decisions": (
            "# 分支决策\n\n"
            "## 关键决策与原因\n\n"
            "- 决策 A\n"
        ),
    })
    payload = {
        "rewrites": [
            {"id": "decisions::0::99", "content": "不存在的 rewrite"},
        ],
    }

    code, out = _run_apply(branch, payload)

    assert code == 1
    assert "entry_idx 99" in out["error"]


def test_apply_summary_output_compacts_shared_decisions(branch_dir):
    payload = {
        "shared_decisions": [
            {
                "summary": "启动本地服务前先探测现有进程",
                "reason": "避免重复启动占端口",
                "impact": "所有前端本地验证",
            }
        ]
    }

    code, out = _run_apply(branch_dir, payload)

    assert code == 0
    touched = {(item["file"], item["section"], item["mode"]) for item in out["touched_targets"]}
    assert ("repo/decisions.md", "跨分支通用决策", "append") in touched
    repo_decisions = branch_dir["paths"]["repo_decisions"].read_text(encoding="utf-8")
    assert "启动本地服务前先探测现有进程" in repo_decisions
    assert "- 原因:" not in repo_decisions
    assert "- 影响范围:" not in repo_decisions


def test_apply_summary_output_prunes_repo_shared_sections(seed_branch_files):
    old_entries = "\n".join(f"- 旧共享规则 {idx}" for idx in range(25))
    branch = seed_branch_files({
        "repo_decisions": (
            "# 跨分支通用决策\n\n"
            "## 仓库\n\n"
            "- test-repo\n\n"
            "## 跨分支通用决策\n\n"
            f"{old_entries}\n"
        ),
    })

    code, out = _run_apply(branch, {"title": "无新增", "skip_reason": "没有新增有效内容"})

    assert code == 0
    assert any(action["op"] == "prune-repo-shared" for action in out["actions"])
    repo_decisions = branch["paths"]["repo_decisions"].read_text(encoding="utf-8")
    assert "旧共享规则 0" not in repo_decisions
    assert "旧共享规则 4" not in repo_decisions
    assert "旧共享规则 5" in repo_decisions
    assert "旧共享规则 24" in repo_decisions
