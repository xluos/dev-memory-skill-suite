"""
Capture dedup tests.

Covers similarity_check (pure function) and _check_dedup_for_kind (file-aware
wrapper), plus end-to-end command_record behavior for the most important
gates: append-mode kinds block on duplicates, upsert kinds never block,
placeholder entries and AUTO blocks are ignored, --force bypasses, and the
batch (summary-json) path emits a dedup_blocked list.
"""
import argparse
import io
import json
import sys
from contextlib import redirect_stdout

import pytest

import dev_memory_capture as cap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_args(branch_info, *, kind=None, content=None, summary_json=None,
                  title=None, force=False, auto=False):
    return argparse.Namespace(
        repo=str(branch_info["repo_root"]),
        context_dir=str(branch_info["storage_root"]),
        branch=branch_info["branch_name"],
        kind=kind,
        auto=auto,
        title=title,
        content=content,
        content_file=None,
        summary=None,
        summary_file=None,
        user_input=None,
        user_input_file=None,
        summary_json=summary_json,
        force=force,
    )


def _run_record(branch_info, **kwargs):
    """Run command_record with a stdout buffer; return (exit_code, parsed_json)."""
    args = _record_args(branch_info, **kwargs)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cap.command_record(args)
    out = buf.getvalue().strip()
    parsed = json.loads(out) if out else None
    return code, parsed


# ---------------------------------------------------------------------------
# similarity_check pure-function tests
# ---------------------------------------------------------------------------

class TestSimilarityCheck:
    def test_no_section_body_returns_empty(self):
        assert cap.similarity_check("something new", "") == []

    def test_no_matches_below_threshold(self):
        section = "- 完全不相关的另一回事\n- 又一条独立内容"
        out = cap.similarity_check("FE-3 工作台改造方案", section)
        assert out == []

    def test_exact_match_high_similarity(self):
        section = "- FE-3 分工：徐帅武只做工作台\n- 其他无关条目"
        matches = cap.similarity_check("FE-3 分工：徐帅武只做工作台", section)
        assert len(matches) == 1
        assert matches[0]["entry_idx"] == 0
        assert matches[0]["similarity"] >= 0.99

    def test_near_duplicate_mid_high_similarity(self):
        # ~0.85 — same first line, one char tweak
        section = "- 前端分工：徐帅武负责工作台和管理员页面\n"
        matches = cap.similarity_check(
            "前端分工：徐帅武负责工作台和管理页面",
            section,
        )
        assert len(matches) == 1
        assert 0.7 <= matches[0]["similarity"] <= 1.0

    def test_just_above_threshold_kept(self):
        # 0.72 ratio range
        section = "- FE-3.1 协议弹窗增加门禁逻辑\n"
        matches = cap.similarity_check(
            "FE-3.1 协议弹窗加签字门禁逻辑",
            section,
        )
        assert len(matches) == 1
        assert matches[0]["similarity"] >= 0.7

    def test_below_threshold_dropped(self):
        section = "- 完全无关：服务端 API 设计讨论\n"
        out = cap.similarity_check("前端 UI 协议组件实现", section)
        assert out == []

    def test_supersedes_keyword_boosts(self):
        # Without supersedes — ratio would be ~0.5 (below threshold).
        # With supersedes keyword in the new content, the +0.15 boost should
        # NOT push a totally unrelated string above 0.7 (because the boost
        # is on a real similarity, not a free pass). But for a moderately
        # similar string sitting at ~0.6, the boost should push it over.
        section = "- 协议门禁：前端只展示不阻塞\n"
        # Pick text that has solid overlap so unboosted ratio is around 0.6:
        new_text = "协议门禁：前端展示不阻塞 重新校正"
        matches = cap.similarity_check(new_text, section)
        # Should be picked up — either base ratio passes, or boost pushes it.
        assert len(matches) == 1
        assert matches[0]["supersedes_signal_detected"] is True

    def test_placeholder_entries_ignored(self):
        section = "- 待补充\n- 待刷新\n- FE-3 分工：徐帅武"
        matches = cap.similarity_check("FE-3 分工：徐帅武", section)
        # Only the third entry counts; the two placeholders are skipped.
        # entry_idx is from the surviving entries' real ordinal.
        assert len(matches) == 1
        assert matches[0]["match_first_line"].startswith("FE-3 分工")

    def test_auto_block_content_ignored(self):
        from dev_memory_common import AUTO_START, AUTO_END
        section = (
            "- 真实条目：协议门禁方案\n\n"
            f"{AUTO_START}\n"
            "- 自动生成的 focus area: apps/foo/bar\n"
            f"{AUTO_END}\n"
        )
        # New content that matches the auto-block bullet must NOT trigger a
        # match — auto blocks are managed by sync-working-tree.
        matches = cap.similarity_check("自动生成的 focus area: apps/foo/bar", section)
        for m in matches:
            assert "focus area" not in m["match_first_line"]


# ---------------------------------------------------------------------------
# _check_dedup_for_kind tests (file-aware wrapper)
# ---------------------------------------------------------------------------

class TestCheckDedupForKind:
    def test_upsert_kind_skipped(self, seed_branch_files):
        branch = seed_branch_files({
            "progress": (
                "# 当前进展\n\n"
                "## 当前进展\n\n"
                "- 已完成 FE-3.1 协议组件\n"
            ),
        })
        # Even a perfect match in progress.md should not block — progress is
        # upsert mode.
        hint = cap._check_dedup_for_kind(
            branch["paths"], "progress", "已完成 FE-3.1 协议组件",
        )
        assert hint is None

    def test_append_kind_blocks_on_match(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- FE-3 分工：徐帅武只做工作台\n"
            ),
        })
        hint = cap._check_dedup_for_kind(
            branch["paths"], "decision", "FE-3 分工：徐帅武只做工作台",
        )
        assert hint is not None
        assert hint["blocked"] is True
        assert hint["kind"] == "decision"
        assert hint["target_file"] == "branch/decisions.md"
        assert hint["recommendation"] == "update_existing"  # single match @ 1.0
        assert len(hint["matches"]) == 1
        assert hint["matches"][0]["id"].startswith("decisions::")

    def test_force_bypasses(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- FE-3 分工：徐帅武只做工作台\n"
            ),
        })
        hint = cap._check_dedup_for_kind(
            branch["paths"], "decision", "FE-3 分工：徐帅武只做工作台",
            force=True,
        )
        assert hint is None

    def test_recommendation_review_when_mid_similarity(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- FE-3 分工：徐帅武只做工作台\n"
            ),
        })
        # Mid-similarity, no supersedes signal → review_and_decide
        hint = cap._check_dedup_for_kind(
            branch["paths"], "decision", "FE-3 分工：徐帅武只做管理员",
        )
        assert hint is not None
        if hint["matches"][0]["similarity"] < 0.9:
            assert hint["recommendation"] == "review_and_decide"

    def test_recommendation_update_when_supersedes(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- FE-3 分工：徐帅武只做工作台和管理员页面\n"
            ),
        })
        hint = cap._check_dedup_for_kind(
            branch["paths"], "decision",
            "FE-3 分工：徐帅武只做工作台 重新校正",
        )
        assert hint is not None
        assert hint["recommendation"] == "update_existing"


# ---------------------------------------------------------------------------
# command_record end-to-end behavior
# ---------------------------------------------------------------------------

class TestCommandRecordDedupIntegration:
    def test_first_write_succeeds(self, branch_dir):
        code, out = _run_record(
            branch_dir, kind="decision",
            content="FE-3 工作台门禁方案：协议组件 + server 标志位",
        )
        assert code == 0
        assert out["mode"] == "explicit-kind"
        # File should now contain the new entry.
        text = branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
        assert "FE-3 工作台门禁方案" in text

    def test_duplicate_write_blocked(self, branch_dir):
        code, _ = _run_record(
            branch_dir, kind="decision",
            content="FE-3 工作台门禁方案：协议组件 + server 标志位",
        )
        assert code == 0
        code2, out2 = _run_record(
            branch_dir, kind="decision",
            content="FE-3 工作台门禁方案：协议组件 + server 标志位",
        )
        assert code2 == 2
        assert out2["blocked"] is True
        assert out2["dedup_hint"]["recommendation"] == "update_existing"
        # File should NOT have a second copy.
        text = branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
        # Count occurrences of the lead line.
        assert text.count("FE-3 工作台门禁方案") == 1

    def test_force_writes_through(self, branch_dir):
        _run_record(
            branch_dir, kind="decision",
            content="FE-3 工作台门禁方案：协议组件 + server 标志位",
        )
        code, out = _run_record(
            branch_dir, kind="decision",
            content="FE-3 工作台门禁方案：协议组件 + server 标志位",
            force=True,
        )
        assert code == 0
        assert "blocked" not in out
        text = branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
        assert text.count("FE-3 工作台门禁方案") == 2

    def test_unrelated_write_not_blocked(self, branch_dir):
        _run_record(
            branch_dir, kind="decision",
            content="前端协议门禁方案：协议组件 + server 标志位",
        )
        code, out = _run_record(
            branch_dir, kind="decision",
            content="后端 API 调整：服务端增加签约状态字段",
        )
        assert code == 0
        assert "blocked" not in out

    def test_upsert_kind_never_blocked(self, branch_dir):
        # First write to overview (upsert).
        _run_record(
            branch_dir, kind="overview",
            content="- FE-3 工作台门禁方案：协议组件 + server 标志位",
        )
        # Same content — would block under append mode, must pass under upsert.
        code, out = _run_record(
            branch_dir, kind="overview",
            content="- FE-3 工作台门禁方案：协议组件 + server 标志位",
        )
        assert code == 0
        assert "blocked" not in out

    def test_summary_json_batch_blocks_duplicates(self, branch_dir):
        # Seed a decision via single-mode first.
        _run_record(
            branch_dir, kind="decision",
            content="前端分工：徐帅武负责工作台",
        )
        payload = {
            "title": "checkpoint",
            "decisions": [
                {"decision": "前端分工：徐帅武负责工作台", "reason": "需求会决定"},
            ],
        }
        code, out = _run_record(
            branch_dir, summary_json=json.dumps(payload, ensure_ascii=False),
        )
        # At least one item blocked → exit 2.
        assert code == 2
        assert "dedup_blocked" in out
        blocked_kinds = {entry["kind"] for entry in out["dedup_blocked"]}
        assert "decision" in blocked_kinds

    def test_summary_json_accepts_worker_schema(self, branch_dir):
        payload = {
            "title": "worker checkpoint",
            "decisions": [
                {"summary": "管理员昵称读取 IpContext", "reason": "AdminContext 未签协议时为空", "impact": "admin workbench"},
            ],
            "glossary": ["IpContext.userProfileResp.nick_name 是当前用户昵称来源"],
            "shared_decisions": [
                {"summary": "工作台当前态优先覆盖记忆", "reason": "避免历史状态无限追加", "impact": "session summary"},
            ],
            "shared_context": ["自动总结前先读取现有记忆"],
            "shared_sources": ["docs/dev-memory-skill-suite-guide.md"],
        }
        code, out = _run_record(
            branch_dir, summary_json=json.dumps(payload, ensure_ascii=False),
        )

        assert code == 0
        touched = {(item["file"], item["section"]) for item in out["touched_targets"]}
        assert ("branch/decisions.md", "关键决策与原因") in touched
        assert ("branch/glossary.md", "当前有效上下文") in touched
        assert ("repo/decisions.md", "跨分支通用决策") in touched
        assert ("repo/glossary.md", "长期有效背景") in touched
        assert ("repo/glossary.md", "共享入口") in touched

        decisions = branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
        assert "管理员昵称读取 IpContext" in decisions


# ---------------------------------------------------------------------------
# Edge-case regressions
# ---------------------------------------------------------------------------

class TestDedupEdgeCases:
    def test_empty_content_returns_none(self, branch_dir):
        # Direct check; empty input shouldn't blow up.
        hint = cap._check_dedup_for_kind(branch_dir["paths"], "decision", "")
        assert hint is None
        hint = cap._check_dedup_for_kind(branch_dir["paths"], "decision", "   \n  ")
        assert hint is None

    def test_unknown_kind_returns_none(self, branch_dir):
        hint = cap._check_dedup_for_kind(branch_dir["paths"], "nonsense-kind", "hi")
        assert hint is None

    def test_dedup_against_only_placeholder_doesnt_block(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- 待补充\n"
            ),
        })
        hint = cap._check_dedup_for_kind(
            branch["paths"], "decision", "FE-3 工作台决策",
        )
        assert hint is None
