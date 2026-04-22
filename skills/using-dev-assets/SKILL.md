---
name: using-dev-assets
description: >-
  Use when starting any Git-repository or non-git project development conversation,
  before code edits, before repository exploration, before clarifying implementation details,
  or whenever the agent may need to recover, update, or sync repo+branch development memory.
  In non-git working directories, the suite degrades to a single repo-shared layer keyed off
  a `.dev-assets-id` dotfile.
---

# Using Dev Assets

这是开发资产套件的总入口 skill。它不直接沉淀资料，而是决定在当前对话起点或会话进行中，是否应该先走 `dev-assets-context`、`dev-assets-setup`、`dev-assets-update` 或 `dev-assets-sync`。

**Announce at start:** 用一句简短的话说明将先判断当前对话是否需要进入 dev-assets 套件。

## Routing

按下面的顺序判断，命中第一个就走对应 skill：

| 当前情境 | 走哪个 skill |
| --- | --- |
| 当前 repo 还没有当前 branch 的资产目录，或这是新需求/新分支第一次开始 | `dev-assets-setup` |
| 在已有分支上继续开发、排查、解释、修改（默认情况） | `dev-assets-context` |
| 已经到了检查点：刚提交 / 准备提交 / handoff / 阶段收敛 / lifecycle hook 触发 | `dev-assets-sync` |
| 不是检查点，但对话里出现了"现有记忆写错了/缺了/被新资料推翻了" | `dev-assets-update` |
| 都不命中 | 不进入套件，正常对话 |

### Update 与 Sync 的关系

不是平级互斥的两个触发器，而是：

- `sync` 是检查点时刻的**复合动作包**：内部可以包含 0–N 次 update + HEAD marker + manifest 刷新。
- 检查点时如果同时发现某条旧记忆需要修正，正确路径是"在 sync 流程里先调一次 update，再 record-session"，不要两个都跳过、也不要纠结二选一。
- 非检查点的零散修正/补充才是 update 的独立触发场景。
- 一次性澄清、普通问答、低价值波动既不该 sync 也不该 update。

子 skill 内部不再重复这条边界，统一以本表为准。

## Always / Never

**Always:**

- 在 Git 仓库开发对话开头先做一次路由判断
- 优先维护当前 branch 的有效记忆
- 把检查点时刻理解为"sync 包含若干 update"，而不是二选一

**Never:**

- 明知道在继续分支开发，却跳过 dev-assets 判断
- 把 repo 共享层当成 branch 当前工作态的替代品
- 在 branch 资产缺失时直接开始实现
- 因为低价值波动或一次性澄清就触发任何写入
