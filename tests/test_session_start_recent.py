import sys
from pathlib import Path


HOOKS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from _common import compact_recent_body  # noqa: E402


def test_compact_recent_body_prefers_newest_blank_line_entries():
    body = "- old decision\n\n- middle decision\n\n- newest decision"

    compacted, truncated = compact_recent_body(body, max_lines=2, max_chars=200)

    assert compacted.startswith("- newest decision")
    assert "- middle decision" in compacted
    assert "- old decision" not in compacted
    assert truncated is True


def test_compact_recent_body_prefers_newest_contiguous_bullets():
    body = "- old risk\n- middle risk\n- newest risk"

    compacted, truncated = compact_recent_body(body, max_lines=2, max_chars=200)

    assert compacted.startswith("- newest risk")
    assert "- middle risk" in compacted
    assert "- old risk" not in compacted
    assert truncated is True
