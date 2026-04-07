---
name: using-dev-assets
description: Use when starting any Git-repository development conversation, before code edits, before repository exploration, or before clarifying implementation details, to decide whether the branch's development asset skills should be invoked first.
---

# Using Dev Assets

这是开发资产套件的总入口 skill。它不直接沉淀资料，而是决定在当前对话起点是否应该先走 `dev-asset-context`、`dev-asset-setup` 或 `dev-asset-sync`。

**Announce at start:** `我先检查当前对话是否应该进入 dev-asset 套件。`

**Core principle:** 在 Git 仓库里继续需求开发时，先判断是否该恢复或同步分支资产，再去读代码、提问题、改实现。

## The Rule

如果当前对话满足任一条件，优先调用对应的 dev-asset skill：

- 正在 Git 仓库里继续已有分支工作
- 即将开始读代码、改代码、排查问题
- 用户提到“继续这个需求”“看下这个分支”“接着做”“开始处理”
- 用户提到提交、commit、stage、commit message

## Routing

### Route to `dev-asset-context`

当用户是在已有分支上继续开发、排查、解释、修改时：

- 先恢复当前分支资产
- 先读 `overview.md` / `development.md`
- 再决定是否补读专项文档

### Route to `dev-asset-setup`

当当前分支还没有资产目录，或者用户明确表示这是新需求/新分支第一次开始时：

- 初始化 `.dev-assets/<branch>/`
- 主动索要 PRD、评审、技术方案、测试用例等资料

### Route to `dev-asset-sync`

当用户提到以下任一提交相关动作时：

- commit
- 提交
- stage / staging
- commit message
- 提交前同步
- 提交后补记录

把它视为提交检查点，先同步工作区，再记录提交。

## Workflow

### Step 1: Check whether this is Git-repository development work

如果不是 Git 仓库内的开发对话，不使用本套件。

如果是，继续 Step 2。

### Step 2: Decide whether this is a context, setup, or sync moment

- 开始继续已有分支工作 → `dev-asset-context`
- 新分支第一次开始，或缺少资产目录 → `dev-asset-setup`
- 提交相关动作 → `dev-asset-sync`

### Step 3: Invoke the routed skill before proceeding

在调用对应 skill 前，不要直接：

- 开始代码编辑
- 先问实现细节问题
- 先浏览大量代码

## Always / Never

**Always:**

- 在 Git 仓库开发对话开头先做一次路由判断
- 把“继续开发”和“提交检查点”视为 dev-asset 套件的高优先级触发场景
- 先路由，再进入具体 skill

**Never:**

- 明知道在继续分支开发，却跳过 dev-asset 判断
- 把提交相关对话直接当成纯 Git 操作
- 在资产目录缺失时直接开始实现

## Red Flags

- “先看看代码，之后再决定要不要读资产”
- “这只是个小问题，不算开发上下文”
- “先把 commit 做了，记录回头补”

这些都说明你在绕开套件入口。
