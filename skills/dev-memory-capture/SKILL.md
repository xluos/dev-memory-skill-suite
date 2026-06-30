---
name: dev-memory-capture
description: '**Hard rule：识别到 capture 信号必须直接执行写入，不要输出"要不要记"类问句。字面值缺失时只问缺的字段，不问"是否 capture"。** 当对话里出现值得未来读到的开发记忆（稳定决策、用户纠正/偏好、踩过的坑、试错收敛后的结论、文档与实测口径漂移、commit 落盘后 progress.md 未同步、用户 checkpoint 发言如"commit 一下/告一段落/记一下"），或用户显式让记（"记一下"、"留个 note"、"以后都按这个"、"立个规矩"），用本 skill 写入。动作前用陈述句报备写什么和 kind。record --kind 支持 decision/risk/glossary/source/overview/scope/constraint/shared-*/unsorted/pending/filemap；进展类用 summary-json、glossary 或 sync-working-tree。dev-memory 套件唯一写入入口。'
---

# Dev Memory Capture

Capture 是 dev-memory 套件里**唯一的写入入口**。不管是 checkpoint 批量记录，还是改写某段旧结论，还是随手丢一句话给它自己分类，都走这个 skill。

核心特性：

- **Lazy init**：当前仓库/分支还没被 setup 过也可以直接写，第一次调用会自动把记忆骨架建出来，内容落到对应 `branches/<branch>/` 目录下。
- **三种写入模式**：显式 `--kind`、自动分类（heuristic）、batch session payload。脚本内部统一路由。
- **kind 口径**：`record --kind` 支持 `decision` / `risk` / `glossary` / `source` / `overview` / `scope` / `constraint` / `shared-*` / `unsorted` / `pending` / `filemap`。进展、下一步、checkpoint 摘要用 `--summary-json`；有效上下文补充用 `glossary`；Git 改动概览用 `sync-working-tree` 刷新 `progress.md` 自动同步区。
- **自动跨分支打标**：如果内容看起来跨分支可复用（不含分支特有名词 + 有"经验/模式/最佳实践"类信号），会同时追加到 `pending-promotion.md`，给后续 graduate 预筛。
- **写入前 dedup check**：append 类 kind（decision / risk / glossary / source / shared-* / unsorted / pending）写入前自动对目标 section 做相似度查重。命中疑似重复时**拦下不写**、退出码 2、stdout 返回 `dedup_hint`（含相似候选 + 推荐 action），agent 看完决定 `rewrite-entry` / `--force` / 不调。这是阻止"6 天累积出 3 套互相矛盾决策"的最后一道防线。
- **纠错前置读取**：用户纠正旧记忆、指出前面记错、说某条不再适用时，先用 `list-entries` 读取目标文件 section 的现有 entry，再 `rewrite-entry` / `delete-entry`。不要依赖精确/模糊匹配先猜哪条，也不要 append 一条"修正说明"把错误和正确内容同时留在记忆里。

## Announce at start —— 必须是陈述句

把本轮关键信息写入当前分支记忆**之前**，用一句**陈述句**报备，写明三件事：写什么 + kind + 模式（explicit kind / auto / session batch）。

- ✅ 正确：「我把 ByteRAG 接口口径写入 dev-memory（kind=glossary，repo 共享层，explicit kind 模式）」
- ❌ 错误：「要不要我把 ByteRAG 接口口径写进 dev-memory？」
- ❌ 错误：「我先确认一下，是不是该把这个记下来？」
- ❌ 错误：「这些信息看起来值得记录，你看要不要 capture？」

任何疑问句开头、或含"要不要 / 是不是 / 需不需要 / 是否 / 你看 / 让我先确认"等征询语的 announce 都是 bug。

**字面值缺失时的拆分原则**：

如果你判断要 capture，但内容里某个字段（API 域名、文件路径、命令具体值等）**在当前会话上下文里找不到字面值**：

1. 先 announce：「我准备把 X 写入 dev-memory（kind=Y）」—— 这是陈述，不可省略
2. 再**单独**询问那个缺失字段：「但 API 域名我从上下文里没找到，能贴一下吗？」
3. 拿到字段后立刻执行 capture

不要因为字面值缺失就退化成「要不要 capture」这种大问句 —— "是否做 capture" 已经由触发信号决定了，不需要再问；只有"具体填什么"才能问。把这两件事揉进同一个问句是复合 bug。

## 二阶 anti-pattern — meta context 识别

如果对话历史里出现你（或上一个 agent）已经抛出的"要不要 capture / 要不要记下来"问句，**立刻直接执行 capture，不要再次确认** —— 用户的质疑或重复本身就是对那条问句的否定，再问一次是复合 bug。

典型场景：

- 用户在消息里**引用了** agent 之前发的"要不要把 X 记进 dev-memory？"
- 用户在**质疑这种问询行为本身**（"为什么还问？""不是说好自动触发吗？"）
- 用户在**重述** agent 之前提议过但没执行的 capture 内容

正确动作：简短承认问题 → 立刻 capture（陈述式 announce + 调用），而不是接着讨论"那我现在写吗？"。如果字面值缺失，按上一节的拆分原则单独问字段，但绝不再问"是否 capture"。

## 触发词典

### DO — 这些时刻应该跑 capture

**Checkpoint 类（会话节奏到了该存档的点）：**

- "commit 一下" / "先 commit" / "帮我 commit"
- "告一段落" / "先到这" / "休息一下" / "明天再继续"
- "同步一下 dev-memory" / "记一笔" / "把这个记下来"
- "这一轮做完了" / "这波没问题了"

**改写类（旧记忆已失效需要更新）：**

- "刚才说的改了" / "那个结论过时了" / "之前的记忆错了"
- "用 X 替代 Y"（替换决策）
- "这条不再适用" / "把这条删掉重写"
- "前面那条有问题" / "不是这么记的" / "这会导致记忆混乱"

**自动触发（不需要用户说话）：**

- 本轮产生了新的稳定决策（"结论：X 改为 Y，因为 Z"）
- 新增了可落库的阻塞或注意点
- 新 commit 已经落盘，但 `progress.md` 的 "当前进展" 还在上一版
- 用户手动甩了一段话（"这段记一下"）

**经验/教训类（用户不主动开口但高价值，agent 必须主动捕捉）：**

这一类是 dev-memory 的最大收益点 —— 不现在记，下一个 agent（或下一次的你）就会重新走同一条弯路、被同一个用户纠正同一件事。识别信号：

- **试错收敛型**：本轮经过多轮尝试才拿到可用答案。除了最终结论（→ `decision`），把**走过的弯路**单独记 `risk`（"以为 X 能行，结果 Y 失败，最终改 Z"）。不记，下次 dead-end 会被完整重走一遍 —— 这正是 capture 存在的理由。
- **用户反对 / 纠正 agent 做法**：agent 做了或提议了 X，用户说 "不对，改 Y" / "别这样做" / "这里不能这么用" —— 先判断是否是在纠正已有记忆。若是，走下方"Rewrite-first 纠错流程"，改写/删除旧 entry；只有找不到对应旧 entry 时，才把用户的反向意见作为新决策写入（→ `decision`）。如果纠正听上去是一般性规则（不局限于本次场景），升格为 `shared-decision`。
- **用户声明偏好/禁令**：用户说 "以后 X 都用 Y" / "不要用 Z" / "这个仓库一律走 W" —— 最典型的跨分支规则，直接走 `shared-decision` 写 repo/decisions.md，不要落在分支层（分支结束就丢了）。
- **认知修正 / gotcha**：本轮出现"原以为 X，实际 Y"的反直觉发现，即便用户没说"记一下"，也算 `risk`。

这一类**强烈适合 pending-promotion**：内置 `is_cross_branch_candidate()` 会识别 "经验/模式/教训/通用/复用/gotcha/pattern/lesson" 类信号并自动追加到 `pending-promotion.md`；content 措辞里带上这些词能帮分类器更准确地打标。

### DON'T — 这些时刻不要跑 capture

- 本轮只是探索性讨论，还没形成可保留结论（等稳定再记）
- 纯上下文澄清、一次性问答（"这是什么"类问题）
- 用户在快速试错中，结论还在反复变（等收敛后再一次性 capture 试错过程 + 最终结论，参见上方"试错收敛型"）
- SessionStart 注入的内容已经覆盖本轮产出，没有新增（避免重复写）

## Rewrite-first 纠错流程（**必读**）

用户指出旧内容错了、前面思路有问题、某条记忆不再适用时，默认不是"再补一条修正"，而是**先改旧 entry**。同一主题里同时保留错误条目和更正条目，会让下一个 agent 同时读到互相冲突的事实，这是 capture 的高危失败模式。

**强制流程：**

1. 先判断这类记忆大概率落在哪个 kind，然后直接读取该 kind 对应文件 section 的现有 entries：
   ```bash
   npx dev-memory-cli capture list-entries \
     --repo <repo-path> \
     --kind decision \
     --limit 120
   ```
   常见映射：决策/规则读 `decision` 或 `shared-decision`；风险/坑读 `risk`；术语/上下文读 `glossary` 或 `shared-context`。不确定 kind 时先用 `classify` / `suggest-kind` 判断，再读对应 section；不要直接靠关键词匹配决定旧条目。

2. 读 `entries[]` 的 `full_text`：
   - 明确是同一条旧记忆 → `rewrite-entry --id <id> --content "<正确的新条目>"`
   - 旧条目应彻底失效且不需要替代 → `delete-entry --id <id>`
   - 多个候选都相关 → 逐条 rewrite/delete；不要只改最新一条
   - 找不到对应旧条目 → 才允许 `record` 写新条目
   - section 很长时，可加 `--tail` 先看最新条目；仍不确定再读更大 `--limit`

3. 新内容应写成最终事实，不要写成"上一条是错的，修正为..."这种依赖历史的描述。例：
   - ✅ `结论: capture 遇到用户纠正旧记忆时先 list-entries 读取目标 section，基于真实 entry 内容选择 rewrite-entry/delete-entry；找不到旧条目才 append。`
   - ❌ `修正: 之前说 capture 可以追加修正说明是不对的。`

**禁止：**

- 用户已经指出旧内容错了，还直接 `record --kind decision --content "修正: ..."`。
- 先 append 修正条目，再期待 tidy 以后清理旧错误。tidy 是兜底清理，不是纠错主路径。
- 只因为精确匹配 / fuzzy 匹配没找到，就认为可以 append。纠错场景必须先读目标 section 的现有 entries。

## 三种写入模式

### Mode 1: 显式 kind（最精准，适合你知道该进哪个文件）

```bash
npx dev-memory-cli capture record \
  --repo <repo-path> \
  --kind decision \
  --content "结论: 撤销 fresh retry，改为抛 CodexMissingResumeError"
```

支持的 kind：

| Kind | 目标文件 | 默认 section | 何时用 |
|---|---|---|---|
| `decision` | branch/decisions.md | 关键决策与原因 | 稳定结论 + Why |
| `risk` | branch/risks.md | 阻塞与注意点 | 坑 / 失败 / 注意 |
| `glossary` | branch/glossary.md | 当前有效上下文 | 术语、外部系统、测试命令 |
| `source` | branch/glossary.md | 分支源资料入口 | 分支专属的文档/链接 |
| `overview` / `scope` / `constraint` | branch/overview.md | 各分 section | 冷启动摘要的目标、范围、约束 |
| `shared-decision` | repo/decisions.md | 跨分支通用决策 | 仓库级通用决策 |
| `shared-overview` / `shared-constraint` | repo/overview.md | 对应 section | 仓库级目标/约束 |
| `shared-context` | repo/glossary.md | 长期有效背景 | 仓库级长期背景 |
| `shared-source` | repo/glossary.md | 共享入口 | 仓库级文档/链接 |
| `unsorted` | branch/unsorted.md | 待分类 | 明知不清楚，让 setup 以后分类 |
| `pending` | branch/pending-promotion.md | 候选条目 | 手动打标跨分支候选 |
| `filemap` | branch/progress.md | 功能文件索引 | 文件索引快照，通常由工具生成，人工少用 |

进展与下一步记录方式：checkpoint 摘要用 `record --summary-json`；交接上下文用 `glossary`；Git 改动概览用 `capture sync-working-tree`。

### Mode 2: auto（让分类器决定）

```bash
npx dev-memory-cli capture record \
  --repo <repo-path> \
  --auto \
  --content "阻塞：恢复卡 pending 状态是进程内 Map，服务重启后旧按钮失效"
```

heuristic 规则（按先到先中的顺序）：

1. 含 `结论 / 决[定议] / 不再 / 改为 / 采用 / 废弃` → `decision`
2. 含 `阻塞 / 注意 / 坑 / 失败 / 风险 / 卡住 / gotcha / caveat / warning` → `risk`
3. 含 `即 / 指的是 / 对应 / 链接 / http / 缩写 / 术语 / 简称 / 别名` → `glossary`
4. 进展类文本（`当前 / 已完成 / 下一步 / commit / 提交 / 实现 / 进展 / todo / wip`）：用 `--summary-json`、`glossary` 或 `sync-working-tree`
5. 都不中 + 未 setup → `unsorted`（等 setup 时人工分类）
6. 都不中 + 已 setup：显式选择 `decision` / `risk` / `glossary` / `overview` / `scope` / `constraint` / `unsorted` 中最贴近的 kind

不确定时用 `suggest-kind` 子命令先 dry-run：

```bash
npx dev-memory-cli capture suggest-kind \
  --content "..." --branch-name "feature/xxx"
```

### Mode 3: batch session payload（会话结束整理一次）

适合一次会话末尾打包记录多类信息：

```bash
npx dev-memory-cli capture record \
  --repo <repo-path> \
  --summary-json '{
    "title": "Codex resume-card 实现",
    "overview_summary": ["当前阶段：恢复卡错误处理已收敛"],
    "context_updates": ["实现口径：缺失 rollout 时抛 CodexMissingResumeError"],
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
| `list-entries` | 读取某个 kind 对应 section 的现有 entries，纠错时优先用它选旧条目 |
| `find-candidates` | fuzzy 搜索现有 append 型 entry；只作辅助缩小范围，不作为纠错主路径 |
| `rewrite-entry` | 按 entry id 改写已有 entry（dedup_hint 推荐 update_existing 时用这个，不要 record --force） |

## Dedup hint 处理流程（**必读**）

append 模式的 `record` 写入前会查重。**通过**（exit 0）→ 同正常流程。**被拦下**（exit 2）→ stdout 是这个形状：

```json
{
  "blocked": true,
  "reason": "similar_entry_exists",
  "kind": "decision",
  "target_file": "branch/decisions.md",
  "section": "关键决策与原因",
  "new_content_preview": "...",
  "matches": [
    {
      "id": "decisions::1::5",
      "similarity": 0.83,
      "match_first_line": "前端分工：徐帅武 ...",
      "match_full_text": "...",
      "supersedes_signal_detected": false
    }
  ],
  "recommendation": "update_existing" | "review_and_decide",
  "next_actions": [...]
}
```

**正确处理（按 recommendation 分流）**：

1. **`recommendation == "update_existing"`**（新内容含 supersedes 关键词、或仅一个超高相似匹配）：
   ```bash
   npx dev-memory-cli capture rewrite-entry \
     --repo <repo-path> \
     --id <matches[0].id> \
     --content "<new text>"
   ```
   这是"刚才说的改了 / 那条结论过时了"类纠正的**主路径**，比 append 新条目 + 留旧条目互相矛盾干净得多。

2. **`recommendation == "review_and_decide"`**（多个中相似候选）：读 `matches[].match_full_text`，自己判断：
   - 是同一回事的演进 → `rewrite-entry`
   - 是相关但独立的新事实 → `record --force`
   - 是误判同义、确实不该写 → 不调任何命令

3. **千万不要做的事**：拿到 blocked 直接 retry `--force`。`--force` 是给"agent 已经看过 matches、确认是独立新事实"的逃生口，不是错误恢复路径。无脑 `--force` = 把 dedup 当垃圾防御绕过 = 重新累积矛盾决策。

### `--force` 使用前提

仅在以下场景用 `record --force`：

- agent 已读 dedup_hint 的 matches，**判断确实是独立新事实**（不是修订旧条目）
- 用户明确说"留两条都记一下、不删旧的"
- 测试 / 调试场景

绝不在以下场景用 `--force`：

- 没看 matches，看到 blocked 就 retry
- 拿不准 update 还是 append，"先 force 再说"
- 字面值有 80% 重复但内容意图本质不同 —— 这种应 rewrite-entry 把新表达替换进旧 entry，而不是同名再加一条

### 批量模式（`record --summary-json`）

每条独立 dedup check。整体退出码：所有都未 blocked → 0；至少一条 blocked → 2。stdout JSON 会含 `dedup_blocked: [...]` 列出被挡的条目，未 blocked 部分照常写。处理被挡的条目和单条模式一致 —— 按 recommendation 分流 rewrite-entry / --force / skip。

## Setup 之前 vs 之后

| 时刻 | auto 分类默认 | unsorted.md 累积策略 |
|---|---|---|
| 未 setup | 不确定 → unsorted | 用户稍后跑 setup 时批量分类 |
| 已 setup | 显式选择 kind | 不确定时写 `unsorted` |

Capture 会在 `branch_dir 不存在` 时走 lazy-init 自动建骨架。进展类文本使用 `--summary-json`、`glossary` 或 `sync-working-tree`；保守兜底使用 `--kind unsorted`。

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

- **问"要不要 capture / 要不要记一下"** —— 是否 capture 由触发信号决定，不该交回给用户。识别到信号就 announce + 执行；字面值缺失只能单独问那个字段，不能把"是否 capture"也变成问题。
- 在未 setup 时拒绝写入（永远走 unsorted 兜底）
- 对分支特有内容打 pending-promotion（这会污染 graduate 候选）
- 对一次性澄清、试错性讨论做任何写入
- 看到 `exit 2 + dedup_hint` 直接 retry `--force` —— 必须先读 matches[] 决定 rewrite-entry / --force / skip 中的哪一个，盲 force 是把 dedup 防御废掉、重新累积矛盾决策的最快方式
