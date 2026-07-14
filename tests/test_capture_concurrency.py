"""Concurrency coverage for capture's repo-scoped write lock."""

import subprocess
import sys
from pathlib import Path


LIB = Path(__file__).resolve().parent.parent / "lib"


def test_parallel_records_keep_all_entries_and_valid_utf8(branch_dir, tmp_repo, tmp_storage_root):
    commands = []
    expected = []
    for index in range(8):
        marker = f"并发决策-{index}-" + "中文内容" * 200
        expected.append(marker)
        commands.append(
            [
                sys.executable,
                str(LIB / "dev_memory_capture.py"),
                "record",
                "--kind",
                "decision",
                "--content",
                marker,
                "--force",
                "--repo",
                str(tmp_repo),
                "--context-dir",
                str(tmp_storage_root),
                "--branch",
                "test-branch",
            ]
        )

    processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for command in commands]
    results = [process.communicate(timeout=30) for process in processes]

    for process, (_stdout, stderr) in zip(processes, results):
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")

    decisions = branch_dir["paths"]["decisions"].read_text(encoding="utf-8")
    log = branch_dir["paths"]["log"].read_text(encoding="utf-8")
    for marker in expected:
        assert marker in decisions
    assert log.count("] capture") == len(expected)
