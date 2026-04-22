#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

# Prefer the bundled lib/ when available so all skills share one source. The
# local skill-copy of dev_asset_common.py only kicks in for portable installs
# where lib/ is missing.
_lib = Path(__file__).resolve().parents[3] / "lib"
if _lib.exists() and str(_lib) not in sys.path:
    sys.path.insert(0, str(_lib))

from dev_asset_common import get_branch_paths, initialize_assets


def main():
    parser = argparse.ArgumentParser(description="Initialize repo+branch development assets in user-home storage.")
    parser.add_argument("--repo", default=".", help="Path inside the target Git repository")
    parser.add_argument("--context-dir", help="User-home storage root. Defaults to ~/.dev-assets/repos")
    parser.add_argument("--branch", help="Branch name. Defaults to the current checked-out branch")
    args = parser.parse_args()

    try:
        repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir = get_branch_paths(
            args.repo, args.context_dir, args.branch
        )
        paths = initialize_assets(repo_root, branch_name, branch_key, storage_root, repo_key, repo_dir, branch_dir)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "repo_key": repo_key,
                "branch": branch_name,
                "storage_root": str(storage_root),
                "repo_dir": str(repo_dir),
                "branch_dir": str(branch_dir),
                "files": {key: str(value) for key, value in paths.items()},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
