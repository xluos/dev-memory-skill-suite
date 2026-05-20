"""Smoke test: verifies pytest config + conftest fixtures wire up correctly.
Phase A/B subagents will add domain tests on top of this baseline."""
from pathlib import Path


def test_lib_importable():
    """lib/ is on sys.path so subagent tests can import freely."""
    import dev_memory_common  # noqa: F401
    import dev_memory_tidy  # noqa: F401
    import dev_memory_capture  # noqa: F401


def test_branch_dir_lazy_init(branch_dir):
    """ensure_branch_paths_exist runs under the temp storage root and
    produces the expected branch directory structure."""
    bd = branch_dir["branch_dir"]
    assert isinstance(bd, Path)
    assert bd.exists()
    paths = branch_dir["paths"]
    for key in ("overview", "decisions", "progress", "risks", "glossary"):
        assert key in paths, f"missing path key {key!r}"
        assert paths[key].exists(), f"{key} not lazy-init"


def test_seed_branch_files(seed_branch_files):
    """The factory writes raw content, overriding the lazy-init template."""
    branch = seed_branch_files({
        "decisions": "# decisions\n\n## 关键决策与原因\n\n- 测试决策\n",
    })
    text = branch["paths"]["decisions"].read_text(encoding="utf-8")
    assert "测试决策" in text
