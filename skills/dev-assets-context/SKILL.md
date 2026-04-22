---
name: dev-assets-context
description: Use when starting work in any Git repository conversation on an existing branch, before code edits or repo exploration, when Codex should first recover the current branch's saved development memory, then pull repo-shared memory only if needed. In non-git working directories (no branch concept), this skill recovers the single repo-shared layer instead.
---

# Dev Assets Context

把当前仓库的 branch 记忆作为默认上下文入口恢复出来；repo 共享层只在需要时补读。

**Workspace mode：** cwd 是多 repo workspace 时，SessionStart 已自动注入 primary 仓库的完整记忆 + 其他仓库的简短概览。当需要切换焦点到非 primary 仓库补读完整记忆时，向脚本传递 `--repo <basename>` 明确指定。

**Announce at start:** 用一句简短的话说明将先恢复当前 branch 记忆，再按需补读 repo 共享记忆。

## Workflow

### Step 1: Locate repo + branch assets

先运行：

```bash
python3 /absolute/path/to/dev-assets-context/scripts/dev_asset_context.py show --repo <repo-path>
```

如果 branch 目录不存在，立即切到 `dev-assets-setup`。

### Step 2: Refresh lightweight Git-derived navigation

在继续工作前，可以轻量刷新 branch `development.md` 的 Git 自动区和 focus areas：

```bash
python3 /absolute/path/to/dev-assets-context/scripts/dev_asset_context.py sync --repo <repo-path>
```

### Step 3: Read in layers

默认先读：

- branch `overview.md`
- branch `development.md`
- branch `context.md`

只有在确实需要跨分支稳定背景时，再读：

- repo `overview.md`
- repo `context.md`

只有在需要原始事实时，才去读：

- branch `sources.md`
- repo `sources.md`

### Step 4: Call out gaps before acting

如果文件仍是模板占位或明显缺失：

- 先指出缺口
- 再决定是否切到 `dev-assets-update` 或 `dev-assets-setup`
- 不要把占位模板当成真实事实

## Commands

```bash
python3 /absolute/path/to/dev-assets-context/scripts/dev_asset_context.py show --repo <repo-path>
python3 /absolute/path/to/dev-assets-context/scripts/dev_asset_context.py sync --repo <repo-path>
```

## Always / Never

**Always:**

- 在 Git 仓库内继续已有分支工作时优先使用本 skill
- 在开始代码修改前先刷新一次 branch `development.md`
- 先读 branch，再决定是否补读 repo 共享层
- 发现缺失资产时明确说出来

**Never:**

- 不经读取资产目录就直接声称“我已经理解当前需求”
- 默认把 repo+branch 全部文件一次灌进上下文
- 把模板占位内容当成真实背景
- branch 目录缺失时跳过 setup 直接开始改代码
