---
name: dev-assets-capture
description: 统一写入入口：把本轮对话产生的决策 / 进展 / 阻塞 / 术语 / 跨分支经验沉淀到当前仓库+分支的开发记忆。在 Git 仓库或非 git 项目开发中随时可触发 —— lazy init，不再要求先 setup。触发词："commit 一下"、"告一段落"、"先到这"、"明天再继续"、"记一笔"、"刚才说的改了"、"那个结论过时了"、"同步到 dev-assets"、"把这条经验写进去"；以及检测到本轮产生了稳定结论 / 新决策 / 新阻塞 / 新 commit 未同步时。也替代了已经删除的 dev-assets-sync 和 dev-assets-update —— 任何"写入分支记忆"的需求都走这里。
---

# Dev Assets Capture

Capture 是 dev-assets 套件里**唯一的写入入口**。不管是 checkpoint 批量记录，还是改写某段旧结论，还是随手丢一句话给它自己分类，都走这个 skill。它替代了老的 `dev-assets-sync` + `dev-assets-update`。

核心特性：

- **Lazy init**：当前仓库/分支还没被 setup 过也可以直接写，第一次调用会自动把记忆骨架建出来，内容落到对应 `branches/<branch>/` 目录下。
- **三种写入模式**：显式 `--kind`、自动分类（heuristic）、batch session payload。脚本内部统一路由。
- **自动跨分支打标**：如果内容看起来跨分支可复用（不含分支特有名词 + 有"经验/模式/最佳实践"类信号），会同时追加到 `pending-promotion.md`，给后续 graduate 预筛。

**Announce at start:** 用一句简短的话说明将把本轮关键信息沉淀到当前分支记忆，并说明走的是哪种模式（explicit kind / auto / session batch）。

## 触发词典

### DO — 这些时刻应该跑 capture

**Checkpoint 类（会话节奏到了该存档的点）：**

- "commit 一下" / "先 commit" / "帮我 commit"
- "告一段落" / "先到这" / "休息一下" / "明天再继续"
- "同步一下 dev-assets" / "记一笔" / "把这个记下来"
- "这一轮做完了" / "这波没问题了"

**改写类（旧记忆已失效需要更新）：**

- "刚才说的改了" / "那个结论过时了" / "之前的记忆错了"
- "用 X 替代 Y"（替换决策）
- "这条不再适用" / "把这条删掉重写"

**自动触发（不需要用户说话）：**

- 本轮产生了新的稳定决策（"结论：X 改为 Y，因为 Z"）
- 新增了可落库的阻塞或注意点
- 新 commit 已经落盘，但 `progress.md` 的 "当前进展" 还在上一版
- 用户手动甩了一段话（"这段记一下"）

### DON'T — 这些时刻不要跑 capture

- 本轮只是探索性讨论，还没形成可保留结论（等稳定再记）
- 纯上下文澄清、一次性问答（"这是什么"类问题）
- 用户在快速试错中，结论还在反复变（等用户确认了再记）
- SessionStart 注入的内容已经覆盖本轮产出，没有新增（避免重复写）

## 三种写入模式

### Mode 1: 显式 kind（最精准，适合你知道该进哪个文件）

```bash
python3 scripts/dev_asset_capture.py record \
  --repo <repo-path> \
  --kind decision \
  --content "结论: 撤销 fresh retry，改为抛 CodexMissingResumeError"
```

支持的 kind：

| Kind | 目标文件 | 默认 section | 何时用 |
|---|---|---|---|
| `decision` | branch/decisions.md | 关键决策与原因 | 稳定结论 + Why |
| `progress` | branch/progress.md | 当前进展 | 当前做到哪 |
| `next` | branch/progress.md | 下一步 | 下一步要做什么 |
| `risk` | branch/risks.md | 阻塞与注意点 | 坑 / 失败 / 注意 |
| `glossary` | branch/glossary.md | 当前有效上下文 | 术语、外部系统、测试命令 |
| `source` | branch/glossary.md | 分支源资料入口 | 分支专属的文档/链接 |
| `overview` / `scope` / `stage` / `constraint` | branch/overview.md | 各分 section | 冷启动摘要的四部分 |
| `shared-decision` | repo/decisions.md | 跨分支通用决策 | 仓库级通用决策 |
| `shared-overview` / `shared-constraint` | repo/overview.md | 对应 section | 仓库级目标/约束 |
| `shared-context` | repo/glossary.md | 长期有效背景 | 仓库级长期背景 |
| `shared-source` | repo/glossary.md | 共享入口 | 仓库级文档/链接 |
| `unsorted` | branch/unsorted.md | 待分类 | 明知不清楚，让 setup 以后分类 |
| `pending` | branch/pending-promotion.md | 候选条目 | 手动打标跨分支候选 |

### Mode 2: auto（让分类器决定）

```bash
python3 scripts/dev_asset_capture.py record \
  --repo <repo-path> \
  --auto \
  --content "阻塞：恢复卡 pending 状态是进程内 Map，服务重启后旧按钮失效"
```

heuristic 规则（按先到先中的顺序）：

1. 含 `结论 / 决[定议] / 不再 / 改为 / 采用 / 废弃` → `decision`
2. 含 `阻塞 / 注意 / 坑 / 失败 / 风险 / 卡住 / gotcha / caveat / warning` → `risk`
3. 含 `即 / 指的是 / 对应 / 链接 / http / 缩写 / 术语 / 简称 / 别名` → `glossary`
4. 含 `当前 / 已完成 / 下一步 / commit / 提交 / 实现 / 进展 / todo / wip` → `progress`
5. 都不中 + 未 setup → `unsorted`（等 setup 时人工分类）
6. 都不中 + 已 setup → `progress`（默认兜底）

不确定时用 `suggest-kind` 子命令先 dry-run：

```bash
python3 scripts/dev_asset_capture.py suggest-kind \
  --content "..." --branch-name "feature/xxx"
```

### Mode 3: batch session payload（会话结束整理一次）

适合一次会话末尾打包记录多类信息：

```bash
python3 scripts/dev_asset_capture.py record \
  --repo <repo-path> \
  --summary-json '{
    "title": "Codex resume-card 实现",
    "implementation_notes": ["改为抛 CodexMissingResumeError", "..."] ,
    "next_steps": ["真实 Feishu 中复现"],
    "risks": ["恢复卡 pending 是进程内 Map"],
    "decisions": [{"decision": "rollout 缺失不再静默新开", "reason": "...", "impact": "..."}],
    "memory": ["测试命令: bun run check"]
  }'
```

payload 字段 → kind 的映射内置在脚本里，不需要用户关心。

## 其他子命令

| 子命令 | 作用 |
|---|---|
| `show` | 输出当前路径 + 缺失文件 + setup 状态，诊断用 |
| `suggest-kind` | dry-run 分类（不写任何文件） |
| `classify` | 同 suggest-kind，但会基于真实的 setup 状态判断 |
| `sync-working-tree` | 刷新 progress.md 的自动同步区（git 改动概览） |
| `record-head` | 只更新 manifest 里的 last_seen_head |

## Setup 之前 vs 之后

| 时刻 | auto 分类默认 | unsorted.md 累积策略 |
|---|---|---|
| 未 setup | 不确定 → unsorted | 用户稍后跑 setup 时批量分类 |
| 已 setup | 不确定 → progress | unsorted 基本不用了 |

Capture **永远不会拒绝写入**。这是它和老 sync/update 最大的差别 —— 老两个 skill 会在 `branch_dir 不存在` 时报 `Run dev-assets-setup first.`，capture 不会。

## Cross-branch staging 机制

每次 `record` 的 content 都会过一次 `is_cross_branch_candidate()`：

- 内容不包含分支名中的任何 ≥4 字符的 token
- 且含有 `经验 / 模式 / 最佳实践 / 教训 / 通用 / 复用 / gotcha / pattern / lesson` 之一

两条都满足才会同步追加到 `pending-promotion.md`。不是替代主写入，是在主写入之外**额外**追加一条候选标记，graduate 时只扫这个文件，不用再全量审。

## Always / Never

**Always:**

- 写入前让 lib 自己 lazy-init 目录，别先问"setup 了吗"
- 内容经过 cross-branch staging 判断后再返回
- 把 `touched_targets` 原样回显给用户，让他知道写到哪了

**Never:**

- 在未 setup 时拒绝写入（永远走 unsorted 兜底）
- 对分支特有内容打 pending-promotion（这会污染 graduate 候选）
- 对一次性澄清、试错性讨论做任何写入
