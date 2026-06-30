import sys
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

import _common as hook_common  # noqa: E402
from _common import (  # noqa: E402
    _build_context_from_assets,
    brief_profile_for_repo_count,
    compact_recent_body,
)


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


def _workspace_brief_assets(branch_dir, seed_branch_files):
    seed_branch_files(
        {
            "progress": (
                "# 自动索引\n\n"
                "## 功能文件索引\n\n"
                "- old file map\n\n"
                "- newest file map\n\n"
                "## 建议优先查看的目录\n\n"
                "- src/features\n"
            ),
            "overview": (
                "# 分支概览\n\n"
                "## 当前目标\n\n"
                "- old noisy goal\n"
            ),
            "glossary": (
                "# 术语与源资料\n\n"
                "## 当前有效上下文\n\n"
                "- old noisy branch context\n"
            ),
            "repo_decisions": (
                "# 跨分支通用决策\n\n"
                "## 跨分支通用决策\n\n"
                "- shared decision\n"
            ),
            "repo_glossary": (
                "# 仓库共享术语与入口\n\n"
                "## 长期有效背景\n\n"
                "- long lived context\n"
            ),
        }
    )
    return {
        "branch_dir": branch_dir["branch_dir"],
        "branch_name": branch_dir["branch_name"],
        "paths": branch_dir["paths"],
    }


def test_workspace_brief_expanded_uses_xml_priority_blocks(branch_dir, seed_branch_files):
    assets = _workspace_brief_assets(branch_dir, seed_branch_files)

    context = _build_context_from_assets(
        assets,
        full=False,
        heading="## `repo-a` @ branch `test-branch`",
        brief_profile="expanded",
    )

    assert "<file_map>" in context
    assert "<read_first_dirs>" in context
    assert "<shared_decisions>" in context
    assert "<long_term_context>" in context
    assert "old noisy goal" not in context
    assert "old noisy branch context" not in context


def test_workspace_brief_standard_keeps_recent_priority_entries(branch_dir, seed_branch_files):
    assets = _workspace_brief_assets(branch_dir, seed_branch_files)
    assets["paths"]["repo_decisions"].write_text(
        "# 跨分支通用决策\n\n"
        "## 跨分支通用决策\n\n"
        + "\n\n".join(f"- shared decision {idx}" for idx in range(12))
        + "\n",
        encoding="utf-8",
    )

    context = _build_context_from_assets(
        assets,
        full=False,
        heading="## `repo-a` @ branch `test-branch`",
        brief_profile="standard",
    )

    assert "<shared_decisions>" in context
    assert "shared decision 11" in context
    assert "shared decision 0" not in context
    assert "↪ 完整: decisions.md" in context


def test_workspace_brief_minimal_only_keeps_map_and_read_dirs(branch_dir, seed_branch_files):
    assets = _workspace_brief_assets(branch_dir, seed_branch_files)
    assets["paths"]["progress"].write_text(
        "# 自动索引\n\n"
        "## 功能文件索引\n\n"
        + "\n\n".join(f"- file map {idx}" for idx in range(8))
        + "\n\n## 建议优先查看的目录\n\n"
        "- src/features\n",
        encoding="utf-8",
    )

    context = _build_context_from_assets(
        assets,
        full=False,
        heading="## `repo-a` @ branch `test-branch`",
        brief_profile="minimal",
    )

    assert "<file_map>" in context
    assert "<read_first_dirs>" in context
    assert "<shared_decisions>" not in context
    assert "<long_term_context>" not in context
    assert "file map 7" in context
    assert "file map 0" not in context


def test_workspace_brief_profile_depends_on_repo_count():
    assert brief_profile_for_repo_count(1) == "expanded"
    assert brief_profile_for_repo_count(2) == "expanded"
    assert brief_profile_for_repo_count(3) == "standard"
    assert brief_profile_for_repo_count(5) == "standard"
    assert brief_profile_for_repo_count(6) == "minimal"


@pytest.fixture
def workspace_primary_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DEV_MEMORY_PRIMARY_REPO", raising=False)
    monkeypatch.delenv("DEV_ASSETS_PRIMARY_REPO", raising=False)
    monkeypatch.setattr(hook_common, "REPO_ROOT", tmp_path)
    return tmp_path


def test_primary_repo_name_reads_workspace_local_config(workspace_primary_env):
    (workspace_primary_env / ".dev-memory-workspace.json").write_text(
        '{"primary_repo":"repo-a"}\n',
        encoding="utf-8",
    )

    assert hook_common.primary_repo_name() == "repo-a"


def test_primary_repo_name_env_overrides_workspace_local_config(monkeypatch, workspace_primary_env):
    (workspace_primary_env / ".dev-memory-workspace.json").write_text(
        '{"primary_repo":"repo-a"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DEV_MEMORY_PRIMARY_REPO", "repo-b")

    assert hook_common.primary_repo_name() == "repo-b"
