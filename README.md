# Dev Asset Skill Suite

Branch-scoped development asset skills for Codex and similar agent runtimes.

This repository packages a small skill suite for maintaining requirement continuity on long-running feature branches:

- `using-dev-assets` — entry router for Git-repository development conversations
- `dev-assets-setup` — initialize `.dev-assets/<branch>/` and collect reusable requirement materials
- `dev-assets-context` — recover and refresh the current branch's saved development assets before coding
- `dev-assets-update` — actively add or correct requirement context in the current branch's asset files
- `dev-assets-sync` — treat commit-related moments as checkpoints and sync branch assets

Detailed guide:

- [docs/dev-asset-skill-suite-guide.md](docs/dev-asset-skill-suite-guide.md)

## Install with `npx skills`

List available skills:

```bash
npx skills add xluos/dev-asset-skill-suite --list
```

Install the whole suite for Codex globally:

```bash
npx skills add xluos/dev-asset-skill-suite --skill '*' -a codex -g -y
```

Install the whole suite for all detected agents:

```bash
npx skills add xluos/dev-asset-skill-suite --all -g -y
```

## Repository Layout

```text
skills/
  using-dev-assets/
  dev-assets-setup/
  dev-assets-context/
  dev-assets-update/
  dev-assets-sync/
lib/
  dev_asset_common.py
scripts/
  install_suite.py
```

## Notes

- The skills use `.dev-assets/<branch>/` as the branch-local asset directory.
- `dev-assets-update` is the manual ingestion entry for cases where the user proactively provides new requirement context mid-stream.
- `npx skills` does not need `scripts/install_suite.py`; the repository already follows standard skill discovery rules.
- `scripts/install_suite.py` remains useful for local symlink-based installs during development.
