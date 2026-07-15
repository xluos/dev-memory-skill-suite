import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "bin" / "dev-memory.js"


def run_cli(tmp_path, *args):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "DEV_MEMORY_CONFIG_PATH": str(home / ".dev-memory" / "config.json"),
    }
    result = subprocess.run(
        ["node", str(CLI), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), home


def test_install_trae_variants_use_distinct_repo_directories(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    trae_report, _ = run_cli(tmp_path, "install-hooks", "trae", "--repo", str(repo))
    trae_cn_report, _ = run_cli(tmp_path, "install-hooks", "trae-cn", "--repo", str(repo))

    expected = {
        "trae": repo / ".trae" / "hooks.json",
        "trae-cn": repo / ".trae-cn" / "hooks.json",
    }
    for report in (trae_report, trae_cn_report):
        target = expected[report["agent"]]
        assert Path(report["target"]) == target
        assert report["events"] == ["SessionStart", "Stop"]
        config = json.loads(target.read_text(encoding="utf-8"))
        assert config["version"] == 1
        assert set(config["hooks"]) == {"SessionStart", "Stop"}


def test_install_trae_variants_use_distinct_global_directories(tmp_path):
    trae_report, home = run_cli(tmp_path, "install-hooks", "trae", "--global")
    trae_cn_report, _ = run_cli(tmp_path, "install-hooks", "trae-cn", "--global")

    assert Path(trae_report["target"]) == home / ".trae" / "hooks.json"
    assert Path(trae_cn_report["target"]) == home / ".trae-cn" / "hooks.json"


def test_trae_install_preserves_third_party_hooks_and_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    target = repo / ".trae" / "hooks.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "custom": {"enabled": True},
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "say third-party"}]},
                        {"hooks": [{"type": "command", "command": "dev-memory-cli hook stop"}]},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    run_cli(tmp_path, "install-hooks", "trae", "--repo", str(repo))
    run_cli(tmp_path, "install-hooks", "trae", "--repo", str(repo))

    config = json.loads(target.read_text(encoding="utf-8"))
    stop_hooks = config["hooks"]["Stop"]
    assert config["custom"] == {"enabled": True}
    assert len(stop_hooks) == 2
    assert sum("say third-party" in json.dumps(item) for item in stop_hooks) == 1
    managed = [item for item in stop_hooks if "dev-memory-cli hook stop" in json.dumps(item)]
    assert len(managed) == 1
    assert managed[0]["hooks"][0]["timeout"] == 15


def test_install_all_includes_both_trae_variants(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    reports, _ = run_cli(tmp_path, "install-hooks", "--all", "--repo", str(repo))

    assert [report["agent"] for report in reports] == ["codex", "claude", "trae", "trae-cn"]
    assert (repo / ".trae" / "hooks.json").exists()
    assert (repo / ".trae-cn" / "hooks.json").exists()
