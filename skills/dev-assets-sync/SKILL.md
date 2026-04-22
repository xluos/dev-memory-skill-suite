---
name: dev-assets-sync
description: Use when the current conversation reaches a commit-related checkpoint or another clear persistence checkpoint, and this round produced progress, decisions, or risks worth carrying into the next session. Implicit use applies whenever new commits land or the conversation produces a stable conclusion that the SessionStart-injected memory does not yet reflect — for example, when `development.md` "当前进展" lists commits that no longer match the head of `git log <base>..HEAD`. Repo-shared source updates here mean shared documents and links, not branch-only hot paths.
---

# Dev Assets Sync

在提交相关检查点，或在当前对话已经形成需要跨会话保留的稳定结论时，把这次会话结束后仍然有价值的信息同步到当前 branch 记忆，并顺带刷新 repo 共享层的轻量元信息。

**Workspace mode：** 当 cwd 是 workspace 根（不是 git repo，但一级子目录中有多个 git repo）时：向脚本传递 `--repo <basename>` 明确指定目标仓库；若未指定且 `DEV_ASSETS_PRIMARY_REPO` env 已设置，会默认落到 primary 仓库。跨仓库 sync 需要为每个仓库各调用一次。

**Announce at start:** 用一句简短的话说明将先沉淀本次检查点留下的关键信息。

## Workflow

### Step 1: Confirm this is a real checkpoint

进入 sync 前，确认当前时点确实是检查点：

- 刚完成一次提交、准备提交、或刚完成一轮代码/排查检查点
- 当前进展、阻塞、下一步已经清晰到值得 handoff 或跨会话延续
- 阶段性方向已经收敛，适合在这个检查点留一份快照
- 本轮新增了仓库共享层之后还会复用的文档入口、链接或决策结论
- lifecycle hook 触发

如果只是单次排查里刚确认 root cause / scope / 某条规则，还不到检查点，就别进 sync。具体路由判定（包括 sync 与 update 的关系）以 `using-dev-assets` 为准。

确认是检查点后，sync 是一个"复合动作包"：本流程内部如果同时发现需要改写某条旧记忆，先按 `dev-assets-update` 改写对应 section，再回到 Step 2 做 record-session。

### Step 2: Summarize only what this checkpoint should leave behind

优先提炼：

- 当前进展
- 当前阻塞与注意点
- 下一步
- 关键决策与原因
- 本次新增的共享资料入口

这里的“共享资料入口”只指仓库共享层应该复用的文档、链接、外部资料，不包括当前分支后续要看的 hot paths、目录或局部代码入口。

`record-session` 不接受任意自定义字段名（例如 `current_progress`、`blockers_or_caveats`、`shared_sources` 都会被忽略；`decisions` 必须是 object 数组而不是 string 数组，否则脚本会直接报错）。准备 `summary.json` 之前请先读 `references/commit-sync.md`，里面有完整字段表、别名、最小示例和常见错误模式，不要凭印象脑补 schema。

整理好 summary 后运行：

```bash
python3 /absolute/path/to/dev-assets-sync/scripts/dev_asset_sync.py record-session --repo <repo-path> --summary-file <summary.json>
```

### Step 3: Keep branch state and repo metadata separate

`record-session` 的默认落点是：

- 进展 / 风险 / 下一步 / 分支级决策 → branch 文件
- 新增资料入口 → repo `sources.md`
- HEAD / 最近访问分支 → manifest

额外约束：

- 写进 `repo/sources.md` 的必须是 repo 共享资料入口
- branch-only 导航、hot paths、局部代码入口不属于这里的 `sources`

它默认不做这些事：

- 不重建整个 branch memory
- 不把实现流水账写回记忆
- 不把 branch-specific 当前状态写进 repo 共享正文

### Step 4: Lifecycle hooks are the default low-friction path

如果希望不依赖对话里显式触发，就使用仓库提供的生命周期 hook 模板与脚本：

- `SessionStart` 恢复当前 repo+branch 记忆
- `PreCompact` 刷新 working-tree-derived navigation
- `Stop` / `SessionEnd` 只保留轻量 HEAD marker

推荐的 repo-local 落地点：

- Claude: `.claude/settings.local.json`
- Codex: `.codex/hooks.json`

可复用模板：

- Claude: `hooks/hooks.json`
- Codex: `hooks/codex-hooks.json`

## Commands

```bash
python3 /absolute/path/to/dev-assets-sync/scripts/dev_asset_sync.py record-session --repo <repo-path> --summary-file <summary.json>
python3 /absolute/path/to/dev-assets-sync/scripts/dev_asset_sync.py sync-working-tree --repo <repo-path>
python3 /absolute/path/to/dev-assets-sync/scripts/dev_asset_sync.py record-head --repo <repo-path>
```

## Always / Never

**Always:**

- 把提交检查点和阶段性里程碑都视为 `sync` 触发点
- 优先沉淀本次提交留下的关键信息，而不是累积历史
- 明确区分 branch 当前记忆、repo 共享入口、Git 历史

**Never:**

- 把 `sync` 当成 append-only 日志工具
- 把 `sync` 当成全局状态重建工具
- 为了“怕漏”就把所有改动详情抄进记忆
- 把 commit history 当成 dev-assets 的一部分
