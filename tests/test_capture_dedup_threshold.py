"""
Capture --dedup-threshold tests.

Covers the hidden `--dedup-threshold` flag on `capture record`: it overrides
similarity_check's default 0.7 threshold when set, leaves behavior unchanged
when omitted, and rejects out-of-range values with exit 1 + error JSON.

The flag is intended for debug / experiment scenarios; production callers
should not pass it. These tests pin the contract so future refactors don't
silently drift on:
  - default path (threshold=None) → identical to pre-flag behavior
  - lower threshold catches mid-similarity entries the default would miss
  - higher threshold lets near-duplicates through that the default would block
  - edge values 0.0 / >1.0 / negative are rejected with a clear error

Tests reuse the `seed_branch_files` fixture from conftest.py so the branch
storage layout matches the rest of the dedup test suite.
"""
import argparse
import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr

import pytest

import dev_memory_capture as cap


# ---------------------------------------------------------------------------
# Helpers — local mirror of test_capture_dedup's _record_args, with the new
# dedup_threshold attribute included so command_record can read it via
# getattr without falling through to the default.
# ---------------------------------------------------------------------------

def _record_args(branch_info, *, kind=None, content=None, summary_json=None,
                 title=None, force=False, auto=False, dedup_threshold=None):
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
        dedup_threshold=dedup_threshold,
    )


def _run_record(branch_info, **kwargs):
    """Invoke command_record with a captured stdout buffer.
    Returns (exit_code, parsed_stdout_json_or_None)."""
    args = _record_args(branch_info, **kwargs)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cap.command_record(args)
    out = buf.getvalue().strip()
    parsed = json.loads(out) if out else None
    return code, parsed


def _run_main(branch_info, *extra):
    """Run main() with sys.argv replaced. Returns (exit_code, stdout, stderr).
    Used to verify argparse-level acceptance of --dedup-threshold (e.g. the
    SUPPRESS flag is still parseable even though --help hides it)."""
    argv = [
        "dev-memory-capture", "record",
        "--repo", str(branch_info["repo_root"]),
        "--context-dir", str(branch_info["storage_root"]),
        "--branch", branch_info["branch_name"],
        *extra,
    ]
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    saved = sys.argv
    sys.argv = argv
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            code = cap.main()
    finally:
        sys.argv = saved
    return code, out_buf.getvalue(), err_buf.getvalue()


# ---------------------------------------------------------------------------
# Default path: omitting --dedup-threshold preserves existing behavior.
# ---------------------------------------------------------------------------

class TestDedupThresholdDefaultBehavior:
    """If --dedup-threshold isn't passed, similarity_check's hard-coded 0.7
    must still govern. Mirrors test_capture_dedup's duplicate-blocked case so
    we explicitly pin "no threshold = old behavior"."""

    def test_default_threshold_blocks_exact_match(self, branch_dir):
        code, _ = _run_record(
            branch_dir, kind="decision",
            content="FE-3 工作台门禁方案：协议组件 + server 标志位",
        )
        assert code == 0
        code2, out2 = _run_record(
            branch_dir, kind="decision",
            content="FE-3 工作台门禁方案：协议组件 + server 标志位",
        )
        # Default path: still exit 2 with dedup_hint.
        assert code2 == 2
        assert out2["blocked"] is True
        assert out2["dedup_hint"]["recommendation"] == "update_existing"

    def test_default_threshold_lets_unrelated_through(self, branch_dir):
        _run_record(
            branch_dir, kind="decision",
            content="前端协议门禁方案：协议组件",
        )
        code, out = _run_record(
            branch_dir, kind="decision",
            content="后端 API 调整：签约状态字段",
        )
        assert code == 0
        assert "blocked" not in out


# ---------------------------------------------------------------------------
# Lower threshold catches what default would let through.
# ---------------------------------------------------------------------------

class TestDedupThresholdLow:
    """A mid-similarity pair (ratio ≈ 0.58) sits below the default 0.7 but
    above 0.5. Passing --dedup-threshold 0.5 should surface it as a match
    and block the write. The same pair under default threshold (covered in
    test_capture_dedup) is known to pass."""

    def test_threshold_0_5_blocks_mid_similarity(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- FE-3 工作台门禁方案\n"
            ),
        })
        # Sanity: at default 0.7 this pair should NOT match.
        no_match = cap._check_dedup_for_kind(
            branch["paths"], "decision", "FE-3 管理员页面方案",
        )
        assert no_match is None

        # With threshold=0.5 it should match (ratio ≈ 0.58).
        code, out = _run_record(
            branch, kind="decision",
            content="FE-3 管理员页面方案",
            dedup_threshold=0.5,
        )
        assert code == 2
        assert out["blocked"] is True
        assert out["dedup_hint"]["matches"][0]["similarity"] >= 0.5
        # And no second copy should have been written.
        text = branch["paths"]["decisions"].read_text(encoding="utf-8")
        assert text.count("FE-3 管理员页面方案") == 0


# ---------------------------------------------------------------------------
# Higher threshold lets things through that default would block.
# ---------------------------------------------------------------------------

class TestDedupThresholdHigh:
    """A high-but-not-exact pair (ratio ≈ 0.83) is above default 0.7 (so
    default would block) but below 0.9 (so the stricter threshold should
    let it pass). Threshold=0.9 here means "only flag near-identical"."""

    def test_threshold_0_9_lets_high_similarity_pass(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- FE-3 工作台门禁方案\n"
            ),
        })
        # Sanity: at default 0.7 this pair WOULD match and block.
        default_match = cap._check_dedup_for_kind(
            branch["paths"], "decision", "FE-3 工作台改造方案",
        )
        assert default_match is not None
        assert default_match["matches"][0]["similarity"] >= 0.7

        # With threshold=0.9 it should pass.
        code, out = _run_record(
            branch, kind="decision",
            content="FE-3 工作台改造方案",
            dedup_threshold=0.9,
        )
        assert code == 0
        assert "blocked" not in out
        text = branch["paths"]["decisions"].read_text(encoding="utf-8")
        assert "FE-3 工作台改造方案" in text


# ---------------------------------------------------------------------------
# Boundary value: threshold=1.0 → only exact matches block.
# ---------------------------------------------------------------------------

class TestDedupThresholdBoundary:
    """1.0 is the inclusive upper bound — meaningful as "only block if the
    new line is character-for-character identical to an existing one." Pick
    this as the canonical strict mode."""

    def test_threshold_1_0_blocks_only_exact(self, seed_branch_files):
        branch = seed_branch_files({
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- FE-3 工作台门禁方案\n"
            ),
        })
        # Near-identical (ratio ≈ 0.83) — must pass at threshold=1.0.
        code, _ = _run_record(
            branch, kind="decision",
            content="FE-3 工作台改造方案",
            dedup_threshold=1.0,
        )
        assert code == 0

        # Identical — should still block at threshold=1.0.
        code2, out2 = _run_record(
            branch, kind="decision",
            content="FE-3 工作台门禁方案",
            dedup_threshold=1.0,
        )
        assert code2 == 2
        assert out2["blocked"] is True


# ---------------------------------------------------------------------------
# Validation: out-of-range values raise → exit 1 + error JSON.
# ---------------------------------------------------------------------------

class TestDedupThresholdValidation:
    """Range is (0.0, 1.0]. 0.0 is excluded because difflib ratios are ≥ 0
    so any text would match — a debug knob that blocks everything has no
    legitimate use; better to fail fast. Values > 1.0 or < 0 are also
    rejected as obviously wrong."""

    def _run_and_capture_err(self, branch_dir, threshold):
        args = _record_args(
            branch_dir, kind="decision", content="anything",
            dedup_threshold=threshold,
        )
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        # main() catches the RuntimeError and emits the JSON to stderr;
        # invoke through main to mirror the real CLI path.
        saved_argv = sys.argv
        sys.argv = [
            "dev-memory-capture", "record",
            "--repo", str(branch_dir["repo_root"]),
            "--context-dir", str(branch_dir["storage_root"]),
            "--branch", branch_dir["branch_name"],
            "--kind", "decision",
            "--content", "anything",
            "--dedup-threshold", str(threshold),
        ]
        try:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                code = cap.main()
        finally:
            sys.argv = saved_argv
        return code, out_buf.getvalue(), err_buf.getvalue()

    def test_threshold_zero_rejected(self, branch_dir):
        code, _stdout, stderr = self._run_and_capture_err(branch_dir, 0.0)
        assert code == 1
        err = json.loads(stderr.strip())
        assert "dedup-threshold" in err["error"]

    def test_threshold_above_one_rejected(self, branch_dir):
        code, _stdout, stderr = self._run_and_capture_err(branch_dir, 1.5)
        assert code == 1
        err = json.loads(stderr.strip())
        assert "dedup-threshold" in err["error"]

    def test_threshold_negative_rejected(self, branch_dir):
        code, _stdout, stderr = self._run_and_capture_err(branch_dir, -0.1)
        assert code == 1
        err = json.loads(stderr.strip())
        assert "dedup-threshold" in err["error"]


# ---------------------------------------------------------------------------
# Argparse: --dedup-threshold is parseable (despite being hidden via SUPPRESS).
# ---------------------------------------------------------------------------

class TestDedupThresholdArgparse:
    """The flag uses argparse.SUPPRESS so --help omits it, but it must still
    parse. A successful write through main() with the flag set proves the
    parser accepts it end-to-end."""

    def test_main_accepts_flag(self, branch_dir):
        code, stdout, _stderr = _run_main(
            branch_dir,
            "--kind", "decision",
            "--content", "全新条目：用 main() 走一次",
            "--dedup-threshold", "0.5",
        )
        assert code == 0
        payload = json.loads(stdout.strip())
        assert payload["mode"] == "explicit-kind"
