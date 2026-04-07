---
name: dev-asset-sync
description: Use when the user mentions commit, 提交, commit message, stage, staging, or preparing to commit on the current branch, so Codex treats this as a commit-adjacent checkpoint and syncs the branch's development assets.
---

# Dev Asset Sync

在用户提到“提交”相关行为时，刷新当前分支开发资产，并把提交记录沉淀下来。

**Announce at start:** `我先用 dev-asset-sync 同步当前分支的开发资产和提交记录。`

**Core principle:** 提交是资产沉淀检查点，不只是 Git 动作。

## Trigger Intent

典型触发语义：

- 帮我提交
- 准备 commit
- 生成 commit message
- 把这些改动提交掉
- 提交前同步一下
- stage 一下这些改动
- 提交之后记一下这次变更

## Workflow

### Step 1: Sync working tree first

先运行：

```bash
python3 /absolute/path/to/dev-asset-sync/scripts/dev_asset_sync.py sync-working-tree --repo <repo-path>
```

更新：

- `development.md`
- `manifest.json`

### Step 2: Record the latest commit if one exists

如果已经有新的 HEAD 提交，再运行：

```bash
python3 /absolute/path/to/dev-asset-sync/scripts/dev_asset_sync.py record-head --repo <repo-path>
```

把最新提交写入 `commits.md`。

### Step 3: Preserve meaningful conclusions

如果本轮开发已经形成明确结论、权衡、限制条件或风险口径，同时把这些内容写入 `decision-log.md`，不要只留在对话里。

## Commands

```bash
python3 /absolute/path/to/dev-asset-sync/scripts/dev_asset_sync.py sync-working-tree --repo <repo-path>
python3 /absolute/path/to/dev-asset-sync/scripts/dev_asset_sync.py record-head --repo <repo-path>
```

## Output Rules

- 不要只同步 Git 文件列表。
- 至少更新：
  - `development.md`
  - `manifest.json`
  - `commits.md`（如存在新提交）
- 如果没有新提交，明确说明“只完成工作区同步，未新增提交记录”。

## Always / Never

**Always:**

- 先同步 working tree，再记录 commit
- 在用户提到提交相关动作时优先想到本 skill
- 明确区分“工作区同步完成”和“提交记录完成”
- 有新提交时把 sha 和 message 落到 `commits.md`

**Never:**

- 只记录 commit，不刷新 `development.md`
- 在没有新提交时假装已经记录了提交
- 把提交动作当成纯 Git 操作，不沉淀任何开发资产

## Red Flags

- “先提交，资产回头再补”
- “反正 commit message 在 Git 里有，不用记”
- “这次只是小提交，不值得同步”

这些都说明你在绕过提交检查点。
