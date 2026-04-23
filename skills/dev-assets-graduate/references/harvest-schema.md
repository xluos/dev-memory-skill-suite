# harvest.json schema (v2)

`apply` 命令读取的 patch 文件。v2 把旧的 `repo_context` / `repo_sources` 合并重分到 `repo_decisions` / `repo_glossary`，schema 必须同步更新，否则条目会被 apply 静默忽略，总数为 0 但归档照常进行。

## 结构

```json
{
  "repo_overview": [
    { "section": "长期目标与边界", "body": "- ...", "mode": "append" }
  ],
  "repo_decisions": [
    { "section": "跨分支通用决策", "body": "- ...", "mode": "append" }
  ],
  "repo_glossary": [
    { "section": "长期有效背景", "body": "- ...", "mode": "append" },
    { "section": "共享入口", "body": "- ...", "mode": "append" },
    { "section": "共享注意点", "body": "- ...", "mode": "append" }
  ],
  "notes": "短说明，落到 archive_summary.md 头部",
  "archive": true
}
```

## 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `repo_overview` | array | 上提到 repo/overview.md 的条目，可省略 |
| `repo_decisions` | array | 上提到 repo/decisions.md 的条目，可省略 |
| `repo_glossary` | array | 上提到 repo/glossary.md 的条目，可省略 |
| `section` | string | repo 文件中的目标 section title（必须是已存在的 section，否则会被新建） |
| `body` | string | 写入内容（markdown，允许多行） |
| `mode` | enum | `append`：追加到 section 末尾；`replace`：完全覆盖该 section |
| `notes` | string | 可选，会被写到 `archive_summary.md` 顶部 |
| `archive` | bool | 可选，默认 `true`。设 `false` 只做 harvest 不做归档（少见，用于先 review） |

## v1 → v2 迁移映射参考

写 harvest 时如果习惯了 v1 的 key 名字，按下表对应：

| v1 key | v1 section | v2 key | v2 section |
|---|---|---|---|
| `repo_overview` | 长期目标与边界 | `repo_overview` | 长期目标与边界 |
| `repo_overview` | 仓库级关键约束 | `repo_overview` | 仓库级关键约束 |
| `repo_context` | 跨分支通用决策 | `repo_decisions` | 跨分支通用决策 |
| `repo_context` | 长期有效背景 | `repo_glossary` | 长期有效背景 |
| `repo_context` | 共享注意点 | `repo_glossary` | 共享注意点 |
| `repo_sources` | 共享入口 | `repo_glossary` | 共享入口 |

## apply 的校验

v2 `apply` 会对顶层未知 key 直接报错：

```
unknown harvest key(s): ['repo_context', 'repo_sources'] (v1 schema? ...)
```

这是为了避免 v1 schema 的 harvest.json 条目被静默丢弃、但 branch 仍然被归档的 silent failure。如果你真的要在过渡期兼容，先手动把 v1 key 映射成 v2 key 再跑 apply。

## append 行为

- 已有 section 内容末尾追加换行 + body
- 多个同 target section 的 entry 会按顺序逐个追加
- 如果 section 不存在，创建该 section 并写入 body

## replace 行为

- 整个 section 内容被替换为 body
- 多个同 target 的 entry 时，**只有最后一个生效**（前面的被覆盖）—— 所以 replace 模式建议每个 section 只出现一次

## 不写入的字段

v2 branch 文件（overview / progress / decisions / risks / glossary / unsorted / pending-promotion）不接受 harvest patch —— 这些是 branch 级文件，归档时整体 mv 走，不存在"提炼后留下"的概念。
