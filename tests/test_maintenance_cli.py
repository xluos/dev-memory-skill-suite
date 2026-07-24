import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "dev-memory.js"


def run_cli(*args, cwd=None, env=None):
    return subprocess.run(
        ["node", str(CLI), *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_suite_exposes_read_and_manual_maintenance_skills():
    manifest = json.loads((ROOT / "suite-manifest.json").read_text(encoding="utf-8"))
    assert manifest["skills"] == ["dev-memory-read", "dev-memory-maintain"]
    assert sorted(path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")) == [
        "dev-memory-maintain",
        "dev-memory-read",
    ]
    assert {
        "dev-memory-capture",
        "dev-memory-setup",
        "dev-memory-tidy",
        "dev-memory-graduate",
    }.issubset(set(manifest["legacy_skills"]))


def test_maintenance_skill_is_manual_and_routes_to_one_reference():
    skill_dir = ROOT / "skills" / "dev-memory-maintain"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    read_skill = (ROOT / "skills" / "dev-memory-read" / "SKILL.md").read_text(encoding="utf-8")
    tidy = (skill_dir / "references" / "tidy.md").read_text(encoding="utf-8")
    archive = (skill_dir / "references" / "archive.md").read_text(encoding="utf-8")

    assert "仅当用户明确点名" in skill
    assert "不要因普通的“整理记忆”或“归档分支”等自然语言自动触发" in skill
    assert "只读取当前类型对应的 reference" in skill
    assert "references/tidy.md" in skill
    assert "references/archive.md" in skill
    assert "没有用户导出的 plan 文件，禁止调用 `tidy apply`" in tidy
    assert "获得明确确认之前禁止 apply" in archive
    assert "用户显式调用 `dev-memory-maintain`" in read_skill


def test_tidy_print_prompt_is_self_contained(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = run_cli(
        "maintain",
        "tidy",
        "--repo",
        str(repo),
        "--branch",
        "feature/example",
        "--scope",
        "branch+repo",
        "--print-prompt",
    )
    assert result.returncode == 0, result.stderr
    assert "DEV_MEMORY_INTERNAL_MAINTENANCE_AGENT_V1" in result.stdout
    assert f"目标仓库：{repo}" in result.stdout
    assert "目标分支：feature/example" in result.stdout
    assert "整理范围：branch+repo" in result.stdout
    assert "tidy prepare" in result.stdout
    assert "没有用户导出的 plan 文件" in result.stdout
    assert "不要依赖全局 dev-memory" in result.stdout


def test_archive_print_prompt_keeps_confirmation_gate(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = run_cli("maintain", "archive", "--repo", str(repo), "--print-prompt")
    assert result.returncode == 0, result.stderr
    assert "graduate dry-run" in result.stdout
    assert "获得明确确认之前禁止 apply" in result.stdout
    assert "archive_summary.md" in result.stdout


def test_maintain_rejects_invalid_scope(tmp_path):
    result = run_cli(
        "maintain",
        "tidy",
        "--repo",
        str(tmp_path),
        "--scope",
        "all",
        "--print-prompt",
    )
    assert result.returncode != 0
    assert "unsupported tidy scope" in result.stderr


def test_maintain_launches_dedicated_codex_session(tmp_path):
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    args_file = tmp_path / "args.txt"
    repo.mkdir()
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$DEV_MEMORY_TEST_ARGS\"\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DEV_MEMORY_TEST_ARGS": str(args_file),
    }

    result = run_cli(
        "maintain",
        "tidy",
        "--repo",
        str(repo),
        "--executor",
        "codex",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    args = args_file.read_text(encoding="utf-8")
    assert f"-C\n{repo}" in args
    assert "--sandbox\ndanger-full-access" in args
    assert "--ask-for-approval\non-request" in args
    assert "DEV_MEMORY_INTERNAL_MAINTENANCE_AGENT_V1" in args


def test_init_alias_routes_to_setup_init(tmp_path):
    repo = tmp_path / "repo"
    storage = tmp_path / "memory"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    result = run_cli(
        "init",
        "--repo",
        str(repo),
        "--branch",
        "feature/example",
        "--context-dir",
        str(storage),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["branch"] == "feature/example"
    assert Path(payload["branch_dir"]).exists()
