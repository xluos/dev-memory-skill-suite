"""Tests for the annotated md mirror produced by `_render_annotated_md`
(end-to-end via `tidy prepare`). The mirror is the single artifact an
agent reads to recover entry id + block id + orphan paragraph markers
without reparsing the original markdown twice."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


LIB = Path(__file__).resolve().parent.parent / "lib"


def _run_prepare(repo, storage):
    """Run `dev_memory_tidy prepare` against a (repo, storage) pair and
    return the parsed JSON payload printed to stdout."""
    cmd = [
        sys.executable, str(LIB / "dev_memory_tidy.py"), "prepare",
        "--repo", str(repo), "--context-dir", str(storage),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"prepare failed: stderr={proc.stderr!r}"
    return json.loads(proc.stdout)


def test_annotated_md_has_entry_id_on_top_bullets(seed_branch_files, tmp_repo, tmp_storage_root):
    seed_branch_files({
        "decisions": "\n".join([
            "# decisions",
            "",
            "## 关键决策与原因",
            "",
            "- 决策一",
            "- 决策二",
            "",
        ]),
    })
    payload = _run_prepare(tmp_repo, tmp_storage_root)
    md = Path(payload["annotated_md"]).read_text(encoding="utf-8")

    # Both top-level bullets should carry id annotations.
    assert "决策一  <!-- id: decisions::0::0 -->" in md
    assert "决策二  <!-- id: decisions::0::1 -->" in md


def test_annotated_md_wraps_block_boundaries(seed_branch_files, tmp_repo, tmp_storage_root):
    seed_branch_files({
        "decisions": "\n".join([
            "# decisions",
            "",
            "## 关键决策与原因",
            "",
            "- 块一",
            "",
            "- 块二",
            "",
        ]),
    })
    payload = _run_prepare(tmp_repo, tmp_storage_root)
    md = Path(payload["annotated_md"]).read_text(encoding="utf-8")
    # Each block has a wrapping comment pair (start + /block).
    assert "<!-- block: decisions::0::block-0 -->" in md
    assert "<!-- block: decisions::0::block-1 -->" in md
    # /block count should match the number of `<!-- block: ` openings
    # (every opened block has a matching close).
    open_count = md.count("<!-- block: ")
    close_count = md.count("<!-- /block -->")
    assert open_count == close_count and open_count >= 2


def test_annotated_md_orphan_paragraph_tagged(seed_branch_files, tmp_repo, tmp_storage_root):
    seed_branch_files({
        "decisions": "\n".join([
            "# decisions",
            "",
            "## 关键决策与原因",
            "",
            "- 选 feature flag",
            "**Why:** 降低 blast radius",
            "**How to apply:** 在 handler 里读 flag",
            "",
        ]),
    })
    payload = _run_prepare(tmp_repo, tmp_storage_root)
    md = Path(payload["annotated_md"]).read_text(encoding="utf-8")
    # Both Why and How lines should carry the orphan marker.
    assert "**Why:** 降低 blast radius  <!-- orphan: paragraph -->" in md
    assert "**How to apply:** 在 handler 里读 flag  <!-- orphan: paragraph -->" in md


def test_annotated_md_h2_per_file(seed_branch_files, tmp_repo, tmp_storage_root):
    seed_branch_files({
        "decisions": "# decisions\n\n## 关键决策与原因\n\n- 决策\n",
        "risks": "# risks\n\n## 风险\n\n- 风险点\n",
    })
    payload = _run_prepare(tmp_repo, tmp_storage_root)
    md = Path(payload["annotated_md"]).read_text(encoding="utf-8")
    assert "## 📄 branch/decisions.md" in md
    assert "## 📄 branch/risks.md" in md


def test_annotated_md_skips_auto_generated_block(seed_branch_files, tmp_repo, tmp_storage_root):
    seed_branch_files({
        "overview": "\n".join([
            "# overview",
            "",
            "## 当前目标",
            "",
            "- 目标 A",
            "",
            "<!-- AUTO-GENERATED-START -->",
            "machine state nobody should see",
            "<!-- AUTO-GENERATED-END -->",
            "",
        ]),
    })
    payload = _run_prepare(tmp_repo, tmp_storage_root)
    md = Path(payload["annotated_md"]).read_text(encoding="utf-8")
    assert "machine state nobody should see" not in md
    assert "AUTO-GENERATED" not in md
    assert "目标 A" in md


def test_prepare_payload_contains_blocks_field(seed_branch_files, tmp_repo, tmp_storage_root):
    seed_branch_files({
        "decisions": "\n".join([
            "# decisions",
            "",
            "## 关键决策与原因",
            "",
            "- 决策一",
            "- 决策二",
            "",
        ]),
    })
    payload = _run_prepare(tmp_repo, tmp_storage_root)
    assert "blocks" in payload
    assert "annotated_md" in payload
    assert "annotated_md_open" in payload
    assert payload["block_count"] == len(payload["blocks"])
    # At minimum, the two seeded decisions become 2 blocks.
    decisions_blocks = [b for b in payload["blocks"] if b["file_key"] == "decisions"]
    assert len(decisions_blocks) >= 2
    for b in decisions_blocks:
        assert b["id"].startswith("decisions::")
        assert "block-" in b["id"]


def test_placeholder_entries_marked(seed_branch_files, tmp_repo, tmp_storage_root):
    seed_branch_files({
        "decisions": "# decisions\n\n## 关键决策与原因\n\n- 待补充\n",
    })
    payload = _run_prepare(tmp_repo, tmp_storage_root)
    md = Path(payload["annotated_md"]).read_text(encoding="utf-8")
    assert "<!-- placeholder -->" in md
