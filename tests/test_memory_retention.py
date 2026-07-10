import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT / "lib"
HOOKS_DIR = ROOT / "scripts" / "hooks"
for directory in (LIB_DIR, HOOKS_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from dev_memory_capture import _append_with_separator  # noqa: E402
from dev_memory_common import append_to_section, limit_markdown_entries, split_sections  # noqa: E402


def _seed(path, title, count):
    path.write_text(
        "# Memory\n\n"
        f"## {title}\n\n"
        + "\n\n".join(f"- entry {idx}" for idx in range(count))
        + "\n",
        encoding="utf-8",
    )


def _extract(path, title):
    _prefix, sections = split_sections(path.read_text(encoding="utf-8"))
    return next(body for section_title, body in sections if section_title == title)


def test_limit_markdown_entries_keeps_newest_200():
    body = "\n\n".join(f"- entry {idx}" for idx in range(205))

    bounded, pruned = limit_markdown_entries(body)

    assert pruned == 5
    assert "- entry 0\n" not in bounded
    assert bounded.startswith("- entry 5")
    assert bounded.endswith("- entry 204")


def test_capture_append_enforces_200_entry_limit(tmp_path):
    path = tmp_path / "decisions.md"
    _seed(path, "关键决策与原因", 205)

    pruned = _append_with_separator(path, "关键决策与原因", "- newest entry")
    body = _extract(path, "关键决策与原因")

    assert pruned == 6
    entry_lines = [line for line in body.splitlines() if line.startswith("- ")]
    assert len(entry_lines) == 200
    assert "- entry 5" not in entry_lines
    assert "- entry 6" in entry_lines
    assert body.endswith("- newest entry")


def test_shared_append_helper_enforces_same_limit(tmp_path):
    path = tmp_path / "glossary.md"
    _seed(path, "当前有效上下文", 200)

    append_to_section(path, "当前有效上下文", "- newest context")
    body = _extract(path, "当前有效上下文")

    assert len([line for line in body.splitlines() if line.startswith("- ")]) == 200
    assert "entry 0" not in body
    assert body.endswith("- newest context")


def test_capture_repo_shared_append_uses_stricter_20_entry_limit(tmp_path):
    path = tmp_path / "decisions.md"
    _seed(path, "跨分支通用决策", 20)

    pruned = _append_with_separator(
        path,
        "跨分支通用决策",
        "- newest shared decision",
        max_entries=20,
    )
    body = _extract(path, "跨分支通用决策")

    assert pruned == 1
    assert len([line for line in body.splitlines() if line.startswith("- ")]) == 20
    assert "- entry 0" not in body.splitlines()
    assert body.endswith("- newest shared decision")
