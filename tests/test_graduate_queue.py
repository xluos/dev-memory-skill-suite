import fcntl
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dev_memory_common import get_branch_paths
from dev_memory_graduate import _graduate_apply_lock_path


LIB = Path(__file__).resolve().parent.parent / "lib"


def _repo_dir(repo, storage_root):
    _, _, _, _, _, repo_dir, _ = get_branch_paths(
        str(repo),
        str(storage_root),
        "test-branch",
    )
    return repo_dir


def _popen_graduate_apply(repo, storage_root, harvest_file):
    return subprocess.Popen(
        [
            sys.executable,
            str(LIB / "dev_memory_graduate.py"),
            "apply",
            "--harvest-file",
            str(harvest_file),
            "--repo",
            str(repo),
            "--context-dir",
            str(storage_root),
            "--branch",
            "test-branch",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_graduate_apply_waits_behind_repo_queue(branch_dir, tmp_repo, tmp_storage_root, tmp_path):
    harvest = {
        "repo_decisions": [
            {"section": "跨分支通用决策", "body": "- queued harvest", "mode": "append"}
        ],
        "notes": "queue test",
        "archive": False,
    }
    harvest_file = tmp_path / "harvest.json"
    harvest_file.write_text(json.dumps(harvest), encoding="utf-8")

    lock_path = _graduate_apply_lock_path(_repo_dir(tmp_repo, tmp_storage_root))
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    proc = None
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        proc = _popen_graduate_apply(tmp_repo, tmp_storage_root, harvest_file)
        # Give the child enough time to finish Python startup and pre-flight,
        # then prove it is blocked on the queue lock. A 200ms window was
        # flaky on loaded machines and could release the lock before the
        # child had attempted flock(), producing a false queue_waited=false.
        with pytest.raises(subprocess.TimeoutExpired):
            proc.communicate(timeout=1.0)
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        stdout, stderr = proc.communicate(timeout=5)

    assert proc.returncode == 0, stderr
    payload = json.loads(stdout)
    assert payload["queue_waited"] is True
    assert payload["archived_to"] is None
    assert "another apply is running; queued" in stderr
    assert "- queued harvest" in branch_dir["paths"]["repo_decisions"].read_text(encoding="utf-8")


def test_graduate_apply_preflight_errors_do_not_wait_for_queue(branch_dir, tmp_repo, tmp_storage_root, tmp_path):
    harvest_file = tmp_path / "bad-harvest.json"
    harvest_file.write_text(json.dumps({"repo_context": []}), encoding="utf-8")

    lock_path = _graduate_apply_lock_path(_repo_dir(tmp_repo, tmp_storage_root))
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        proc = _popen_graduate_apply(tmp_repo, tmp_storage_root, harvest_file)
        stdout, stderr = proc.communicate(timeout=2)
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    assert proc.returncode == 1
    assert stdout == ""
    assert "unknown harvest key" in stderr
    assert "queued" not in stderr
