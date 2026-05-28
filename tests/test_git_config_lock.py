import subprocess

from dev_memory_common import set_storage_root_config


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_set_storage_root_config_skips_when_value_unchanged(monkeypatch):
    calls = []

    def fake_run_git(args, cwd, check=True):
        calls.append(args)
        if args == ["config", "--get", "dev-memory.root"]:
            return FakeResult(stdout="/tmp/dev-memory\n")
        raise AssertionError(f"unexpected git write: {args}")

    monkeypatch.setattr("dev_memory_common.run_git", fake_run_git)

    changed = set_storage_root_config("/repo", "/tmp/dev-memory")

    assert changed is False
    assert calls == [["config", "--get", "dev-memory.root"]]


def test_set_storage_root_config_tolerates_parallel_writer(monkeypatch):
    state = {"reads": 0, "writes": 0}

    def fake_run_git(args, cwd, check=True):
        if args == ["config", "--get", "dev-memory.root"]:
            state["reads"] += 1
            return FakeResult(stdout="/tmp/dev-memory\n" if state["writes"] else "")
        if args == ["config", "--local", "dev-memory.root", "/tmp/dev-memory"]:
            state["writes"] += 1
            return FakeResult(
                returncode=255,
                stderr="error: could not lock config file .git/config: File exists",
            )
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr("dev_memory_common.run_git", fake_run_git)
    monkeypatch.setattr("dev_memory_common.time.sleep", lambda _seconds: None)

    changed = set_storage_root_config("/repo", "/tmp/dev-memory")

    assert changed is False
    assert state == {"reads": 2, "writes": 1}


def test_set_storage_root_config_writes_when_value_differs(tmp_repo, tmp_storage_root):
    changed = set_storage_root_config(tmp_repo, tmp_storage_root)

    stored = subprocess.run(
        ["git", "config", "--get", "dev-memory.root"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert changed is True
    assert stored == str(tmp_storage_root)
