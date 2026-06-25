import subprocess

from dev_memory_common import build_auto_block, collect_git_facts, merged_focus_areas, summarize_focus_areas


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _commit_file(repo, path, content, message):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", message)


def test_recent_commit_focus_is_bounded_to_default_base(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")

    _commit_file(repo, "README.md", "init\n", "init")
    _commit_file(repo, "packages/noisy-a/package.json", "{}\n", "baseline package noise")
    _commit_file(repo, "skills/noisy/SKILL.md", "# skill\n", "baseline skill noise")
    _commit_file(repo, ".trae/skills/noisy/SKILL.md", "# trae\n", "baseline trae noise")
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")

    _git(repo, "switch", "-q", "-c", "feature/focus")
    _commit_file(repo, "apps/infra-website/src/pages/match/index.tsx", "export {}\n", "feature change")

    facts = collect_git_facts(repo, "feature/focus")

    assert facts["default_base"] == "origin/master"
    assert facts["recent_commit_files"] == [
        "apps/infra-website/src/pages/match/index.tsx",
    ]
    assert facts["scope_summary"] == [{"scope": "apps", "files": 1}]
    assert facts["focus_areas"] == ["apps/infra-website/src/pages/match"]


def test_auto_block_history_command_uses_default_base():
    block = build_auto_block({
        "updated_at": "2026-06-04T00:00:00+00:00",
        "branch": "feature/focus",
        "default_base": "origin/master",
        "last_seen_head": "abc123",
        "focus_areas": ["apps/infra-website"],
        "scope_summary": [{"scope": "apps", "files": 1}],
    })

    assert "`git log --oneline -n 10 --no-merges origin/master..HEAD`" in block


def test_merged_focus_areas_drops_stale_and_overwide_existing_entries():
    paths = [
        "config/hooks.json",
        "apps/infra-website/components/FloatingMatchWidget.tsx",
        "apps/infra-website/docs/_nav.json",
        "apps/infra-website/rspress.config.ts",
        "apps/infra-website/src/pages/match-component-v2/index.tsx",
        "apps/infra-website/src/pages/match-component-v2/analyze.ts",
    ]
    polluted_existing = ["packages", "apps", "skills", ".", ".trae"]

    focus = merged_focus_areas(paths, polluted_existing)

    assert "apps/infra-website/src/pages/match-component-v2" in focus
    assert "." not in focus
    assert "packages" not in focus
    assert "apps" not in focus


def test_focus_areas_skip_root_level_files():
    focus = summarize_focus_areas([
        "package.json",
        "go.mod",
        "apps/web/src/index.tsx",
    ])

    assert "." not in focus
    assert "package.json" not in focus
    assert "go.mod" not in focus
    assert "apps/web/src" in focus


def test_focus_area_default_limit_allows_ten_entries():
    paths = [f"packages/pkg-{idx}/src/index.ts" for idx in range(10)]

    focus = summarize_focus_areas(paths)

    assert len(focus) == 10
    assert focus == [f"packages/pkg-{idx}/src" for idx in range(10)]
