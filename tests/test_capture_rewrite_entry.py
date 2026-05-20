"""
rewrite-entry tests.

Verify that capture's rewrite-entry subcommand replaces an entry by id
without disturbing sibling entries, handles multi-line replacements, and
emits structured error JSON for malformed / missing references.
"""
import argparse
import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

import dev_memory_capture as cap


def _rewrite_args(branch_info, *, entry_id, content):
    return argparse.Namespace(
        repo=str(branch_info["repo_root"]),
        context_dir=str(branch_info["storage_root"]),
        branch=branch_info["branch_name"],
        id=entry_id,
        content=content,
        content_file=None,
    )


def _run_rewrite(branch_info, **kwargs):
    args = _rewrite_args(branch_info, **kwargs)
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        code = cap.command_rewrite_entry(args)
    return code, out_buf.getvalue(), err_buf.getvalue()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestRewriteEntryHappyPath:
    def test_simple_replace_keeps_siblings(self, seed_branch_files):
        # Layout: H1 + (section 0 = 分支) + (section 1 = 关键决策与原因)
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 分支\n\n"
                "- test-branch\n\n"
                "## 关键决策与原因\n\n"
                "- 旧决策 A：用方案 X\n"
                "- 旧决策 B：用方案 Y\n"
                "- 旧决策 C：用方案 Z\n"
            ),
        })
        code, out, err = _run_rewrite(
            branch,
            entry_id="decisions::1::1",
            content="新决策 B：改用方案 Y'（5/15 校正）",
        )
        assert code == 0, err
        payload = json.loads(out)
        assert payload["mode"] == "rewrite-entry"
        assert payload["id"] == "decisions::1::1"
        assert "旧决策 B" in payload["previous_first_line"]
        assert "新决策 B" in payload["new_first_line"]

        text = branch["paths"]["decisions"].read_text(encoding="utf-8")
        assert "旧决策 A" in text  # sibling kept
        assert "旧决策 C" in text  # sibling kept
        assert "旧决策 B" not in text  # original replaced
        assert "新决策 B" in text

    def test_multi_line_replacement(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 分支\n\n"
                "- test-branch\n\n"
                "## 关键决策与原因\n\n"
                "- 单行旧条目\n"
            ),
        })
        new_text = "新条目首行\n附属说明 line 2\n附属说明 line 3"
        code, out, err = _run_rewrite(
            branch,
            entry_id="decisions::1::0",
            content=new_text,
        )
        assert code == 0, err
        text = branch["paths"]["decisions"].read_text(encoding="utf-8")
        assert "- 新条目首行" in text
        assert "  附属说明 line 2" in text
        assert "  附属说明 line 3" in text
        assert "单行旧条目" not in text

    def test_rewrite_in_section_with_auto_block(self, seed_branch_files):
        # progress.md has an auto-block section; rewrite-entry in a non-auto
        # section must leave the auto-block intact.
        from dev_memory_common import AUTO_START, AUTO_END
        branch = seed_branch_files({
            "progress": (
                "# 当前进展\n\n"
                "## 当前进展\n\n"
                "- 旧进展条目 1\n"
                "- 旧进展条目 2\n\n"
                "## 自动同步区\n\n"
                f"{AUTO_START}\n"
                "auto block payload here\n"
                f"{AUTO_END}\n"
            ),
        })
        # section idx 0 == "当前进展" (no H1 section), entry 1
        code, out, err = _run_rewrite(
            branch,
            entry_id="progress::0::1",
            content="改后的进展条目 2",
        )
        assert code == 0, err
        text = branch["paths"]["progress"].read_text(encoding="utf-8")
        assert "旧进展条目 1" in text
        assert "改后的进展条目 2" in text
        assert "旧进展条目 2" not in text
        # auto-block survives intact
        assert AUTO_START in text and AUTO_END in text
        assert "auto block payload here" in text


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestRewriteEntryErrors:
    def test_malformed_id(self, branch_dir):
        code, out, err = _run_rewrite(
            branch_dir,
            entry_id="not-a-valid-id",
            content="something",
        )
        assert code == 1
        err_payload = json.loads(err)
        assert "malformed" in err_payload["error"]

    def test_unknown_file_key(self, branch_dir):
        code, out, err = _run_rewrite(
            branch_dir,
            entry_id="nonexistent_key::0::0",
            content="something",
        )
        assert code == 1
        err_payload = json.loads(err)
        assert "unknown file_key" in err_payload["error"]

    def test_section_out_of_range(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- 一条\n"
            ),
        })
        code, out, err = _run_rewrite(
            branch,
            entry_id="decisions::99::0",
            content="x",
        )
        assert code == 1
        err_payload = json.loads(err)
        assert "section_idx" in err_payload["error"]

    def test_entry_out_of_range(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- 一条\n"
            ),
        })
        code, out, err = _run_rewrite(
            branch,
            entry_id="decisions::0::99",
            content="x",
        )
        assert code == 1
        err_payload = json.loads(err)
        assert "entry_idx" in err_payload["error"]

    def test_missing_content(self, branch_dir):
        # Use a valid file_key so the file existence check passes; the
        # content guard fires before we touch the file.
        code, out, err = _run_rewrite(
            branch_dir,
            entry_id="decisions::0::0",
            content=None,
        )
        assert code == 1
        err_payload = json.loads(err)
        assert "--content" in err_payload["error"]
