#!/usr/bin/env python3
"""
dev-memory-capture: unified write entrypoint for repo+branch development
assets. Merges the old sync (checkpoint batch record) and update (targeted
section rewrite) into one skill. Also the lazy-init entry — never raises on
missing branch_dir; the directory is seeded on first write.

Subcommands:
  record          : write content into one or more sections (inline/kind/auto/batch)
  show            : dump current paths + missing docs
  sync-working-tree : refresh progress.md's auto-sync block from git facts
  record-head     : stamp last_seen_head onto branch+repo manifests
  suggest-kind    : dry-run heuristic classifier
  classify        : dry-run — run classifier AND cross-branch detector
"""

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dev_memory_common import (
    AUTO_END,
    AUTO_START,
    PLACEHOLDER_MARKERS,
    append_log_event,
    append_to_section,
    asset_paths,
    classify_content,
    collect_git_facts,
    ensure_branch_paths_exist,
    get_head_commit,
    get_setup_completed,
    is_cross_branch_candidate,
    join_sections,
    list_missing_docs,
    merged_focus_areas,
    now_iso,
    read_json,
    render_bullets,
    run_git,
    sanitize_branch_name,
    split_sections,
    sync_progress,
    upsert_markdown_section,
    upsert_progress_section,
    write_json,
)


def _log_targets_for(touched):
    """Return (touches_repo, formatted_targets_line) from the touched list.

    `touched` items look like {"file": "branch/decisions.md", "section": ...,
    "mode": ...}. The formatted line keeps it short for log readability:
    each target rendered as `branch/decisions.md(append)`, dropping the
    section to avoid blowing past _LOG_SUMMARY_MAX on batch writes.
    """
    if not touched:
        return False, None
    touches_repo = any(t.get("file", "").startswith("repo/") for t in touched)
    parts = []
    for t in touched:
        file_ = t.get("file", "?")
        mode = t.get("mode", "?")
        parts.append(f"{file_}({mode})")
    return touches_repo, ", ".join(parts)


def _emit_capture_log(paths, *, action, kind_label, summary, touched, extra_details=None):
    """Append an event row to log.md after a successful capture write.

    Writes to the branch-level log by default. If any touched target lives
    under the repo-shared layer (`repo/...`), also mirrors a row into the
    repo log so cross-branch readers see shared-layer mutations.
    """
    touches_repo, targets_line = _log_targets_for(touched)
    details = []
    if targets_line:
        details.append(("targets", targets_line))
    for k, v in (extra_details or []):
        details.append((k, v))
    append_log_event(
        paths.get("log"),
        action,
        kind=kind_label,
        summary=summary,
        details=details,
    )
    if touches_repo and paths.get("repo_log") and paths.get("repo_log") != paths.get("log"):
        append_log_event(
            paths.get("repo_log"),
            action,
            kind=kind_label,
            summary=summary,
            details=details,
        )


def _append_with_separator(path, title, body):
    """Like append_to_section but always inserts a blank line between the
    existing section body and the new entry. Drops placeholder-only bodies
    instead of padding them with a blank line.
    """
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    prefix, sections = split_sections(content)
    target = title.strip()
    matched = False
    updated = []
    body_stripped = body.strip()

    def _is_placeholder_only(text):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return True
        return all(any(marker in ln for marker in PLACEHOLDER_MARKERS) for ln in lines)

    for existing_title, existing_body in sections:
        if existing_title.strip() == target and not matched:
            if _is_placeholder_only(existing_body):
                combined = body_stripped
            else:
                combined = existing_body.rstrip() + "\n\n" + body_stripped
            updated.append((existing_title, combined))
            matched = True
        else:
            updated.append((existing_title, existing_body))
    if not matched:
        updated.append((title, body_stripped))
    path.write_text(join_sections(prefix, updated), encoding="utf-8")


# ---------------------------------------------------------------------------
# Dedup check primitives
# ---------------------------------------------------------------------------

# Chinese (and a few English) phrases that overtly signal "I'm rewriting an
# earlier entry". When the new content contains any of these, similarity is
# boosted (so even mid-similarity matches surface) and the recommendation
# leans toward update_existing — capture should treat this as a hint that the
# user already knows there's a prior entry to revise.
SUPERSEDES_KEYWORDS = (
    "supersedes",
    "重新校正",
    "已更新",
    "新版",
    "修正",
    "推翻",
    "取代",
    "覆盖",
    "撤销",
)

# 80-char preview window for similarity comparison. Long bodies dilute the
# ratio with prose; capping at the first line's first 80 chars keeps the
# signal-to-noise ratio high — typical entries in decisions.md / risks.md
# fit their key claim into the lead line.
_SIM_PREVIEW_LEN = 80


def _first_nonempty_line(text):
    """Return the first non-blank line of `text`, stripped. Empty string if
    `text` is all blank. Used as the comparison anchor for similarity_check
    because top-level bullets carry their "thesis" in the lead line."""
    if not text:
        return ""
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _strip_bullet_prefix(text):
    """Drop a leading `- ` / `* ` from a single line so the comparison key is
    the entry's content, not its bullet marker. Without this, every entry's
    preview starts with `- ` and difflib over-credits common prefixes."""
    s = text.lstrip()
    if s.startswith("- ") or s.startswith("* "):
        return s[2:].strip()
    return s


def _section_top_level_entries(section_body):
    """Yield (entry_idx, full_text) for each top-level bullet entry in a
    section body. Mirrors tidy's `_parse_entries` boundary rules (top-level
    `- ` starts a new entry; indented or non-bullet continuation lines fold
    into the current entry) but stays local to capture so the modules don't
    grow a cross-dependency on a private symbol.

    The AUTO-GENERATED block (machine-managed state in progress.md) is
    excluded — dedup against auto-block content would block every progress
    write the moment git facts repeat.
    """
    if not section_body:
        return
    # Drop the AUTO-GENERATED block if present so we don't dedup against
    # auto-managed content (e.g. progress.md focus areas regenerated by
    # sync-working-tree). The visible top of the section still gets scanned.
    body = section_body
    if AUTO_START in body and AUTO_END in body:
        start = body.index(AUTO_START)
        end = body.index(AUTO_END) + len(AUTO_END)
        body = (body[:start].rstrip() + "\n" + body[end:].lstrip()).strip()

    current_lines = None
    current_idx = -1
    counter = -1
    entries = []

    def flush():
        if current_lines is None:
            return
        entries.append((current_idx, "\n".join(current_lines).strip()))

    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            if current_lines is not None:
                flush()
                current_lines_local = None  # noqa: F841 (clarity)
            current_lines = None
            continue
        if stripped.startswith("- ") and not (raw.startswith(" ") or raw.startswith("\t")):
            if current_lines is not None:
                flush()
            counter += 1
            current_idx = counter
            current_lines = [raw]
        elif current_lines is not None:
            current_lines.append(raw)
        # Prose before any bullet is ignored — matches _parse_entries.
    if current_lines is not None:
        flush()

    for entry_idx, text in entries:
        yield entry_idx, text


def _is_placeholder_entry(text):
    """A top-level bullet whose content is exactly the placeholder marker
    (待补充/待刷新) shouldn't participate in dedup — it represents "empty
    slot, please fill" not "real content."""
    body = _strip_bullet_prefix(_first_nonempty_line(text))
    return body in ("待补充", "待刷新")


def similarity_check(new_content, section_body, threshold=0.7):
    """Find near-duplicate top-level bullets in `section_body` for `new_content`.

    Returns a list of matches sorted by similarity desc:
        [{"entry_idx": int, "similarity": float, "match_first_line": str,
          "match_full_text": str}, ...]

    Algorithm:
      1. Compute the comparison key for `new_content`: first non-empty line,
         bullet prefix stripped, capped at _SIM_PREVIEW_LEN chars.
      2. For each top-level bullet in `section_body` (placeholders + AUTO
         blocks excluded), compute the same key and a
         difflib.SequenceMatcher ratio against the new key.
      3. If `new_content` contains any SUPERSEDES_KEYWORDS, boost the
         similarity by +0.15 (capped at 1.0). Rationale: an explicit "I'm
         updating X" cue from the user is a stronger signal than text
         similarity alone — surface mid-similarity candidates that would
         otherwise sit below the threshold.
      4. Keep only matches with (boosted) similarity ≥ `threshold`.
    """
    new_first = _strip_bullet_prefix(_first_nonempty_line(new_content))
    if not new_first:
        return []
    new_key = new_first[:_SIM_PREVIEW_LEN]

    has_supersedes = any(kw in new_content for kw in SUPERSEDES_KEYWORDS)

    matches = []
    for entry_idx, entry_text in _section_top_level_entries(section_body):
        if _is_placeholder_entry(entry_text):
            continue
        existing_first = _strip_bullet_prefix(_first_nonempty_line(entry_text))
        if not existing_first:
            continue
        existing_key = existing_first[:_SIM_PREVIEW_LEN]
        ratio = difflib.SequenceMatcher(None, new_key, existing_key).ratio()
        if has_supersedes:
            ratio = min(1.0, ratio + 0.15)
        if ratio >= threshold:
            matches.append({
                "entry_idx": entry_idx,
                "similarity": round(ratio, 4),
                "match_first_line": existing_first,
                "match_full_text": entry_text,
                "supersedes_signal_detected": has_supersedes,
            })
    matches.sort(key=lambda m: m["similarity"], reverse=True)
    return matches


def _file_key_to_label(file_key):
    """Same as _label() but standalone (avoids forward reference). Kept here
    so _check_dedup_for_kind can emit the user-facing target_file string
    before _label is defined later in the module."""
    if file_key.startswith("repo_"):
        return f"repo/{file_key[5:]}.md"
    if file_key == "pending_promotion":
        return "branch/pending-promotion.md"
    return f"branch/{file_key}.md"


def _build_dedup_hint(kind, file_key, section_title, new_content, matches):
    """Assemble the dedup_hint payload returned to the caller when a write is
    blocked. Schema is part of the public CLI contract — additions OK,
    renames/removals are breaking changes.

    `recommendation` logic:
      - explicit supersedes signal in new content → "update_existing"
      - single match with very high similarity (≥ 0.9) → "update_existing"
      - otherwise → "review_and_decide" (let agent inspect matches[])
    """
    preview = _first_nonempty_line(new_content)[:_SIM_PREVIEW_LEN]
    has_supersedes = any(kw in new_content for kw in SUPERSEDES_KEYWORDS)
    high_conf_single = len(matches) == 1 and matches[0]["similarity"] >= 0.9
    if has_supersedes or high_conf_single:
        recommendation = "update_existing"
    else:
        recommendation = "review_and_decide"

    # Find the section_idx for each match so we can render full entry ids.
    # The caller has already passed `section_title`; section_idx is derived
    # later in _check_dedup_for_kind when it has the file context.
    next_actions = [
        "如果是修订旧条目: dev-memory-cli capture rewrite-entry --id <match_id> --content '<text>'",
        "如果确实要再写一条: dev-memory-cli capture record --kind {kind} --content '<text>' --force".format(kind=kind),
        "如果不写: 不调任何命令",
    ]
    return {
        "blocked": True,
        "reason": "similar_entry_exists",
        "kind": kind,
        "target_file": _file_key_to_label(file_key),
        "section": section_title,
        "new_content_preview": preview,
        "matches": matches,  # ids filled in by caller after section_idx lookup
        "recommendation": recommendation,
        "next_actions": next_actions,
    }


def _resolve_section_idx(target_path, section_title):
    """Return the 0-based section index of `section_title` in `target_path`,
    or None if the file doesn't exist / section is absent. Used to construct
    full entry ids (`<file_key>::<section_idx>::<entry_idx>`) for matches
    surfaced via the dedup hint."""
    if not target_path.exists():
        return None
    content = target_path.read_text(encoding="utf-8")
    _, sections = split_sections(content)
    target = section_title.strip()
    for idx, (title, _body) in enumerate(sections):
        if title.strip() == target:
            return idx
    return None


def _load_section_body(target_path, section_title):
    """Return the body of `section_title` in `target_path` (empty string if
    file missing / section absent)."""
    if not target_path.exists():
        return ""
    content = target_path.read_text(encoding="utf-8")
    _, sections = split_sections(content)
    target = section_title.strip()
    for title, body in sections:
        if title.strip() == target:
            return body
    return ""


def _check_dedup_for_kind(paths, kind, body, force=False, title_override=None, threshold=None):
    """Return a dedup_hint dict if the write should be blocked, else None.

    Pure read; writes nothing. Skips:
      - kinds whose default_mode is not "append" (upsert always overwrites,
        dedup is meaningless there)
      - force=True (explicit opt-out)
      - kinds not in KIND_MAP (caller handles error elsewhere)
      - empty/whitespace body (caller handles error elsewhere)

    `threshold` (optional float in (0.0, 1.0]) overrides similarity_check's
    default 0.7. None preserves default behavior — kept as the production
    contract; the override is exposed only via the hidden CLI flag
    `--dedup-threshold` for debugging / experiments. Validation of the range
    is performed at the CLI layer (command_record) before reaching here, so
    this helper trusts the caller's value when it's not None.

    When matches are found, the returned hint includes each match's full
    `<file_key>::<section_idx>::<entry_idx>` id so the agent can pass it
    straight to `rewrite-entry --id <id>`.
    """
    if force:
        return None
    if not body or not body.strip():
        return None
    spec = KIND_MAP.get(kind)
    if not spec:
        return None
    if spec.get("default_mode") != "append":
        return None

    file_key = spec["file"]
    target_path = paths[file_key]
    section_title = (title_override or spec["section"]).strip()
    section_body = _load_section_body(target_path, section_title)
    if threshold is None:
        matches = similarity_check(body, section_body)
    else:
        matches = similarity_check(body, section_body, threshold=threshold)
    if not matches:
        return None

    section_idx = _resolve_section_idx(target_path, section_title)
    # Fill in full entry ids on each match. If section_idx is None (section
    # somehow not found, but we got matches — implies an in-memory race or a
    # bug), fall back to entry-idx-only ids so the agent at least sees the
    # ordinal.
    for m in matches:
        if section_idx is None:
            m["id"] = f"{file_key}::?::{m['entry_idx']}"
        else:
            m["id"] = f"{file_key}::{section_idx}::{m['entry_idx']}"

    return _build_dedup_hint(kind, file_key, section_title, body, matches)


# ---------------------------------------------------------------------------
# rewrite-entry primitives
# ---------------------------------------------------------------------------

def _parse_entry_id_local(eid):
    """Parse `<file_key>::<section_idx>::<entry_idx>` into a tuple. Local copy
    rather than importing tidy's private function — keeps capture's CLI
    surface decoupled from tidy's internal refactor risk."""
    if not isinstance(eid, str):
        return None
    parts = eid.split("::")
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _replace_entry_at_index(body, target_idx, new_text):
    """Replace the top-level bullet at ordinal `target_idx` in `body` with
    `new_text`. Returns (new_body, previous_first_line) where
    `previous_first_line` is the first line of the entry that got
    overwritten (for caller's audit output).

    Why this lives here and not in tidy: tidy's `_apply_actions_to_section`
    takes an entry-action dict and processes the whole section; capture's
    rewrite-entry only ever touches one entry, so a focused helper keeps the
    code reviewable and skips tidy's keep/delete branching.

    Multi-line `new_text` is rendered as a single bullet with continuation
    lines indented two spaces — same shape `_apply_actions_to_section` uses
    for edited entries, so the on-disk format stays consistent across both
    write paths.
    """
    lines = body.splitlines()
    out_lines = []
    current_block_lines = None
    current_idx = -1
    counter = -1
    found = False
    previous_first_line = None

    def render_new():
        new_text_stripped = (new_text or "").strip()
        if not new_text_stripped:
            # Empty new_text would silently delete — disallow at this layer;
            # caller validates before calling.
            return []
        first, *rest = new_text_stripped.split("\n")
        out = ["- " + first.strip()]
        for cont in rest:
            out.append("  " + cont.strip())
        return out

    def flush():
        nonlocal current_block_lines, current_idx, found, previous_first_line
        if current_block_lines is None:
            return
        if current_idx == target_idx and not found:
            # Capture previous first line before replacement for the audit
            # output.
            for ln in current_block_lines:
                s = ln.strip()
                if s.startswith("- "):
                    previous_first_line = s[2:].strip()
                    break
                if s:
                    previous_first_line = s
                    break
            out_lines.extend(render_new())
            found = True
        else:
            out_lines.extend(current_block_lines)
        current_block_lines = None
        current_idx = -1

    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("- ") and not (raw.startswith(" ") or raw.startswith("\t")):
            flush()
            counter += 1
            current_idx = counter
            current_block_lines = [raw]
        elif current_block_lines is not None and stripped and (raw.startswith(" ") or raw.startswith("\t")):
            current_block_lines.append(raw)
        elif not stripped:
            flush()
            out_lines.append(raw)
        else:
            # Free-form prose between bullets — pass through.
            flush()
            out_lines.append(raw)
    flush()

    # Trim trailing blank lines.
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()

    return "\n".join(out_lines).strip(), previous_first_line, found


def _delete_entry_at_index(body, target_idx):
    lines = body.splitlines()
    out_lines = []
    current_block_lines = None
    current_idx = -1
    counter = -1
    found = False
    previous_first_line = None

    def capture_previous():
        nonlocal previous_first_line
        for ln in current_block_lines or []:
            s = ln.strip()
            if s.startswith("- "):
                previous_first_line = s[2:].strip()
                break
            if s:
                previous_first_line = s
                break

    def flush():
        nonlocal current_block_lines, current_idx, found
        if current_block_lines is None:
            return
        if current_idx == target_idx and not found:
            capture_previous()
            found = True
        else:
            out_lines.extend(current_block_lines)
        current_block_lines = None
        current_idx = -1

    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("- ") and not (raw.startswith(" ") or raw.startswith("\t")):
            flush()
            counter += 1
            current_idx = counter
            current_block_lines = [raw]
        elif current_block_lines is not None and stripped and (raw.startswith(" ") or raw.startswith("\t")):
            current_block_lines.append(raw)
        elif not stripped:
            flush()
            out_lines.append(raw)
        else:
            flush()
            out_lines.append(raw)
    flush()

    while out_lines and not out_lines[-1].strip():
        out_lines.pop()

    return "\n".join(out_lines).strip(), previous_first_line, found


def _entry_mutation(paths, eid, *, new_text=None, delete=False):
    parsed = _parse_entry_id_local((eid or "").strip())
    if not parsed:
        raise RuntimeError(f"malformed entry id: {eid!r}; expected <file_key>::<section_idx>::<entry_idx>")
    file_key, section_idx, entry_idx = parsed
    if file_key not in paths:
        raise RuntimeError(f"unknown file_key {file_key!r}; available: {', '.join(sorted(paths.keys()))}")

    if not delete and not (new_text or "").strip():
        raise RuntimeError("one of --content / --content-file is required")

    target_path = paths[file_key]
    if not target_path.exists():
        raise RuntimeError(f"file does not exist: {target_path}")

    content = target_path.read_text(encoding="utf-8")
    prefix, sections = split_sections(content)
    if section_idx < 0 or section_idx >= len(sections):
        raise RuntimeError(f"section_idx {section_idx} out of range (file has {len(sections)} sections)")

    section_title, section_body = sections[section_idx]
    mutator = _delete_entry_at_index if delete else (
        lambda body, idx: _replace_entry_at_index(body, idx, new_text)
    )

    if AUTO_START in section_body and AUTO_END in section_body:
        head_end = section_body.index(AUTO_START)
        head = section_body[:head_end].rstrip()
        tail = section_body[head_end:]
        new_head, previous_first_line, found = mutator(head, entry_idx)
        new_body = (new_head + "\n\n" + tail).strip() if new_head else tail.strip()
    else:
        new_body, previous_first_line, found = mutator(section_body, entry_idx)

    if not found:
        raise RuntimeError(f"entry_idx {entry_idx} not found in section {section_idx!r}")

    new_sections = list(sections)
    new_sections[section_idx] = (section_title, new_body)
    target_path.write_text(join_sections(prefix, new_sections), encoding="utf-8")
    return {
        "id": eid,
        "file_key": file_key,
        "file": _label(file_key),
        "section": section_title,
        "previous_first_line": previous_first_line,
    }


def _validate_entry_reference(paths, eid, *, require_content=None):
    parsed = _parse_entry_id_local((eid or "").strip())
    if not parsed:
        raise RuntimeError(f"malformed entry id: {eid!r}; expected <file_key>::<section_idx>::<entry_idx>")
    file_key, section_idx, entry_idx = parsed
    if file_key not in paths:
        raise RuntimeError(f"unknown file_key {file_key!r}; available: {', '.join(sorted(paths.keys()))}")
    if require_content is not None and not (require_content or "").strip():
        raise RuntimeError(f"rewrite {eid!r} requires non-empty content")
    target_path = paths[file_key]
    if not target_path.exists():
        raise RuntimeError(f"file does not exist: {target_path}")
    content = target_path.read_text(encoding="utf-8")
    _prefix, sections = split_sections(content)
    if section_idx < 0 or section_idx >= len(sections):
        raise RuntimeError(f"section_idx {section_idx} out of range (file has {len(sections)} sections)")
    section_body = sections[section_idx][1]
    if AUTO_START in section_body and AUTO_END in section_body:
        section_body = section_body[:section_body.index(AUTO_START)].rstrip()
    entries = {idx for idx, _text in _section_top_level_entries(section_body)}
    if entry_idx not in entries:
        raise RuntimeError(f"entry_idx {entry_idx} not found in section {section_idx!r}")


# v2 KIND_MAP. `default_mode` governs whether a new entry accumulates
# (append) or replaces the whole section (upsert). Accumulation fits
# "decisions", "risks", "glossary" where each entry is independent and
# historically meaningful; upsert fits "progress/next/overview" where the
# section represents *latest state* and older content is stale by design.
KIND_MAP = {
    # accumulation (each entry stands alone, new ones add to the list)
    "decision": {"file": "decisions", "section": "关键决策与原因", "default_mode": "append"},
    "risk": {"file": "risks", "section": "阻塞与注意点", "default_mode": "append"},
    "glossary": {"file": "glossary", "section": "当前有效上下文", "default_mode": "append"},
    "source": {"file": "glossary", "section": "分支源资料入口", "default_mode": "append"},
    # snapshot (section always reflects the latest state; new write replaces)
    "overview": {"file": "overview", "section": "当前目标", "default_mode": "upsert"},
    "scope": {"file": "overview", "section": "范围边界", "default_mode": "upsert"},
    "constraint": {"file": "overview", "section": "关键约束", "default_mode": "upsert"},
    # repo-shared: decisions/context/source accumulate, overview/constraint snapshot
    "shared-decision": {"file": "repo_decisions", "section": "跨分支通用决策", "default_mode": "append"},
    "shared-context": {"file": "repo_glossary", "section": "长期有效背景", "default_mode": "append"},
    "shared-source": {"file": "repo_glossary", "section": "共享入口", "default_mode": "append"},
    "shared-overview": {"file": "repo_overview", "section": "长期目标与边界", "default_mode": "upsert"},
    "shared-constraint": {"file": "repo_overview", "section": "仓库级关键约束", "default_mode": "upsert"},
    # semantic file map: agent outputs merged mapping each session
    "filemap": {"file": "progress", "section": "功能文件索引", "default_mode": "upsert"},
    # fallback bins always accumulate
    "unsorted": {"file": "unsorted", "section": "待分类", "default_mode": "append"},
    "pending": {"file": "pending_promotion", "section": "候选条目", "default_mode": "append"},
}


# Worktree write-back deliberately mirrors only append-style knowledge. Snapshot
# fields like progress/overview/next represent the current branch state and would
# corrupt the source branch if copied back blindly.
WORKTREE_WRITEBACK_KINDS = {"decision", "risk", "glossary", "source"}


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _worktree_writeback_enabled(repo_root):
    env_value = os.environ.get("DEV_MEMORY_WORKTREE_WRITEBACK")
    if env_value is not None:
        return _truthy(env_value)
    cfg = run_git(
        ["config", "--bool", "--get", "dev-memory.worktreeWriteback"],
        cwd=repo_root,
        check=False,
    )
    return cfg.returncode == 0 and _truthy(cfg.stdout)


def _worktree_inherit_source(manifest, branch_name):
    provenance = manifest.get("provenance") or []
    if not isinstance(provenance, list):
        return None
    for item in reversed(provenance):
        if not isinstance(item, dict):
            continue
        if item.get("op") != "worktree-inherit":
            continue
        source = str(item.get("from") or "").strip()
        if source and source != branch_name:
            return source
    return None


def _branch_head(repo_root, branch_name):
    result = run_git(["rev-parse", branch_name], cwd=repo_root, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _worktree_writeback_context(repo_root, repo_dir, paths, branch_name):
    if branch_name is None or not _worktree_writeback_enabled(repo_root):
        return None
    manifest = read_json(paths["manifest"]) or {}
    source_name = _worktree_inherit_source(manifest, branch_name)
    if not source_name:
        return None
    source_key = sanitize_branch_name(source_name)
    source_dir = repo_dir / "branches" / source_key
    if not source_dir.exists() or not source_dir.is_dir():
        return {
            "source": source_name,
            "source_key": source_key,
            "source_dir": str(source_dir),
            "paths": None,
            "attempted": False,
            "touched": [],
            "skipped": [{"reason": "source-missing", "source_dir": str(source_dir)}],
        }
    return {
        "source": source_name,
        "source_key": source_key,
        "source_dir": str(source_dir),
        "paths": asset_paths(repo_dir, source_dir),
        "attempted": False,
        "touched": [],
        "skipped": [],
    }


def _maybe_worktree_writeback(ctx, kind, body, *, title_override=None, mode_override=None,
                             force=False, dedup_threshold=None, summary=None):
    if not ctx:
        return None
    if kind not in WORKTREE_WRITEBACK_KINDS:
        return None
    mode = mode_override or KIND_MAP.get(kind, {}).get("default_mode", "upsert")
    if mode != "append":
        return None
    ctx["attempted"] = True
    if not ctx.get("paths"):
        return None
    hint = _check_dedup_for_kind(
        ctx["paths"],
        kind,
        body,
        force=force,
        title_override=title_override,
        threshold=dedup_threshold,
    )
    if hint is not None:
        ctx["skipped"].append({
            "kind": kind,
            "reason": "dedup-blocked",
            "content_preview": _first_nonempty_line(body)[:_SIM_PREVIEW_LEN],
            "dedup_hint": hint,
        })
        return None
    rec = _write_one(ctx["paths"], kind, body, title_override=title_override, mode_override=mode)
    rec["mode"] = "worktree-writeback-append"
    rec["source_branch"] = ctx["source"]
    ctx["touched"].append(rec)
    _emit_capture_log(
        ctx["paths"],
        action="worktree-writeback",
        kind_label=kind,
        summary=summary or body,
        touched=[rec],
    )
    return rec


def _finalize_worktree_writeback(ctx, repo_root, repo_key, storage_root):
    if not ctx or not ctx.get("paths") or not ctx["touched"]:
        return None
    updated_at = now_iso()
    manifest = read_json(ctx["paths"]["manifest"]) or {}
    manifest.update({
        "repo_key": repo_key,
        "branch": ctx["source"],
        "branch_key": ctx["source_key"],
        "storage_root": str(storage_root),
        "updated_at": updated_at,
        "last_seen_head": _branch_head(repo_root, ctx["source"]),
        "last_capture_kind": "worktree-writeback",
        "last_capture_mode": "worktree-writeback",
        "last_capture_update_mode": "worktree-writeback",
        "last_capture_targets": ctx["touched"],
    })
    write_json(ctx["paths"]["manifest"], manifest)
    return {
        "source": ctx["source"],
        "source_dir": ctx["source_dir"],
        "touched": ctx["touched"],
        "skipped": ctx["skipped"],
        "updated_at": updated_at,
    }

# Session-payload → kind mapping, used by `record --summary-json`. Each entry
# is (payload_key, kind, optional_transform). Several keys may route to the
# same kind; that's fine — the write layer upserts.
SESSION_PAYLOAD_MAP = [
    ("overview_summary", "overview", None),
    ("risks", "risk", None),
    ("memory", "glossary", None),
    ("context_updates", "glossary", None),
    ("review_notes", "decision", None),
    ("frontend_updates", "glossary", "前端相关"),
    ("backend_updates", "glossary", "后端相关"),
    ("test_updates", "glossary", "测试相关"),
    ("sources", "shared-source", None),
    ("source_updates", "shared-source", None),
]

SESSION_DIRECT_PAYLOAD_MAP = []

SESSION_EXTRA_PAYLOAD_MAP = [
    ("glossary", "glossary", None),
    ("shared_context", "shared-context", None),
    ("shared_sources", "shared-source", None),
]


def normalize_items(items):
    if items is None:
        return []
    if isinstance(items, str):
        stripped = items.strip()
        return [stripped] if stripped else []
    return [str(item).strip() for item in items if str(item).strip()]


def bullets(items, empty_text="- 待补充", wrap_code=False):
    return render_bullets(normalize_items(items), empty_text=empty_text, wrap_code=wrap_code)


def _decision_summary(item):
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    return str(item.get("decision") or item.get("summary") or "").strip()


def decision_body(item):
    summary = _decision_summary(item)
    parts = [f"- 结论: {summary}"]
    if not isinstance(item, dict):
        return "\n".join(parts)
    if item.get("reason"):
        parts.append(f"- 原因: {item['reason']}")
    if item.get("impact"):
        parts.append(f"- 影响范围: {item['impact']}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Write primitives
# ---------------------------------------------------------------------------

def _resolve_target(paths, kind, title_override=None):
    """Return (target_path, section_title) for a given kind. Raises on
    unknown kind so the caller can surface a clear error."""
    spec = KIND_MAP.get(kind)
    if not spec:
        raise RuntimeError(f"unsupported kind: {kind}")
    target_path = paths[spec["file"]]
    section_title = (title_override or spec["section"]).strip()
    return spec["file"], target_path, section_title


def _write_one(paths, kind, body, title_override=None, *, mode_override=None):
    """Write a body into the kind's target file. Picks append vs upsert from
    KIND_MAP[kind].default_mode unless overridden. progress.md uses a
    dedicated upsert that preserves the auto-sync marker.

    When appending multi-line bodies, a blank line is inserted between the
    existing content and the new entry so each entry reads as its own unit.
    """
    file_key, target_path, section_title = _resolve_target(paths, kind, title_override)
    spec = KIND_MAP.get(kind) or {}
    mode = mode_override or spec.get("default_mode", "upsert")

    if mode == "append":
        # Auto-prefix a bullet if the body isn't already one — accumulation
        # sections are bullet lists by convention. The separator helper
        # handles the blank-line spacing so entries remain visually distinct.
        body_to_write = body.strip()
        if not body_to_write.startswith(("- ", "* ", "#")):
            body_to_write = "- " + body_to_write
        _append_with_separator(target_path, section_title, body_to_write)
    else:
        if file_key == "progress":
            upsert_progress_section(target_path, section_title, body)
        else:
            upsert_markdown_section(target_path, section_title, body)
    return {"file": _label(file_key), "section": section_title, "mode": mode}


def _label(file_key):
    if file_key.startswith("repo_"):
        return f"repo/{file_key[5:]}.md"
    if file_key == "pending_promotion":
        return "branch/pending-promotion.md"
    return f"branch/{file_key}.md"


def _maybe_stage_pending(paths, body, branch_name):
    """If body looks cross-branch-reusable, append a copy to pending-promotion
    with a lightweight marker. Returns the touch record or None.
    """
    if not is_cross_branch_candidate(body, branch_name):
        return None
    # Append rather than upsert — pending should accumulate candidates, not
    # overwrite.
    entry = f"- {now_iso()[:10]}: {body.strip().splitlines()[0][:160]}"
    # Keep full text as a nested detail so graduate has the original.
    full = f"{entry}\n  - 原文: {body.strip().replace(chr(10), ' / ')[:500]}"
    append_to_section(paths["pending_promotion"], "候选条目", full)
    return {"file": "branch/pending-promotion.md", "section": "候选条目", "mode": "append-candidate"}


# ---------------------------------------------------------------------------
# Content loaders
# ---------------------------------------------------------------------------

def _load_optional_text(value, file_path=None):
    if value:
        stripped = value.strip()
        return stripped or None
    if file_path:
        return (Path(file_path).read_text(encoding="utf-8").strip()) or None
    return None


def _load_json_payload(value, file_path=None):
    if value:
        return json.loads(value)
    if file_path:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    raise RuntimeError("one of --json / --json-file is required")


def _decision_content(item):
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return decision_body(item)
    return ""


def _load_free_content(args):
    """Load content for single-kind writes. Supports:
      --content / --content-file : inline body
      --summary / --summary-file : session-derived summary (prefixed)
      --user-input / --user-input-file : user turn (prefixed)
    When both summary and user-input are given, they're combined; inline
    content becomes an additional 补充备注 section.
    """
    inline = _load_optional_text(args.content, getattr(args, "content_file", None))
    summary = _load_optional_text(getattr(args, "summary", None), getattr(args, "summary_file", None))
    user_input = _load_optional_text(getattr(args, "user_input", None), getattr(args, "user_input_file", None))

    if summary or user_input:
        blocks = []
        if user_input:
            blocks.append(f"### 用户这次输入\n\n{user_input}")
        if summary:
            blocks.append(f"### 基于当前会话整理\n\n{summary}")
        if inline:
            blocks.append(f"### 补充备注\n\n{inline}")
        return "\n\n".join(blocks), "session+input"
    if inline:
        return inline, "content-only"
    return None, None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def command_show(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    payload = {
        "repo_root": str(repo_root),
        "repo_key": repo_key,
        "branch": branch_name,
        "branch_key": branch_key,
        "storage_root": str(storage_root),
        "repo_dir": str(repo_dir),
        "branch_dir": str(branch_dir),
        "setup_completed": get_setup_completed(paths["manifest"]),
        "files": {key: str(value) for key, value in paths.items()},
        "missing_or_placeholder": list_missing_docs(paths),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_suggest_kind(args):
    content = _load_optional_text(args.content, args.content_file)
    if not content:
        raise RuntimeError("one of --content / --content-file is required")
    already_setup = bool(args.already_setup)
    kind = classify_content(content, already_setup=already_setup)
    spec = KIND_MAP.get(kind, {"file": "unsorted", "section": "待分类"})
    branch_name = args.branch_name or ""
    cross_branch = is_cross_branch_candidate(content, branch_name) if branch_name else None
    print(
        json.dumps(
            {
                "kind": kind,
                "target_file": _label(spec["file"]),
                "section": spec["section"],
                "cross_branch_candidate": cross_branch,
                "already_setup": already_setup,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_classify(args):
    # Like suggest-kind but always resolves through the lazy-init pipeline so
    # the caller also gets the computed paths (useful for the capture skill's
    # inner loop).
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    content = _load_optional_text(args.content, args.content_file)
    if not content:
        raise RuntimeError("one of --content / --content-file is required")
    already_setup = get_setup_completed(paths["manifest"])
    kind = classify_content(content, already_setup=already_setup)
    spec = KIND_MAP.get(kind, KIND_MAP["unsorted"])
    cross_branch = is_cross_branch_candidate(content, branch_name or "")
    print(
        json.dumps(
            {
                "branch": branch_name,
                "already_setup": already_setup,
                "kind": kind,
                "target_file": _label(spec["file"]),
                "section": spec["section"],
                "cross_branch_candidate": cross_branch,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_record(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    worktree_writeback = _worktree_writeback_context(repo_root, repo_dir, paths, branch_name)
    already_setup = get_setup_completed(paths["manifest"])
    force = bool(getattr(args, "force", False))

    # Hidden --dedup-threshold override. Validated here so the error surface
    # is consistent with other record-time failures (RuntimeError → exit 1 +
    # error JSON via main's try/except). None means "use similarity_check
    # default" — the production path.
    dedup_threshold = getattr(args, "dedup_threshold", None)
    if dedup_threshold is not None:
        if not isinstance(dedup_threshold, (int, float)):
            raise RuntimeError(
                f"--dedup-threshold must be a number, got {type(dedup_threshold).__name__}"
            )
        if not (0.0 < dedup_threshold <= 1.0):
            raise RuntimeError(
                f"--dedup-threshold must be in (0.0, 1.0], got {dedup_threshold}"
            )

    touched = []
    dedup_blocked = []  # entries that were stopped by dedup check (batch mode)
    mode_used = None

    # Mode 1: batch session payload (merges old sync record-session).
    payload = None
    if args.summary_json:
        payload = json.loads(args.summary_json)
    elif args.summary_file:
        payload = json.loads(Path(args.summary_file).read_text(encoding="utf-8"))

    if payload:
        mode_used = "session-payload"
        title = payload.get("title") or "checkpoint"

        def _write_or_block(kind, body, title_override=None, source_label=None):
            """Run dedup check; if blocked, push onto dedup_blocked; else write.
            `source_label` is a free-form tag (e.g. "decisions[0]") so the
            caller can correlate the blocked entry back to its payload slot.
            """
            hint = _check_dedup_for_kind(
                paths, kind, body, force=force,
                title_override=title_override, threshold=dedup_threshold,
            )
            if hint is not None:
                dedup_blocked.append({
                    "source": source_label,
                    "kind": kind,
                    "content_preview": _first_nonempty_line(body)[:_SIM_PREVIEW_LEN],
                    "dedup_hint": hint,
                })
                return None
            rec = _write_one(paths, kind, body, title_override=title_override)
            _maybe_worktree_writeback(
                worktree_writeback,
                kind,
                body,
                title_override=title_override,
                force=force,
                dedup_threshold=dedup_threshold,
                summary=source_label,
            )
            return rec

        # Worker-facing current-state fields. Keep these as plain text because
        # progress/next sections are snapshots, not bullet-list accumulators.
        for key, kind in SESSION_DIRECT_PAYLOAD_MAP:
            items = normalize_items(payload.get(key))
            if not items:
                continue
            rec = _write_or_block(kind, "\n".join(items), source_label=f"payload[{key}]")
            if rec is not None:
                touched.append(rec)

        # Simple scalar mappings.
        for key, kind, subsection_title in SESSION_PAYLOAD_MAP + SESSION_EXTRA_PAYLOAD_MAP:
            items = normalize_items(payload.get(key))
            if not items:
                continue
            body = bullets(items) if not subsection_title else f"### {subsection_title}\n\n{bullets(items)}"
            rec = _write_or_block(kind, body, source_label=f"payload[{key}]")
            if rec is not None:
                touched.append(rec)
                # Cross-branch staging — apply to each item separately for best recall.
                for item in items:
                    pending = _maybe_stage_pending(paths, item, branch_name or "")
                    if pending:
                        touched.append(pending)

        # Risks also go into the 后续继续前要注意 section (same as v1 behavior).
        risk_items = normalize_items(payload.get("risks"))
        if risk_items:
            rec = _write_or_block(
                "risk",
                bullets(risk_items),
                title_override="后续继续前要注意",
                source_label="payload[risks→后续继续前要注意]",
            )
            if rec is not None:
                touched.append(rec)

        # Structured decisions (decision|summary/reason/impact trios).
        decision_payload_items = [
            item for item in (payload.get("decisions") or []) if _decision_summary(item)
        ]
        decision_items = [decision_body(item) for item in decision_payload_items]
        if decision_items:
            body = "\n\n".join(decision_items)
            rec = _write_or_block("decision", body, source_label="payload[decisions]")
            if rec is not None:
                touched.append(rec)
                for item in decision_payload_items:
                    pending = _maybe_stage_pending(paths, _decision_summary(item), branch_name or "")
                    if pending:
                        touched.append(pending)

        shared_decision_payload_items = [
            item for item in (payload.get("shared_decisions") or []) if _decision_summary(item)
        ]
        shared_decision_items = [decision_body(item) for item in shared_decision_payload_items]
        if shared_decision_items:
            body = "\n\n".join(shared_decision_items)
            rec = _write_or_block("shared-decision", body, source_label="payload[shared_decisions]")
            if rec is not None:
                touched.append(rec)

        extra_manifest = {"last_session_sync_title": title, "last_session_sync_mode": "capture-session"}

    else:
        # Mode 2/3: free-form content with kind (explicit or auto-classify).
        content, update_mode = _load_free_content(args)
        if content is None:
            raise RuntimeError(
                "provide one of: --summary-json/-file (batch), --kind+--content (targeted), "
                "or --auto --content (heuristic classify)"
            )

        kind = args.kind.lower() if args.kind else None
        if args.auto or not kind:
            classified = classify_content(content, already_setup=already_setup)
            kind = kind or classified
            mode_used = f"auto-classified-{classified}"
        else:
            mode_used = "explicit-kind"

        # Dedup pre-check: if blocked, do not write — return hint and exit
        # with code 2 so callers can distinguish "blocked, take action" from
        # "actual error" (exit 1).
        hint = _check_dedup_for_kind(
            paths, kind, content, force=force,
            title_override=args.title, threshold=dedup_threshold,
        )
        if hint is not None:
            print(json.dumps({
                "blocked": True,
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "storage_root": str(storage_root),
                "branch_dir": str(branch_dir),
                "mode": mode_used,
                "kind": kind,
                "dedup_hint": hint,
            }, ensure_ascii=False, indent=2))
            return 2

        rec = _write_one(paths, kind, content, title_override=args.title)
        touched.append(rec)
        _maybe_worktree_writeback(
            worktree_writeback,
            kind,
            content,
            title_override=args.title,
            force=force,
            dedup_threshold=dedup_threshold,
            summary=content,
        )
        rec = _maybe_stage_pending(paths, content, branch_name or "")
        if rec:
            touched.append(rec)

        extra_manifest = {"last_capture_kind": kind, "last_capture_mode": mode_used, "last_capture_update_mode": update_mode}

    # Manifest bookkeeping.
    manifest = read_json(paths["manifest"])
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": now_iso(),
            "last_seen_head": get_head_commit(repo_root) if repo_root else None,
        }
    )
    manifest.update(extra_manifest)
    manifest["last_capture_targets"] = touched
    write_json(paths["manifest"], manifest)

    repo_manifest = read_json(paths["repo_manifest"])
    repo_manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "storage_root": str(storage_root),
            "updated_at": manifest["updated_at"],
            "last_seen_branch": branch_name,
            "last_seen_head": manifest["last_seen_head"],
        }
    )
    write_json(paths["repo_manifest"], repo_manifest)

    # Event log: one row per record call. Skip when nothing was actually
    # written (e.g. batch mode where every entry was dedup-blocked) — the log
    # would be a misleading "we did something" signal.
    if touched:
        if payload:
            log_kind = "session-payload"
            log_summary = payload.get("title") or f"{len(touched)} target(s)"
        else:
            log_kind = manifest.get("last_capture_kind") or "auto"
            log_summary = (
                content if 'content' in locals() and content else f"{len(touched)} target(s)"
            )
        extra = []
        if dedup_blocked:
            extra.append(("blocked", len(dedup_blocked)))
        _emit_capture_log(
            paths,
            action="capture",
            kind_label=log_kind,
            summary=log_summary,
            touched=touched,
            extra_details=extra,
        )

    output = {
        "repo_root": str(repo_root),
        "repo_key": repo_key,
        "branch": branch_name,
        "storage_root": str(storage_root),
        "repo_dir": str(repo_dir),
        "branch_dir": str(branch_dir),
        "mode": mode_used,
        "setup_completed": already_setup,
        "touched_targets": touched,
        "updated_at": manifest["updated_at"],
    }
    writeback_output = _finalize_worktree_writeback(worktree_writeback, repo_root, repo_key, storage_root)
    if writeback_output:
        output["worktree_writeback"] = writeback_output
    elif worktree_writeback and worktree_writeback.get("attempted") and worktree_writeback.get("skipped"):
        output["worktree_writeback"] = {
            "source": worktree_writeback.get("source"),
            "source_dir": worktree_writeback.get("source_dir"),
            "touched": [],
            "skipped": worktree_writeback["skipped"],
        }
    if dedup_blocked:
        # Batch mode: surface blocked items but still report what was written.
        output["dedup_blocked"] = dedup_blocked
    print(json.dumps(output, ensure_ascii=False, indent=2))
    # Batch with any blocked entries → exit 2 so caller knows to handle them.
    return 2 if dedup_blocked else 0


def command_rewrite_entry(args):
    """Replace a single existing entry by id with new content.

    Why this is a subcommand rather than a flag on `record`: rewrite-entry's
    inputs (id + new content) don't overlap with record's classify/auto/
    session-payload surface, and keeping it separate makes the dedup-driven
    "update existing instead of append" workflow read clearly in CLI logs.
    """
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )

    eid = (args.id or "").strip()
    new_text = _load_optional_text(args.content, args.content_file)
    try:
        mutation = _entry_mutation(paths, eid, new_text=new_text)
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    # Manifest bookkeeping (same shape as record).
    manifest = read_json(paths["manifest"])
    updated_at = now_iso()
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": updated_at,
            "last_seen_head": get_head_commit(repo_root) if repo_root else None,
            "last_capture_kind": None,
            "last_capture_mode": "rewrite-entry",
            "last_capture_update_mode": "rewrite-entry",
            "last_capture_targets": [{
                "file": mutation["file"],
                "section": mutation["section"],
                "mode": "rewrite-entry",
                "id": eid,
            }],
        }
    )
    write_json(paths["manifest"], manifest)

    new_first_line = _first_nonempty_line(_strip_bullet_prefix(_first_nonempty_line(new_text)))

    _emit_capture_log(
        paths,
        action="rewrite-entry",
        kind_label=mutation["file_key"],
        summary=new_first_line,
        touched=[{"file": mutation["file"], "section": mutation["section"], "mode": "rewrite-entry"}],
        extra_details=[("id", eid), ("previous", mutation["previous_first_line"])],
    )

    print(json.dumps({
        "mode": "rewrite-entry",
        "id": eid,
        "file": mutation["file"],
        "section": mutation["section"],
        "previous_first_line": mutation["previous_first_line"],
        "new_first_line": new_first_line,
        "updated_at": updated_at,
    }, ensure_ascii=False, indent=2))
    return 0


def command_delete_entry(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    eid = (args.id or "").strip()
    try:
        mutation = _entry_mutation(paths, eid, delete=True)
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    manifest = read_json(paths["manifest"])
    updated_at = now_iso()
    manifest.update({
        "repo_root": str(repo_root),
        "repo_key": repo_key,
        "branch": branch_name,
        "branch_key": branch_key,
        "storage_root": str(storage_root),
        "updated_at": updated_at,
        "last_seen_head": get_head_commit(repo_root) if repo_root else None,
        "last_capture_kind": None,
        "last_capture_mode": "delete-entry",
        "last_capture_update_mode": "delete-entry",
        "last_capture_targets": [{
            "file": mutation["file"],
            "section": mutation["section"],
            "mode": "delete-entry",
            "id": eid,
        }],
    })
    write_json(paths["manifest"], manifest)

    _emit_capture_log(
        paths,
        action="delete-entry",
        kind_label=mutation["file_key"],
        summary=mutation["previous_first_line"] or eid,
        touched=[{"file": mutation["file"], "section": mutation["section"], "mode": "delete-entry"}],
        extra_details=[("id", eid)],
    )

    print(json.dumps({
        "mode": "delete-entry",
        "id": eid,
        "file": mutation["file"],
        "section": mutation["section"],
        "deleted_first_line": mutation["previous_first_line"],
        "updated_at": updated_at,
    }, ensure_ascii=False, indent=2))
    return 0


def command_apply_summary_output(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    worktree_writeback = _worktree_writeback_context(repo_root, repo_dir, paths, branch_name)
    payload = _load_json_payload(args.json, args.json_file)
    if not isinstance(payload, dict):
        raise RuntimeError("summary output must be a JSON object")

    touched = []
    actions = []
    dedup_blocked = []
    force = bool(getattr(args, "force", False))

    # Preflight destructive/targeted edits before any write happens. This
    # avoids a partial upsert/append landing before a later bad entry id fails.
    for item in payload.get("rewrites") or []:
        _validate_entry_reference(paths, item.get("id"), require_content=item.get("content"))
    for item in payload.get("deletes") or []:
        _validate_entry_reference(paths, item.get("id"))

    def add_write(kind, content, *, mode_override=None, source=None):
        if not kind:
            return
        if kind not in KIND_MAP:
            raise RuntimeError(f"unsupported kind in summary output: {kind}")
        body = "\n".join(normalize_items(content))
        if not body:
            return
        mode = mode_override or KIND_MAP[kind].get("default_mode", "upsert")
        if mode == "append":
            hint = _check_dedup_for_kind(paths, kind, body, force=force)
            if hint is not None:
                dedup_blocked.append({
                    "source": source,
                    "kind": kind,
                    "content_preview": _first_nonempty_line(body)[:_SIM_PREVIEW_LEN],
                    "dedup_hint": hint,
                })
                return
        rec = _write_one(paths, kind, body, mode_override=mode_override)
        touched.append(rec)
        _maybe_worktree_writeback(
            worktree_writeback,
            kind,
            body,
            mode_override=mode_override,
            force=force,
            summary=source,
        )
        actions.append({
            "op": mode,
            "kind": kind,
            "source": source,
            "target": rec,
        })

    # Convenience summary-output fields. These mirror --summary-json but keep
    # execution in code instead of making the agent compose CLI calls.
    for item in payload.get("decisions") or []:
        add_write("decision", _decision_content(item), source="decisions")
    for item in payload.get("risks") or []:
        add_write("risk", item, source="risks")
    for item in payload.get("glossary") or []:
        add_write("glossary", item, source="glossary")
    for item in payload.get("shared_decisions") or []:
        add_write("shared-decision", _decision_content(item), source="shared_decisions")
    for item in payload.get("shared_context") or []:
        add_write("shared-context", item, source="shared_context")
    for item in payload.get("shared_sources") or []:
        add_write("shared-source", item, source="shared_sources")

    file_map = payload.get("file_map")
    if isinstance(file_map, list) and file_map:
        lines = []
        for entry in file_map:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label", "").strip()
            paths_val = entry.get("paths") or ([entry["path"]] if entry.get("path") else [])
            if not label or not paths_val:
                continue
            joined = ", ".join(f"`{p}`" for p in paths_val)
            lines.append(f"- {label}: {joined}")
        if lines:
            add_write("filemap", "\n".join(lines), source="file_map")

    # Explicit patch operations.
    for item in payload.get("upserts") or []:
        add_write(item.get("kind"), item.get("content"), mode_override="upsert", source="upserts")
    for item in payload.get("appends") or []:
        add_write(item.get("kind"), item.get("content"), mode_override="append", source="appends")
    for item in payload.get("rewrites") or []:
        mutation = _entry_mutation(paths, item.get("id"), new_text=item.get("content"))
        touched.append({"file": mutation["file"], "section": mutation["section"], "mode": "rewrite-entry"})
        actions.append({
            "op": "rewrite-entry",
            "id": mutation["id"],
            "file": mutation["file"],
            "section": mutation["section"],
            "previous_first_line": mutation["previous_first_line"],
            "reason": item.get("reason"),
        })
    for item in payload.get("deletes") or []:
        mutation = _entry_mutation(paths, item.get("id"), delete=True)
        touched.append({"file": mutation["file"], "section": mutation["section"], "mode": "delete-entry"})
        actions.append({
            "op": "delete-entry",
            "id": mutation["id"],
            "file": mutation["file"],
            "section": mutation["section"],
            "deleted_first_line": mutation["previous_first_line"],
            "reason": item.get("reason"),
        })

    updated_at = now_iso()

    if touched:
        manifest = read_json(paths["manifest"])
        manifest.update({
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": updated_at,
            "last_seen_head": get_head_commit(repo_root) if repo_root else None,
            "last_capture_kind": "summary-output",
            "last_capture_mode": "apply-summary-output",
            "last_capture_update_mode": "apply-summary-output",
            "last_capture_targets": touched,
        })
        write_json(paths["manifest"], manifest)

        repo_manifest = read_json(paths["repo_manifest"])
        repo_manifest.update({
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "storage_root": str(storage_root),
            "updated_at": updated_at,
            "last_seen_branch": branch_name,
            "last_seen_head": manifest["last_seen_head"],
        })
        write_json(paths["repo_manifest"], repo_manifest)

        _emit_capture_log(
            paths,
            action="apply-summary-output",
            kind_label="summary-output",
            summary=payload.get("title") or f"{len(touched)} target(s)",
            touched=touched,
        )

    output = {
        "mode": "apply-summary-output",
        "repo_root": str(repo_root),
        "repo_key": repo_key,
        "branch": branch_name,
        "touched_targets": touched,
        "actions": actions,
        "skip_reason": payload.get("skip_reason") if not touched else None,
        "updated_at": updated_at,
    }
    writeback_output = _finalize_worktree_writeback(worktree_writeback, repo_root, repo_key, storage_root)
    if writeback_output:
        output["worktree_writeback"] = writeback_output
    elif worktree_writeback and worktree_writeback.get("attempted") and worktree_writeback.get("skipped"):
        output["worktree_writeback"] = {
            "source": worktree_writeback.get("source"),
            "source_dir": worktree_writeback.get("source_dir"),
            "touched": [],
            "skipped": worktree_writeback["skipped"],
        }
    if dedup_blocked:
        output["dedup_blocked"] = dedup_blocked
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def command_sync_working_tree(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    if branch_name is None:
        # no-git mode — no git facts to derive.
        print(json.dumps({"mode": "no-git", "skipped": True}, ensure_ascii=False))
        return 0

    prior_manifest = read_json(paths["manifest"]) or {}
    facts = collect_git_facts(repo_root, branch_name, storage_root)
    all_paths = sorted(set(
        facts["working_tree_files"]
        + facts["staged_files"]
        + facts["untracked_files"]
        + facts["recent_commit_files"]
    ))
    facts["focus_areas"] = merged_focus_areas(all_paths, prior_manifest.get("focus_areas") or [])
    sync_progress(paths, facts)

    manifest = read_json(paths["manifest"])
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": facts["updated_at"],
            "last_seen_head": facts["last_seen_head"],
            "default_base": facts["default_base"],
            "scope_summary": facts["scope_summary"],
            "focus_areas": facts["focus_areas"],
        }
    )
    write_json(paths["manifest"], manifest)

    repo_manifest = read_json(paths["repo_manifest"])
    repo_manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "storage_root": str(storage_root),
            "updated_at": facts["updated_at"],
            "last_seen_branch": branch_name,
            "last_seen_head": facts["last_seen_head"],
            "default_base": facts["default_base"],
        }
    )
    write_json(paths["repo_manifest"], repo_manifest)

    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "storage_root": str(storage_root),
                "repo_dir": str(repo_dir),
                "branch_dir": str(branch_dir),
                "mode": "sync-working-tree",
                "focus_areas": manifest["focus_areas"],
                "files_considered": len(all_paths),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_record_head(args):
    repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir, paths = ensure_branch_paths_exist(
        args.repo, args.context_dir, args.branch
    )
    manifest = read_json(paths["manifest"])
    head = args.commit or (get_head_commit(repo_root) if repo_root else None)
    manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "branch": branch_name,
            "branch_key": branch_key,
            "storage_root": str(storage_root),
            "updated_at": now_iso(),
            "last_seen_head": head,
        }
    )
    write_json(paths["manifest"], manifest)

    repo_manifest = read_json(paths["repo_manifest"])
    repo_manifest.update(
        {
            "repo_root": str(repo_root),
            "repo_key": repo_key,
            "storage_root": str(storage_root),
            "updated_at": manifest["updated_at"],
            "last_seen_branch": branch_name,
            "last_seen_head": head,
        }
    )
    write_json(paths["repo_manifest"], repo_manifest)

    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "storage_root": str(storage_root),
                "repo_dir": str(repo_dir),
                "branch_dir": str(branch_dir),
                "mode": "record-head",
                "last_seen_head": head,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _add_common_args(parser):
    parser.add_argument("--repo", default=".", help="Path inside the target Git repository")
    parser.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-memory/repos")
    parser.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")


def main():
    parser = argparse.ArgumentParser(
        description="Unified write entrypoint for dev-memory repo+branch memory (merges old sync + update).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("show", help="Show current paths + missing docs")
    _add_common_args(show)

    suggest = subparsers.add_parser("suggest-kind", help="Dry-run classify a content string")
    suggest.add_argument("--content", help="Inline content to classify")
    suggest.add_argument("--content-file", help="File containing content to classify")
    suggest.add_argument("--already-setup", action="store_true", help="Hint that setup is completed (shifts default from unsorted to progress)")
    suggest.add_argument("--branch-name", help="Branch name for cross-branch candidate check")

    classify = subparsers.add_parser("classify", help="Classify content against the current branch context")
    _add_common_args(classify)
    classify.add_argument("--content", help="Inline content to classify")
    classify.add_argument("--content-file", help="File containing content to classify")

    record = subparsers.add_parser("record", help="Write content into one or more sections")
    _add_common_args(record)
    record.add_argument("--kind", help=f"Explicit kind. One of: {', '.join(sorted(KIND_MAP.keys()))}")
    record.add_argument("--auto", action="store_true", help="Ignore --kind; run classifier on content")
    record.add_argument("--title", help="Override the default section title")
    record.add_argument("--content", help="Inline markdown content")
    record.add_argument("--content-file", help="File containing markdown content")
    record.add_argument("--summary", help="Session-derived summary")
    record.add_argument("--summary-file", help="File with a session-derived summary")
    record.add_argument("--user-input", help="Latest user input to store alongside summary")
    record.add_argument("--user-input-file", help="File containing the latest user input")
    record.add_argument("--summary-json", help="Inline JSON session payload (batch record)")
    record.add_argument(
        "--force",
        action="store_true",
        help="Skip dedup check and append unconditionally (use after reviewing matches[] in a prior blocked response)",
    )
    # Hidden debugging/experiment flag: override similarity_check's default
    # 0.7 threshold. Range (0.0, 1.0]; outside that command_record raises
    # → exit 1 + error JSON. Not in --help on purpose (argparse.SUPPRESS) —
    # production callers shouldn't need to touch this.
    record.add_argument(
        "--dedup-threshold",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )

    rewrite = subparsers.add_parser(
        "rewrite-entry",
        help="Replace an existing entry by id with new content (use after dedup hint suggests update_existing)",
    )
    _add_common_args(rewrite)
    rewrite.add_argument("--id", required=True, help="Entry id in the form <file_key>::<section_idx>::<entry_idx>")
    rewrite.add_argument("--content", help="Inline markdown content to replace the entry with")
    rewrite.add_argument("--content-file", help="File containing replacement markdown content")

    delete = subparsers.add_parser(
        "delete-entry",
        help="Delete an existing entry by id",
    )
    _add_common_args(delete)
    delete.add_argument("--id", required=True, help="Entry id in the form <file_key>::<section_idx>::<entry_idx>")

    apply_summary = subparsers.add_parser(
        "apply-summary-output",
        help="Apply a structured summary-output JSON patch",
    )
    _add_common_args(apply_summary)
    apply_summary.add_argument("--json", help="Inline summary-output JSON")
    apply_summary.add_argument("--json-file", help="File containing summary-output JSON")
    apply_summary.add_argument("--force", action="store_true", help="Skip dedup checks for append operations")

    sync = subparsers.add_parser("sync-working-tree", help="Refresh progress.md auto-sync block from git facts")
    _add_common_args(sync)

    head = subparsers.add_parser("record-head", help="Stamp last_seen_head onto branch+repo manifests")
    _add_common_args(head)
    head.add_argument("--commit", help="Explicit commit sha to record (defaults to HEAD)")

    args = parser.parse_args()
    try:
        handlers = {
            "show": command_show,
            "suggest-kind": command_suggest_kind,
            "classify": command_classify,
            "record": command_record,
            "rewrite-entry": command_rewrite_entry,
            "delete-entry": command_delete_entry,
            "apply-summary-output": command_apply_summary_output,
            "sync-working-tree": command_sync_working_tree,
            "record-head": command_record_head,
        }
        return handlers[args.command](args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
