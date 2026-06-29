import sys
from pathlib import Path


HOOKS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from _common import _build_context_from_assets, compact_recent_body  # noqa: E402


def test_compact_recent_body_prefers_newest_blank_line_entries():
    body = "- old decision\n\n- middle decision\n\n- newest decision"

    compacted, truncated = compact_recent_body(body, max_lines=2, max_chars=200)

    assert compacted.startswith("- newest decision")
    assert "- middle decision" in compacted
    assert "- old decision" not in compacted
    assert truncated is True


def test_compact_recent_body_prefers_newest_contiguous_bullets():
    body = "- old risk\n- middle risk\n- newest risk"

    compacted, truncated = compact_recent_body(body, max_lines=2, max_chars=200)

    assert compacted.startswith("- newest risk")
    assert "- middle risk" in compacted
    assert "- old risk" not in compacted
    assert truncated is True


def test_session_start_frontloads_high_priority_sections(branch_dir, seed_branch_files):
    seed_branch_files(
        {
            "progress": (
                "# 自动索引\n\n"
                "## 功能文件索引\n\n"
                "- 入口: src/entry.ts\n\n"
                "## 建议优先查看的目录\n\n"
                "- src/features\n"
            ),
            "glossary": (
                "# 术语与源资料\n\n"
                "## 分支源资料入口\n\n"
                "- PRD\n\n"
                "## 当前有效上下文\n\n"
                "- noisy branch context\n"
            ),
            "overview": (
                "# 分支概览\n\n"
                "## 当前目标\n\n"
                "- goal\n"
            ),
            "risks": (
                "# 阻塞与注意点\n\n"
                "## 阻塞与注意点\n\n"
                "- risk\n"
            ),
            "decisions": (
                "# 分支决策\n\n"
                "## 关键决策与原因\n\n"
                "- branch decision\n"
            ),
            "repo_decisions": (
                "# 跨分支通用决策\n\n"
                "## 跨分支通用决策\n\n"
                "- shared decision\n"
            ),
            "repo_glossary": (
                "# 仓库共享术语与入口\n\n"
                "## 长期有效背景\n\n"
                "- long-lived context\n"
            ),
        }
    )
    assets = {
        "branch_dir": branch_dir["branch_dir"],
        "branch_name": branch_dir["branch_name"],
        "paths": branch_dir["paths"],
    }

    context = _build_context_from_assets(assets, full=True)

    high_priority_markers = [
        "<file_map>",
        "</file_map>",
        "<read_first_dirs>",
        "</read_first_dirs>",
        "<shared_decisions>",
        "</shared_decisions>",
        "<long_term_context>",
        "</long_term_context>",
    ]
    high_priority_indexes = [context.index(marker) for marker in high_priority_markers]

    assert high_priority_indexes == sorted(high_priority_indexes)
    assert high_priority_indexes[-1] < context.index("当前有效上下文:")
