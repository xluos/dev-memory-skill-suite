# Backlog

本文件记录已经设计过、但当前规模下尚未落地的能力。每条都有明确的触发条件 ——
当对应痛点真实出现时，再回来看这里的方案草稿，不要提前实现。

---

## episodes.jsonl append-only 日志

### 动机

v2 的 `progress.md` 是 snapshot 语义（新写入 upsert 覆盖"当前进展"整个 section），
旧内容被新内容顶掉后就没了。sync 时累积的历史进展只能在 git log 里间接还原，
调试"上周这个分支为什么从结论 A 变到结论 B"很吃力。

### 方案

在每个分支目录加 `episodes.jsonl`，每次 capture 追加一行：

```json
{
  "ts": "2026-04-23T20:00:00+0800",
  "commit_range": "c145629..13b0afe",
  "kind": "progress",
  "title": "v2 合并落地",
  "summary": "合并 sync+update → capture；lib 重写；添加 lazy init"
}
```

语义：

- `progress.md` = 当前视图（可读，面向人）
- `episodes.jsonl` = ground truth（追加，面向机器）
- 视图任何时候可以从 episodes 重建（扫全量 → 取每个 section 的最新一条）
- graduate 归档时一并带走 `episodes.jsonl`，跨分支历史仍可检索

### 类比

`auto-claude-memory (Graphiti)` 的 "episodes" 概念 —— 对话级记忆用
append-only 日志 + 可派生视图替代直接写入语义化文件。

### 触发条件

- 分支数 >10 且跨分支调试诉求出现
- 出现明显的"当时记忆被覆盖丢失"案例
- graduate 审核时发现 pending-promotion 不够用，需要时间线辅助

### 风险

- 数据量增长：每次 capture +1 行，活跃分支几个月下来可能到几千行。仍比整个 git log 小。
- 格式演进：JSONL schema 改动时需要迁移脚本。保持字段可选、向前兼容。

---

## 隐式 demotion

### 动机

两个场景：

1. **progress.md 的 upsert 语义** —— sync 时新写入直接覆盖旧段落，但某些旧进展有归档价值（比如"之前 X 方案已作废"）。现在直接丢掉了。
2. **shared/ 层的老条目** —— repo-shared decisions/glossary 一旦写进去就留着。有些跨分支结论被后续分支反复推翻，但没有机制提醒"这条可能已过时"。

### 方案

**progress 归档：**

- capture upsert 一个 snapshot section 前，先把旧内容写到 `_archive/<date>/progress-superseded.md`
- 不是 `_archive/<date>/progress.md`（那是整个分支归档）——用独立文件名区分
- capture 的输出里报告"归档了 N 行旧内容到 xxx"

**shared 条目的失效计数：**

- capture 时如果写入的决策 / 风险内容**否定**了某条 shared 条目（heuristic：内容提到 "X 不再适用"、"废弃"、"改为"），在该 shared 条目末尾追加一行 `<!-- demote-hint: 2026-04-23 capture 否定 -->`
- context 读 shared 时扫 demote-hint 累积数：N≥3 时在输出里给用户提示"shared/decisions.md 第 X 行可能已过时，建议 demote 到某分支 archive 或删除"

### 类比

Anthropics memory-management 的 **promotion / demotion 规则** —— 内容根据使用频率 / 一致性自动在热缓存和深存储之间流动。

### 触发条件

- 实际遇到"上次我们明明讨论过 X 方案为什么忘了" —— progress 归档需求
- shared 层开始出现内部矛盾的条目 —— demote-hint 需求
- 用户手动 graduate 次数增多，发现老 shared 条目很多需要手动清理

### 风险

- heuristic 误判：否定性判断很难稳，容易对正常语境里的"不再"、"废弃"打错标。先只记 hint 不自动删，由人决定
- 归档文件膨胀：`_archive/<date>/progress-superseded.md` 按日期聚合即可，不需要按 session

---

## 相关但更远的

### FTS5 索引 + 子 agent 大记忆检索

归档分支多了（几十个 `branches/_archived/*`）时，跨归档 grep 会拖慢 `dev-assets-context`。
用 Python 自带 sqlite3 的 FTS5 对所有 `.md` 做增量索引，context/graduate 检索走索引，
配合 sub-agent 返回摘要而不是原文，保护主 context 窗口。

### user scope

跨仓库个人习惯（比如"我偏好 tabs 不用 spaces"）既不是 branch 也不是 repo 语义。
需要加一层 `_user/` 放全局习惯，capture 识别"这条不是仓库特定的"时提示上浮。

---

## 不做的原因汇总

当前规模（3 个活跃分支、约 30 条记忆）离以上任何一个的临界点都还远。
过早实现只会增加心智成本 + 维护负担。把它们记录下来是为了：

1. 遇到痛点时知道"这件事已经设计过了，不用再想一遍"
2. 真正开始做时有方案草稿不用从头推
3. 避免在无关 PR 里捎带实现，保持每次改动语义纯粹
