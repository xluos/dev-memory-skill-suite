"""capture correction-target lookup tests.

The list-entries command is the primary correction workflow: read the target
section and choose the existing entry id from real content. find-candidates
remains as a fuzzy helper, broader than append-time dedup.
"""
import argparse
import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

import dev_memory_capture as cap


def _list_args(branch_info, *, kind, limit=80, tail=False):
    return argparse.Namespace(
        repo=str(branch_info["repo_root"]),
        context_dir=str(branch_info["storage_root"]),
        branch=branch_info["branch_name"],
        kind=kind,
        limit=limit,
        tail=tail,
    )


def _run_list(branch_info, **kwargs):
    args = _list_args(branch_info, **kwargs)
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        code = cap.command_list_entries(args)
    out = out_buf.getvalue().strip()
    parsed = json.loads(out) if out else None
    return code, parsed, err_buf.getvalue()


def _find_args(branch_info, *, query, kind=None, limit=8, min_score=0.2):
    return argparse.Namespace(
        repo=str(branch_info["repo_root"]),
        context_dir=str(branch_info["storage_root"]),
        branch=branch_info["branch_name"],
        query=query,
        query_file=None,
        kind=kind,
        limit=limit,
        min_score=min_score,
    )


def _run_find(branch_info, **kwargs):
    args = _find_args(branch_info, **kwargs)
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        code = cap.command_find_candidates(args)
    out = out_buf.getvalue().strip()
    parsed = json.loads(out) if out else None
    return code, parsed, err_buf.getvalue()


def test_list_entries_reads_kind_section_without_fuzzy_matching(seed_branch_files):
    branch = seed_branch_files({
        "decisions": (
            "# 分支决策\n\n"
            "## 关键决策与原因\n\n"
            "- 旧决策 A：capture 纠错先追加修正\n"
            "- 旧决策 B：保留 rewrite-entry 能力\n"
        ),
    })

    code, out, err = _run_list(branch, kind="decision")

    assert code == 0, err
    assert out["mode"] == "list-entries"
    assert out["target_file"] == "branch/decisions.md"
    assert out["section"] == "关键决策与原因"
    assert out["total_entries"] == 2
    assert [entry["first_line"] for entry in out["entries"]] == [
        "旧决策 A：capture 纠错先追加修正",
        "旧决策 B：保留 rewrite-entry 能力",
    ]
    assert out["entries"][0]["id"] == "decisions::0::0"


def test_list_entries_limit_and_tail(seed_branch_files):
    branch = seed_branch_files({
        "risks": (
            "# 风险\n\n"
            "## 阻塞与注意点\n\n"
            "- 风险 A\n"
            "- 风险 B\n"
            "- 风险 C\n"
        ),
    })

    code, out, err = _run_list(branch, kind="risk", limit=2, tail=True)

    assert code == 0, err
    assert out["total_entries"] == 3
    assert out["returned_entries"] == 2
    assert out["truncated"] is True
    assert [entry["first_line"] for entry in out["entries"]] == ["风险 B", "风险 C"]


def test_find_candidates_surfaces_below_dedup_threshold_match(seed_branch_files):
    branch = seed_branch_files({
        "decisions": (
            "# 分支决策\n\n"
            "## 关键决策与原因\n\n"
            "- capture 纠错策略：用户修正旧记忆时先追加一条修正说明\n"
        ),
    })
    assert cap._check_dedup_for_kind(
        branch["paths"],
        "decision",
        "纠正 capture 的旧记忆处理方式",
    ) is None

    code, out, err = _run_find(
        branch,
        kind="decision",
        query="capture 纠错旧记忆",
        min_score=0.2,
    )

    assert code == 0, err
    assert out["mode"] == "find-candidates"
    assert out["candidates"]
    top = out["candidates"][0]
    assert top["id"].startswith("decisions::")
    assert top["file"] == "branch/decisions.md"
    assert "先追加一条修正说明" in top["match_first_line"]


def test_find_candidates_respects_kind_filter(seed_branch_files):
    branch = seed_branch_files({
        "decisions": (
            "# 分支决策\n\n"
            "## 关键决策与原因\n\n"
            "- capture 纠错策略：旧决策条目\n"
        ),
        "risks": (
            "# 风险\n\n"
            "## 阻塞与注意点\n\n"
            "- capture 纠错策略：旧风险条目\n"
        ),
    })

    code, out, err = _run_find(
        branch,
        kind="risk",
        query="capture 纠错策略",
    )

    assert code == 0, err
    assert out["candidates"]
    assert {item["file"] for item in out["candidates"]} == {"branch/risks.md"}


def test_find_candidates_rejects_bad_limit(branch_dir):
    with pytest.raises(RuntimeError, match="--limit"):
        _run_find(branch_dir, query="anything", limit=0)


def test_find_candidates_rejects_non_append_kind(branch_dir):
    with pytest.raises(RuntimeError, match="not append-style"):
        _run_find(branch_dir, query="anything", kind="overview")


def test_list_entries_rejects_bad_limit(branch_dir):
    with pytest.raises(RuntimeError, match="--limit"):
        _run_list(branch_dir, kind="decision", limit=0)
