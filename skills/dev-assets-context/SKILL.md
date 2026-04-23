---
name: dev-assets-context
description: 恢复并读取当前仓库+分支的已有开发记忆。在任何 Git 仓库或非 git 项目开发对话起点、代码编辑前、仓库探索前都应该检查。v2 lazy init：分支目录不存在时会自动建骨架。触发词："这个分支之前到哪了"、"上次我们怎么决定的"、"原来的结论是什么"、"恢复一下上下文"、"看看当前进展"。
---

# Dev Assets Context

把当前仓库的 branch 记忆作为默认上下文入口恢复出来；repo 共享层只在需要时补读。

**Workspace mode：** cwd 是多 repo workspace 时，SessionStart 已自动注入 primary 仓库的完整记忆 + 其他仓库的简短概览。当需要切换焦点到非 primary 仓库补读完整记忆时，向脚本传递 `--repo <basename>` 明确指定。

**Announce at start:** 用一句简短的话说明将先恢复当前 branch 记忆，再按需补读 repo 共享记忆。

## DO / DON'T

**Do:**

- 会话起点、代码编辑前、排查前先跑 context（尤其 SessionStart 没注入当前关注的 repo 时）
- 用户说"之前怎么决定的"/"这个分支上次到哪了"/"原来的结论是什么"/"恢复一下"
- 需要验证某个历史假设时（context 能帮你查到过去的结论）

**Don't:**

- SessionStart 已注入当前 repo 完整 progress+risks，且本轮需要的信息都在里面
- 本轮只是问元信息（git status、当前分支名、这是什么文件类）
- 还没理解用户诉求就先跑 context（先听懂再决定要不要拉记忆）

## Tiered lookup（必读）

按下面顺序查询，命中且够用就停下：

1. **SessionStart 已注入的内容**：通常包括当前 repo 的 progress.md + risks.md 摘要。先看是不是已经覆盖本轮需要，避免重复加载。
2. **`branches/<current>/progress.md` + `risks.md`**：hot 层，当前进展 + 阻塞。默认就读这两个。
3. **`branches/<current>/decisions.md`**：需要决策背景（"为什么这么做"）时才补读。
4. **`branches/<current>/glossary.md`**：需要术语、测试命令、外部链接时补读。
5. **`shared/*`（repo 共享层）**：需要跨分支约定、长期背景、共享入口时读。
6. **`branches/_archived/*`（归档）**：查历史问题、追溯某个结论起源时才读。记忆量大时派子 agent 做 FTS/grep + 摘要。
7. **问用户**：前面都没命中，或信息不全/互相冲突时主动问。

**Sub-agent 派发阈值：** 当 `branches/<current>/*.md` 累计 > 3000 行，或需要跨归档分支检索时，不要主 agent 直接读文件，派 Task 子 agent 只返回 200–1000 字综合摘要，避免吃掉主 context。

## Workflow

### Step 1: Locate repo + branch assets

```bash
python3 /absolute/path/to/dev-assets-context/scripts/dev_asset_context.py show \
  [--repo <repo-path>] [--branch <branch-name>]
```

输出里包含：
- `setup_completed`：当前分支是否走过 setup merge 流程
- `missing_or_placeholder`：仍然是空模板或占位符的文件（空 = 没内容，非错误）
- `files`：所有 v2 文件的绝对路径

v2 lazy init：当 branch_dir 不存在时，show / sync 会自动把骨架建出来，不会报错。

### Step 2: 按 Tiered lookup 的顺序读

SessionStart 阶段（hook 自动）会跑一次 context 的 sync 子命令，把 progress.md 的自动同步区刷新成最新 git facts + 注入 progress/risks/decisions 摘要。所以会话开始时你通常不用手工跑 context。

会话中需要补读时，直接 Read 对应文件：
- `Read {paths.progress}`
- `Read {paths.risks}`
- `Read {paths.decisions}`

需要主动刷新自动同步区（新 commit 没被 pick 到）时：

```bash
python3 /absolute/path/to/dev-assets-context/scripts/dev_asset_context.py sync \
  [--repo <repo-path>] [--branch <branch-name>]
```

### Step 3: 缺内容 → 转 capture

context 只读不写。发现记忆缺关键前提 / 有明显错漏 / 用户提供了新信息，走 `dev-assets-capture` 写入。

## 非 git 模式

cwd 不是 git repo 且没有子 git repo 时：
- 记忆按 `.dev-assets-id` dotfile 定位到 repo 共享层
- 没有 branch 概念，所有文件落在 `repo/` 层
- sync 子命令会跳过（没有 git facts 可收集）

## Always / Never

**Always:**

- 先跑 tiered lookup 的前两步（SessionStart 注入 + progress.md + risks.md）
- 大记忆量时派子 agent，保护主 context

**Never:**

- 直接 Read 整个 branch 目录（永远按 tiered 顺序，读到够就停）
- 在 SessionStart 已覆盖时重复加载
- 在 context show 报 error（比如 git 不在仓库内）时强行继续
