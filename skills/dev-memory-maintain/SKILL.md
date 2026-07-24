---
name: dev-memory-maintain
description: dev-memory 的手动维护入口。仅当用户明确点名 `dev-memory-maintain`（例如 `$dev-memory-maintain`）时使用；不要因普通的“整理记忆”或“归档分支”等自然语言自动触发。根据用户选择，只进入整理或归档对应的子流程。
---

# Dev Memory Maintain

这是整理与归档的统一手动入口。它不自动判断开发是否结束，也不在普通会话中主动触发。

## 按类型路由

只读取当前类型对应的 reference，不要一次性加载两份：

| 用户选择 | 读取 |
| --- | --- |
| 整理、tidy、清理未分类或陈旧记忆 | [references/tidy.md](references/tidy.md) |
| 归档、archive、上提共享知识后归档分支 | [references/archive.md](references/archive.md) |

如果用户只调用了本 Skill，没有说明类型，先问一句：“这次要整理还是归档？”

用户已经说明类型时直接进入对应流程，不重复确认类型。读取对应 reference 后完整遵守其中的检查、审核、确认和复核步骤。

## 边界

- 目标仓库默认是当前工作目录对应的 Git 仓库；用户指定了仓库或分支时以用户输入为准。
- 本 Skill 直接执行 reference 中的底层 CLI 流程，不再调用 `dev-memory-cli maintain` 启动另一层维护 Agent。
- 未到 reference 规定的人工确认点，不提前要求确认；到达破坏性 apply 门禁后必须等待用户明确确认。
- 不承担普通的记忆读取；只想查询既有记忆时使用 `dev-memory-read`。
