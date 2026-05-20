"""
Shared pytest fixtures for dev-memory-skill-suite.

Test strategy:
  - Each test gets its own temp storage root via `tmp_storage_root` fixture.
  - The `branch_dir` fixture lazy-inits a branch dir under that storage root,
    using ensure_branch_paths_exist so tests exercise the real lazy-init path.
  - The `seed_branch_files` factory writes canned markdown content into a
    branch's files so tests can simulate "already-populated branch" cases.
"""
import sys
from pathlib import Path

# Make lib/ importable for all tests.
LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

from dev_memory_common import ensure_branch_paths_exist


@pytest.fixture
def tmp_storage_root(tmp_path):
    """Per-test ~/.dev-memory replacement. Returns the Path."""
    root = tmp_path / "dev-memory-storage"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def tmp_repo(tmp_path):
    """A minimal git-initialised directory standing in as the 'repo'.
    ensure_branch_paths_exist needs to read HEAD / branch name, so the
    fixture initialises git and lands an initial commit on `test-branch`."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    def run(*args):
        subprocess.run(
            ["git", *args], cwd=repo, check=True,
            capture_output=True, text=True,
        )
    run("init", "-q", "-b", "test-branch")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "test")
    run("config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def branch_dir(tmp_repo, tmp_storage_root):
    """Lazy-init a branch under the temp storage root and return
    (paths, branch_dir_path, branch_name). Branch name is fixed to keep tests
    deterministic; override with `lazy_branch` factory if needed."""
    branch_name = "test-branch"
    (
        repo_root, branch_name_out, branch_key, storage_root,
        repo_key, repo_dir, branch_dir_path, paths,
    ) = ensure_branch_paths_exist(
        str(tmp_repo), str(tmp_storage_root), branch_name,
    )
    return {
        "paths": paths,
        "branch_dir": branch_dir_path,
        "branch_name": branch_name_out,
        "repo_root": repo_root,
        "storage_root": storage_root,
    }


@pytest.fixture
def seed_branch_files(branch_dir):
    """Factory: seed_branch_files({'decisions': '...', 'risks': '...'}) writes
    raw markdown content into the named files under the branch dir, replacing
    the lazy-init template. Use for tests that need specific structure."""
    def _seed(content_by_file_key):
        for file_key, content in content_by_file_key.items():
            path = branch_dir["paths"].get(file_key)
            assert path is not None, f"unknown file_key {file_key!r}"
            path.write_text(content, encoding="utf-8")
        return branch_dir
    return _seed
