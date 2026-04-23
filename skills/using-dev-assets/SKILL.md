---
name: using-dev-assets
description: Dev-assets 套件总入口路由。在任何 Git 仓库或非 git 项目开发对话开头、代码编辑前、仓库探索前、实现细节澄清前、需要恢复 / 更新 / 沉淀仓库+分支开发记忆时都应该优先经过一次路由判断。v2 不再需要前置 setup —— 任何写入会 lazy init。
---

# Using Dev Assets

这是开发资产套件的总入口 skill。它不直接沉淀资料，而是决定在当前对话起点或会话进行中，是否应该先走 `dev-assets-context`、`dev-assets-capture`、`dev-assets-setup` 或 `dev-assets-graduate`。

**Announce at start:** 用一句简短的话说明将先判断当前对话是否需要进入 dev-assets 套件以及走哪个子 skill。

## v2 架构简述

- **唯一写入入口**：`dev-assets-capture`（合并了原 sync + update）
- **唯一读取入口**：`dev-assets-context`
- **整理入口**：`dev-assets-setup`（不再是前置门禁，而是把 unsorted.md 合并到结构化文件）
- **归档入口**：`dev-assets-graduate`（显式触发，只扫 pending-promotion.md）

写入永远 lazy init：没 setup 过也能写。

## Routing

按下面顺序判断，命中第一个就走对应 skill：

| 当前情境 | 走哪个 skill |
| --- | --- |
| 用户说"归档"/"分支收尾"/"需求做完了"/"merge 完了清一下" | `dev-assets-graduate` |
| 用户说"整理 dev-assets"/"整理一下"；或 unsorted.md 累积 ≥20 条；或 setup_completed=false 且即将产生新稳定结论 | `dev-assets-setup` |
| 在已有分支上继续开发、排查、解释、修改，需要恢复已有记忆（对话起点、代码编辑前） | `dev-assets-context` |
| 本轮产生了值得落库的内容（决策 / 进展 / 阻塞 / 术语 / 跨分支经验），或用户手动甩给你一段话 | `dev-assets-capture` |
| checkpoint 时刻（"commit 一下"/"告一段落"/"先到这"/"明天再继续"） | `dev-assets-capture` |
| 都不命中 | 不进入套件，正常对话 |

`graduate` 必须**显式触发**，destructive 操作不能 implicit 跑。在 no-git 模式下（cwd 不是 git repo），graduate 永远不命中。

## Capture 的合并含义

v2 前有 `dev-assets-sync`（checkpoint 批量）和 `dev-assets-update`（零散改写），**已被合并为 dev-assets-capture**。如果你本能想选 sync 或 update，就走 capture：
- 想批量记 → `capture record --summary-json ...`
- 想改某一条 → `capture record --kind X --content ...`
- 不确定分到哪 → `capture record --auto --content ...`

## DO / DON'T

**Do:**

- 开发对话开头先做一次 dev-assets 路由判断
- 优先维护当前 branch 的有效记忆
- 对 git 仓库、非 git 项目、workspace 多仓库都要路由（不是只在 git repo）
- setup 之前就可以 capture —— lazy init 会兜底

**Don't:**

- 明知道在继续分支开发，却跳过 dev-assets 判断
- 把 repo 共享层当成 branch 当前工作态的替代品
- 在一次性澄清、普通问答、低价值波动时触发任何写入
- 在 SessionStart 已经注入完整当前 repo 记忆时再跑 context（重复加载）
- 在本轮只是探索、还没形成稳定结论时跑 capture（等稳定再记）

## Tiered lookup（context 专属）

context 内部按下面顺序找信息，每步未命中才走下一步：

1. SessionStart 已注入？→ 够用就返回
2. `branches/<current>/progress.md` + `risks.md`（hot 层，SessionStart 主要注入这两个）
3. `branches/<current>/decisions.md`（需要决策背景时）
4. `shared/*`（跨分支约定时）
5. 归档分支检索（历史问题时，派子 agent）
6. 主动问用户

这套顺序由 `dev-assets-context` 的 SKILL.md 显式执行，路由判断时只需要知道它会自动分层。
