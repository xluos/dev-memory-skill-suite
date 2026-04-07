---
name: dev-asset-context
description: Use when starting work in any Git repository conversation on an existing branch, before code edits, before repo exploration, or before clarifying implementation details, when Codex should first recover the current branch's saved development assets and identify missing requirement materials.
---

# Dev Asset Context

把当前分支目录下已经沉淀的开发资产读出来，作为默认上下文入口。

**Announce at start:** `我先用 dev-asset-context 恢复当前分支的开发资产上下文。`

**Core principle:** 先恢复分支资产，再进入编码、排查或补问细节。

如果你正在 Git 仓库里继续一个已有分支上的工作，这个 skill 应该优先于直接读代码、直接问问题、直接开始修改。

## Workflow

### Step 1: Locate branch assets

先确认当前目录位于 Git 仓库内，然后运行：

```bash
python3 /absolute/path/to/dev-asset-context/scripts/dev_asset_context.py show --repo <repo-path>
```

如果资产目录不存在，立即切换到 `dev-asset-setup`，不要直接开始编码。

### Step 2: Refresh Git-derived facts

在继续工作前刷新 `development.md` 的 Git 自动区：

```bash
python3 /absolute/path/to/dev-asset-context/scripts/dev_asset_context.py sync --repo <repo-path>
```

### Step 3: Read in layers, not all at once

默认先读：

- `overview.md`
- `development.md`

按需补读：

- 产品背景或范围不清时读 `prd.md`
- 评审结论或争议点相关时读 `review-notes.md`
- 前端实现问题读 `frontend-design.md`
- 后端约束或接口问题读 `backend-design.md`
- 测试范围与回归口径读 `test-cases.md`

### Step 4: Call out gaps before acting

如果文件仍是模板占位或明显缺失：

- 先指出缺口
- 再决定是否向用户索要资料
- 不要把占位模板当成真实需求事实

## Commands

```bash
python3 /absolute/path/to/dev-asset-context/scripts/dev_asset_context.py show --repo <repo-path>
python3 /absolute/path/to/dev-asset-context/scripts/dev_asset_context.py sync --repo <repo-path>
```

## Reading Strategy

- 不要一上来全量读取整个目录。
- 先读 `overview.md` 和 `development.md`。
- 只在本次任务确实需要时再读其它文档。
- 对于仍然是模板占位的文件，明确告诉用户“已有槽位但内容缺失”。

## Always / Never

**Always:**

- 在 Git 仓库内继续已有分支工作时优先使用本 skill
- 在开始代码修改前先刷新一次 `development.md`
- 先读摘要文件，再决定是否补读专项文档
- 发现缺失资产时明确说出来

**Never:**

- 不经读取资产目录就直接声称“我已经理解当前需求”
- 默认把整个 `.dev-assets/<branch>/` 全部灌进上下文
- 把模板占位内容当成真实背景
- 资产目录缺失时跳过 setup 直接开始改代码

## Red Flags

这些想法出现时，说明你正在绕过入口流程：

- “我先看看代码再说”
- “先问两个问题，不急着读资产”
- “目录里东西太多，先不读”
- “这个需求我大概知道”
- “用户只是问一句，不算正式开工”

如果出现这些想法，回到 Step 1。
