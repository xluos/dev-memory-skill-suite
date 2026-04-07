---
name: using-dev-assets
description: >-
  Use when starting any Git-repository development conversation, before code edits,
  before repository exploration, before clarifying implementation details, or
  whenever the agent may need to silently recover, update, or sync the current
  branch's development assets. Be proactive: if the agent notices meaningful
  progress, reusable conclusions, or code changes worth recording, route into
  the dev-assets suite even without an explicit user request.
---

# Using Dev Assets

这是开发资产套件的总入口 skill。它不直接沉淀资料，而是决定在当前对话起点，或在会话进行中出现阶段性沉淀时，是否应该先走 `dev-assets-context`、`dev-assets-setup`、`dev-assets-update` 或 `dev-assets-sync`。

**Announce at start:** `我先检查当前对话是否应该进入 dev-asset 套件。`

**Core principle:** 在 Git 仓库里继续需求开发时，不只要在开头判断是否恢复上下文；在会话推进过程中，也要判断是否该无感同步当前分支资产，再去继续读代码、提问题、改实现。

## The Rule

如果当前对话满足任一条件，优先调用对应的 dev-assets skill：

- 正在 Git 仓库里继续已有分支工作
- 即将开始读代码、改代码、排查问题
- 用户提到“继续这个需求”“看下这个分支”“接着做”“开始处理”
- 用户提到提交、commit、stage、commit message
- 用户提到“记一下”“补充进去”“更新到资产里”“把这个结论存起来”
- agent 发现已经出现值得记录的改动、结论、约束、风险或阶段性进展

## Routing

### Route to `dev-assets-context`

当用户是在已有分支上继续开发、排查、解释、修改时：

- 先恢复当前分支资产
- 先读 `overview.md` / `development.md`
- 再决定是否补读专项文档

### Route to `dev-assets-setup`

当当前分支还没有资产目录，或者用户明确表示这是新需求/新分支第一次开始时：

- 初始化 `.dev-assets/<branch>/`
- 主动索要 PRD、评审、技术方案、测试用例等资料

### Route to `dev-assets-sync`

当满足以下任一情况时：

- 用户提到 commit / 提交 / stage / staging / commit message / 提交前同步 / 提交后补记录
- agent 判断当前已经形成了值得沉淀的改动、结论、约束、风险、测试口径或阶段性结果
- 当前即将切换任务，而这一段上下文后续大概率还会被引用

把它视为自动同步检查点，尽量无感地先同步会话内容；如存在新提交，再记录提交。

### Route to `dev-assets-update`

当用户明确想把新的背景、结论、约束、风险、测试口径、链接或方案补进分支资产时：

- 定位当前分支资产目录
- 选择最合适的目标文件
- 把本轮新信息和相关会话上下文整理后写入对应资产
- 刷新 `manifest.json` 的更新时间

## Workflow

### Step 1: Check whether this is Git-repository development work

如果不是 Git 仓库内的开发对话，不使用本套件。

如果是，继续 Step 2。

### Step 2: Decide whether this is a context, setup, update, or sync moment

- 开始继续已有分支工作 → `dev-assets-context`
- 新分支第一次开始，或缺少资产目录 → `dev-assets-setup`
- 主动补充或修正资产内容 → `dev-assets-update`
- 提交相关动作，或 agent 发现当前已有值得记录的阶段性产出 → `dev-assets-sync`

### Step 3: Invoke the routed skill before proceeding

在调用对应 skill 前，不要直接：

- 开始代码编辑
- 先问实现细节问题
- 先浏览大量代码
- 在明显值得沉淀的节点上什么都不记就继续往前走

## Always / Never

**Always:**

- 在 Git 仓库开发对话开头先做一次路由判断
- 把“继续开发”“提交检查点”和“阶段性里程碑”都视为 dev-asset 套件的高优先级触发场景
- 先路由，再进入具体 skill
- 优先让同步变成无感动作，而不是等用户提醒

**Never:**

- 明知道在继续分支开发，却跳过 dev-asset 判断
- 把提交相关对话直接当成纯 Git 操作
- 在资产目录缺失时直接开始实现
- 明知道这一段内容后续会复用，却因为用户没开口就不记录

## Red Flags

- “先看看代码，之后再决定要不要读资产”
- “这只是个小问题，不算开发上下文”
- “先把 commit 做了，记录回头补”
- “用户没说要记，那就先不记”

这些都说明你在绕开套件入口。
