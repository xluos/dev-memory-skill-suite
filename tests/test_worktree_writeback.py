import json

import dev_memory_capture as cap
from dev_memory_common import ensure_branch_paths_exist, read_json, write_json

from test_capture_dedup import _run_record


def _init_source_branch(tmp_repo, tmp_storage_root, branch_name="source-branch"):
    (
        _repo_root,
        _branch_name,
        _branch_key,
        _storage_root,
        _repo_key,
        _repo_dir,
        branch_dir,
        paths,
    ) = ensure_branch_paths_exist(str(tmp_repo), str(tmp_storage_root), branch_name)
    return {"branch_name": branch_name, "branch_dir": branch_dir, "paths": paths}


def _mark_worktree_inherited(branch_info, source_branch):
    manifest = read_json(branch_info["paths"]["manifest"])
    manifest["provenance"] = [{"op": "worktree-inherit", "from": source_branch}]
    write_json(branch_info["paths"]["manifest"], manifest)


def test_worktree_writeback_mirrors_append_kind(branch_dir, tmp_repo, tmp_storage_root, monkeypatch):
    source = _init_source_branch(tmp_repo, tmp_storage_root)
    _mark_worktree_inherited(branch_dir, source["branch_name"])
    monkeypatch.setenv("DEV_MEMORY_WORKTREE_WRITEBACK", "1")

    code, payload = _run_record(
        branch_dir,
        kind="decision",
        content="worktree decision should flow back",
    )

    assert code == 0
    assert "worktree_writeback" in payload
    assert payload["worktree_writeback"]["source"] == source["branch_name"]
    assert payload["worktree_writeback"]["touched"][0]["file"] == "branch/decisions.md"
    assert "worktree decision should flow back" in branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
    assert "worktree decision should flow back" in source["paths"]["decisions"].read_text(encoding="utf-8")


def test_worktree_writeback_skips_snapshot_kind(branch_dir, tmp_repo, tmp_storage_root, monkeypatch):
    source = _init_source_branch(tmp_repo, tmp_storage_root)
    _mark_worktree_inherited(branch_dir, source["branch_name"])
    monkeypatch.setenv("DEV_MEMORY_WORKTREE_WRITEBACK", "1")

    code, payload = _run_record(
        branch_dir,
        kind="progress",
        content="worktree-only progress",
    )

    assert code == 0
    assert "worktree_writeback" not in payload
    assert "worktree-only progress" in branch_dir["paths"]["progress"].read_text(encoding="utf-8")
    assert "worktree-only progress" not in source["paths"]["progress"].read_text(encoding="utf-8")


def test_apply_summary_output_worktree_writeback_mirrors_append_kind(
    branch_dir,
    tmp_repo,
    tmp_storage_root,
    monkeypatch,
):
    source = _init_source_branch(tmp_repo, tmp_storage_root)
    _mark_worktree_inherited(branch_dir, source["branch_name"])
    monkeypatch.setenv("DEV_MEMORY_WORKTREE_WRITEBACK", "1")

    payload = {"decisions": ["summary decision should flow back"]}
    args = type("Args", (), {
        "repo": str(branch_dir["repo_root"]),
        "context_dir": str(branch_dir["storage_root"]),
        "branch": branch_dir["branch_name"],
        "json": json.dumps(payload, ensure_ascii=False),
        "json_file": None,
        "force": False,
    })()

    assert cap.command_apply_summary_output(args) == 0
    assert "summary decision should flow back" in branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
    assert "summary decision should flow back" in source["paths"]["decisions"].read_text(encoding="utf-8")
