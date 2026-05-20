"""Tests for `_parse_blocks` — the semantic-unit aggregation layer over
`_parse_entries`. Each test feeds a hand-crafted section body and checks
both the block list and that every entry produced by `_parse_entries`
still falls inside exactly one block (the invariant the design promises)."""

from dev_memory_tidy import _parse_blocks, _parse_entries


def _invariant_entries_covered(body):
    """For any body, the union of block entry_idx ranges must cover
    [0, entry_count) exactly and without overlap."""
    entries = _parse_entries(body)
    blocks = _parse_blocks(body)
    seen = []
    for b in blocks:
        s, e = b["entry_idx_range"]
        for i in range(s, e + 1):
            seen.append(i)
    assert seen == list(range(len(entries))), (
        f"expected entries 0..{len(entries) - 1} covered exactly once, got {seen!r}"
    )


def test_single_bullet_no_subtree():
    body = "- single line"
    blocks = _parse_blocks(body)
    assert len(blocks) == 1
    b = blocks[0]
    assert b["block_idx"] == 0
    assert b["entry_idx_range"] == [0, 0]
    assert b["has_orphan_paragraphs"] is False
    assert b["raw_lines"] == ["- single line"]
    _invariant_entries_covered(body)


def test_bullet_with_indented_subtree():
    """Note: `_parse_entries` treats every bullet line — including indented
    sub-bullets — as a separate entry (4 entries here). `_parse_blocks`
    re-aggregates them into one semantic unit so the agent can delete-block
    the whole subtree at once."""
    body = "\n".join([
        "- top level",
        "  - sub one",
        "  - sub two",
        "    - deep",
    ])
    blocks = _parse_blocks(body)
    entries = _parse_entries(body)
    assert len(entries) == 4
    assert len(blocks) == 1
    b = blocks[0]
    assert b["entry_idx_range"] == [0, 3]  # all 4 entries in one block
    assert b["has_orphan_paragraphs"] is False
    assert len(b["raw_lines"]) == 4
    _invariant_entries_covered(body)


def test_bullet_plus_why_paragraph_absorbed():
    body = "\n".join([
        "- decision: switch to feature flag",
        "**Why:** lower blast radius",
        "**How to apply:** read flag in handler",
    ])
    blocks = _parse_blocks(body)
    assert len(blocks) == 1
    b = blocks[0]
    assert b["has_orphan_paragraphs"] is True
    # Why + How are both inside raw_lines.
    joined = "\n".join(b["raw_lines"])
    assert "**Why:**" in joined and "**How to apply:**" in joined
    _invariant_entries_covered(body)


def test_two_bullets_one_blank_in_between_are_independent_blocks():
    body = "\n".join([
        "- alpha",
        "",
        "- beta",
    ])
    blocks = _parse_blocks(body)
    assert len(blocks) == 2
    assert blocks[0]["entry_idx_range"] == [0, 0]
    assert blocks[1]["entry_idx_range"] == [1, 1]
    _invariant_entries_covered(body)


def test_two_bullets_no_blank_are_independent_blocks():
    body = "\n".join([
        "- alpha",
        "- beta",
    ])
    blocks = _parse_blocks(body)
    assert len(blocks) == 2
    _invariant_entries_covered(body)


def test_placeholder_bullet_still_block():
    body = "- 待补充"
    blocks = _parse_blocks(body)
    assert len(blocks) == 1
    assert blocks[0]["entry_idx_range"] == [0, 0]
    _invariant_entries_covered(body)


def test_block_id_format_helpers():
    # block_id should be parseable round-trip.
    from dev_memory_tidy import _block_id, _parse_block_id
    bid = _block_id("decisions", 1, 0)
    assert bid == "decisions::1::block-0"
    assert _parse_block_id(bid) == ("decisions", 1, 0)
    assert _parse_block_id("nope") is None
    assert _parse_block_id("a::b::block-c") is None
    assert _parse_block_id("a::1::nope-0") is None


def test_mixed_blocks_real_world_shape():
    """Mirrors the baike_community case: a big block with sub-bullets +
    Why/How paragraphs followed by other bullets."""
    body = "\n".join([
        "- **FE-3.1 工作台-签约前** —— 协议弹窗",
        "  - 一级子项",
        "  - 另一个子项",
        "**Why:** 因为 X",
        "**How to apply:** 走 Y",
        "",
        "- FE-3.4 词条维度管理",
        "- FE-1 社区公开页面",
    ])
    blocks = _parse_blocks(body)
    assert len(blocks) == 3
    assert blocks[0]["has_orphan_paragraphs"] is True
    assert blocks[1]["has_orphan_paragraphs"] is False
    assert blocks[2]["has_orphan_paragraphs"] is False
    _invariant_entries_covered(body)
