import json
import subprocess
import sys
from pathlib import Path


LIB = Path(__file__).resolve().parent.parent / "lib"
SCRIPT = LIB / "dev_memory_read.py"


def _run_read(*args):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_read_show_reports_exact_current_branch_paths(branch_dir):
    payload = _run_read(
        "show",
        "--repo",
        str(branch_dir["repo_root"]),
        "--context-dir",
        str(branch_dir["storage_root"]),
    )

    assert payload["branch"] == "test-branch"
    assert payload["branch_key"] == "test-branch"
    assert payload["branch_exists"] is True
    assert payload["branch_dir"] == str(branch_dir["branch_dir"])
    assert payload["branch_files"]["progress"]["exists"] is True
    assert payload["recommended_read_order"][0]["key"] == "progress"


def test_read_search_current_scope_searches_branch_and_repo(seed_branch_files, branch_dir):
    seed_branch_files(
        {
            "decisions": "# 分支决策\n\n## 关键决策与原因\n\n- TODO: 作者信息组件展示头像，确认 showAvatar 参数。\n",
            "progress": "# 当前进展\n\n## 当前进展\n\n- 列表实现已更新。\n",
        }
    )
    branch_dir["paths"]["repo_glossary"].write_text(
        "# 仓库共享术语与入口\n\n## 长期有效背景\n\n- 作者信息列复用 AuthorInfo。\n",
        encoding="utf-8",
    )

    payload = _run_read(
        "search",
        "--repo",
        str(branch_dir["repo_root"]),
        "--context-dir",
        str(branch_dir["storage_root"]),
        "--query",
        "作者信息",
    )

    paths = {hit["path"] for hit in payload["hits"]}
    assert str(branch_dir["paths"]["decisions"]) in paths
    assert str(branch_dir["paths"]["repo_glossary"]) in paths
    assert payload["scope"] == "current"
    assert payload["hit_count"] == 2


def test_read_search_all_branches_stays_inside_resolved_repo_memory(branch_dir):
    repo_dir = branch_dir["branch_dir"].parents[1]
    other = repo_dir / "branches" / "feature__wecom"
    other.mkdir(parents=True)
    (other / "progress.md").write_text(
        "# 当前进展\n\n## 当前进展\n\n- 企微建联 TODO 已更新。\n",
        encoding="utf-8",
    )
    outside = branch_dir["storage_root"] / "other-repo" / "branches" / "feature__wecom"
    outside.mkdir(parents=True)
    (outside / "progress.md").write_text("- 企微建联 不应该被搜到。\n", encoding="utf-8")

    payload = _run_read(
        "search",
        "--repo",
        str(branch_dir["repo_root"]),
        "--context-dir",
        str(branch_dir["storage_root"]),
        "--scope",
        "all-branches",
        "--query",
        "企微建联",
    )

    assert payload["hit_count"] == 1
    assert payload["hits"][0]["path"] == str(other / "progress.md")


def test_read_show_does_not_lazy_init_missing_branch(branch_dir):
    payload = _run_read(
        "show",
        "--repo",
        str(branch_dir["repo_root"]),
        "--context-dir",
        str(branch_dir["storage_root"]),
        "--branch",
        "feature/missing",
    )

    missing_dir = branch_dir["branch_dir"].parents[0] / "feature__missing"
    assert payload["branch"] == "feature/missing"
    assert payload["branch_exists"] is False
    assert not missing_dir.exists()


def test_read_show_refuses_to_create_no_git_id(tmp_path):
    project = tmp_path / "plain-project"
    project.mkdir()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "show", "--repo", str(project)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "refuses to create a .dev-memory-id" in result.stderr
    assert not (project / ".dev-memory-id").exists()
