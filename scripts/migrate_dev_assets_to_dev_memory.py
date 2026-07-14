#!/usr/bin/env python3
"""
One-shot user-side migration for the rename @xluos/dev-assets-cli → dev-memory-cli.

What it does (idempotent, with --dry-run):

  1) Move ~/.dev-assets/repos -> ~/.dev-memory/repos (keeps existing ~/.dev-memory if present)
  2) Rename .dev-assets-id markers -> .dev-memory-id (in well-known locations + scan paths)
  3) For every git repo passed via --scan: rename git config keys
     dev-assets.{root,dir} -> dev-memory.{root,dir}
  4) Recreate the read-skill symlink and remove retired write/maintenance
     skill symlinks under ~/.claude/skills/ and ~/.agents/skills/
  5) Rewrite hook commands "npx dev-assets ..." -> "npx dev-memory-cli ..." in:
        ~/.claude/settings.json
        ~/.codex/hooks.json
        plus any --settings <path> the user passes

Usage:
    python3 scripts/migrate_dev_assets_to_dev_memory.py --dry-run
    python3 scripts/migrate_dev_assets_to_dev_memory.py --apply

Optional:
    --scan PATH  (repeatable)  Extra dirs to scan for .dev-assets-id markers and
                               git repos that may have dev-assets.* config keys
                               set. Defaults: $HOME, common AIWorkspace roots.
    --settings PATH (repeatable)  Extra settings.json files to rewrite hooks in.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


HOME = Path.home()
OLD_ROOT = HOME / ".dev-assets"
NEW_ROOT = HOME / ".dev-memory"
OLD_REPOS = OLD_ROOT / "repos"
NEW_REPOS = NEW_ROOT / "repos"


# ---------- helpers ----------

def log(msg, *, dry):
    prefix = "[dry-run]" if dry else "[apply]"
    print(f"{prefix} {msg}")


def warn(msg):
    print(f"[warn] {msg}", file=sys.stderr)


def run(cmd, *, cwd=None, check=False, capture=True):
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        capture_output=capture,
        text=True,
    )


# ---------- 1. storage root ----------

def migrate_storage_root(dry):
    if not OLD_REPOS.exists():
        log("storage root: ~/.dev-assets/repos not present, skip", dry=dry)
        return

    if NEW_REPOS.exists():
        warn(
            f"both ~/.dev-assets/repos and ~/.dev-memory/repos exist; will not "
            f"merge automatically. Inspect manually."
        )
        return

    log(f"move {OLD_REPOS} -> {NEW_REPOS}", dry=dry)
    if dry:
        return
    NEW_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(OLD_REPOS), str(NEW_REPOS))
    # If old root has nothing else, drop it.
    try:
        OLD_ROOT.rmdir()
    except OSError:
        pass


# ---------- 2. id markers ----------

DEFAULT_SCAN_ROOTS = [
    HOME,
    HOME / "Documents",
    HOME / "Documents" / "AIWorkspace",
]


def find_legacy_id_files(scan_roots):
    """Find .dev-assets-id files. We don't recurse arbitrarily — only scan a
    reasonable set of roots to a shallow depth. For deeper coverage user can
    pass --scan."""
    seen = set()
    found = []
    for root in scan_roots:
        if not root.exists():
            continue
        try:
            # use find for speed and stable depth limit
            result = run(
                ["find", str(root), "-maxdepth", "5", "-name", ".dev-assets-id", "-not", "-path", "*/node_modules/*"],
                check=False,
            )
        except FileNotFoundError:
            continue
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            p = Path(line)
            if p in seen:
                continue
            seen.add(p)
            found.append(p)
    return found


def migrate_id_markers(dry, scan_roots):
    found = find_legacy_id_files(scan_roots)
    if not found:
        log("id markers: no .dev-assets-id files found", dry=dry)
        return
    for src in found:
        dst = src.with_name(".dev-memory-id")
        if dst.exists():
            log(f"id marker: {dst} already exists, leaving {src} in place", dry=dry)
            continue
        log(f"rename {src} -> {dst}", dry=dry)
        if not dry:
            src.rename(dst)


# ---------- 3. git config keys ----------

def find_git_repos(scan_roots, depth=4):
    repos = set()
    for root in scan_roots:
        if not root.exists():
            continue
        result = run(
            ["find", str(root), "-maxdepth", str(depth), "-type", "d", "-name", ".git", "-not", "-path", "*/node_modules/*"],
            check=False,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            repos.add(Path(line).parent)
    return sorted(repos)


def _rewrite_config_value(value):
    """Map legacy dev-assets paths to dev-memory paths inside config values."""
    if not value:
        return value
    new = value
    new = new.replace(str(OLD_REPOS), str(NEW_REPOS))
    new = new.replace(str(OLD_ROOT), str(NEW_ROOT))
    if new == ".dev-assets":
        new = ".dev-memory"
    return new


def migrate_git_config(dry, scan_roots):
    repos = find_git_repos(scan_roots)
    touched = 0
    # Pass 1: rename legacy dev-assets.* keys to dev-memory.* (preserving value)
    # Pass 2: walk all dev-memory.* keys (now including freshly renamed) and
    #         rewrite values that still point at the old storage location.
    for repo in repos:
        legacy = run(
            ["git", "-C", str(repo), "config", "--local", "--get-regexp", r"^dev-assets\."],
            check=False,
        )
        if legacy.returncode == 0 and legacy.stdout.strip():
            for line in legacy.stdout.splitlines():
                if not line.strip():
                    continue
                key, _, value = line.partition(" ")
                new_key = key.replace("dev-assets.", "dev-memory.", 1)
                log(f"git config @ {repo}: rename {key} -> {new_key}", dry=dry)
                if not dry:
                    run(["git", "-C", str(repo), "config", "--local", new_key, value], check=False)
                    run(["git", "-C", str(repo), "config", "--local", "--unset", key], check=False)
                touched += 1

        current = run(
            ["git", "-C", str(repo), "config", "--local", "--get-regexp", r"^dev-memory\."],
            check=False,
        )
        if current.returncode != 0 or not current.stdout.strip():
            continue
        for line in current.stdout.splitlines():
            if not line.strip():
                continue
            key, _, value = line.partition(" ")
            new_value = _rewrite_config_value(value)
            if new_value == value:
                continue
            log(f"git config @ {repo}: set {key}={new_value} (was {value})", dry=dry)
            if not dry:
                run(["git", "-C", str(repo), "config", "--local", key, new_value], check=False)
            touched += 1

    if touched == 0:
        log("git config: nothing to migrate in scanned repos", dry=dry)


# ---------- 4. skill symlinks ----------

SKILL_LINK_DIRS = [
    HOME / ".claude" / "skills",
    HOME / ".agents" / "skills",
]

# These are the rename pairs for symlinks. Source name -> new name.
SKILL_RENAMES = [
    ("dev-assets-context", "dev-memory-read"),
    ("dev-memory-context", "dev-memory-read"),
]

RETIRED_SKILL_NAMES = {
    "using-dev-assets", "using-dev-memory",
    "dev-assets-setup", "dev-memory-setup",
    "dev-assets-capture", "dev-memory-capture",
    "dev-assets-graduate", "dev-memory-graduate",
    "dev-assets-tidy", "dev-memory-tidy",
}


def migrate_skill_symlinks(dry):
    for skills_dir in SKILL_LINK_DIRS:
        if not skills_dir.exists():
            continue
        for retired_name in sorted(RETIRED_SKILL_NAMES):
            retired_link = skills_dir / retired_name
            if retired_link.is_symlink():
                log(f"remove retired skill symlink {retired_link}", dry=dry)
                if not dry:
                    retired_link.unlink()
        for old_name, new_name in SKILL_RENAMES:
            old_link = skills_dir / old_name
            new_link = skills_dir / new_name
            if not old_link.is_symlink():
                if old_link.exists():
                    warn(f"{old_link} exists but is not a symlink; skipping")
                continue

            target = os.readlink(str(old_link))
            # Rewrite target if it points at the old skill dir (renamed to new name).
            new_target = target
            for o, n in SKILL_RENAMES:
                if f"/skills/{o}" in target or target.endswith(f"/{o}") or target.endswith(f"/{o}/"):
                    new_target = target.replace(f"/{o}", f"/{n}")
                    break

            if new_link.is_symlink() or new_link.exists():
                log(f"symlink: {new_link} already exists, removing {old_link}", dry=dry)
                if not dry:
                    old_link.unlink()
                continue

            log(f"symlink {old_link} -> {new_link} (target {new_target})", dry=dry)
            if not dry:
                old_link.unlink()
                new_link.symlink_to(new_target)


# ---------- 5. hook command rewrites ----------

DEFAULT_SETTINGS_FILES = [
    HOME / ".claude" / "settings.json",
    HOME / ".codex" / "hooks.json",
]

CMD_PATTERNS = [
    (re.compile(r"\bnpx\s+dev-assets\b"), "npx dev-memory-cli"),
    (re.compile(r"\bnpx\s+@xluos/dev-assets-cli\b"), "npx dev-memory-cli"),
]

ID_PATTERNS = [
    (re.compile(r'"id"\s*:\s*"dev-assets:'), '"id": "dev-memory:'),
]


def rewrite_settings_text(text):
    new = text
    for pattern, repl in CMD_PATTERNS + ID_PATTERNS:
        new = pattern.sub(repl, new)
    return new


def migrate_hook_settings(dry, extra_files):
    targets = [Path(p) for p in DEFAULT_SETTINGS_FILES] + [Path(p) for p in extra_files]
    for path in targets:
        if not path.exists():
            log(f"settings: {path} not present, skip", dry=dry)
            continue
        original = path.read_text(encoding="utf-8")
        rewritten = rewrite_settings_text(original)
        if rewritten == original:
            log(f"settings: {path} no changes needed", dry=dry)
            continue

        # Validate JSON shape only if file looks like JSON (.json suffix).
        if path.suffix == ".json":
            try:
                json.loads(rewritten)
            except json.JSONDecodeError as exc:
                warn(f"refusing to write {path}: rewritten content is not valid JSON ({exc})")
                continue

        log(f"settings: rewrite hook commands/ids in {path}", dry=dry)
        if not dry:
            backup = path.with_suffix(path.suffix + f".devassets-bak-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
            backup.write_text(original, encoding="utf-8")
            path.write_text(rewritten, encoding="utf-8")
            log(f"  backup written to {backup}", dry=False)


# ---------- entrypoint ----------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print actions without changing anything")
    mode.add_argument("--apply", action="store_true", help="actually perform the migration")
    parser.add_argument("--scan", action="append", default=[], help="extra dir to scan for id markers / git repos")
    parser.add_argument("--settings", action="append", default=[], help="extra settings file to rewrite hooks in")
    parser.add_argument("--skip-storage", action="store_true")
    parser.add_argument("--skip-id-markers", action="store_true")
    parser.add_argument("--skip-git-config", action="store_true")
    parser.add_argument("--skip-symlinks", action="store_true")
    parser.add_argument("--skip-settings", action="store_true")
    args = parser.parse_args()

    dry = bool(args.dry_run)
    scan_roots = list({*DEFAULT_SCAN_ROOTS, *(Path(p).expanduser().resolve() for p in args.scan)})

    print(f"=== migrate dev-assets -> dev-memory  (mode={'dry-run' if dry else 'apply'}) ===")

    if not args.skip_storage:
        migrate_storage_root(dry)
    if not args.skip_id_markers:
        migrate_id_markers(dry, scan_roots)
    if not args.skip_git_config:
        migrate_git_config(dry, scan_roots)
    if not args.skip_symlinks:
        migrate_skill_symlinks(dry)
    if not args.skip_settings:
        migrate_hook_settings(dry, args.settings)

    print()
    if dry:
        print("dry-run complete. Re-run with --apply to perform the migration.")
    else:
        print("migration done. Verify hook still works by starting a fresh session.")


if __name__ == "__main__":
    main()
