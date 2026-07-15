# Dev Memory Skill Suite

Dev Memory Skill Suite 为 Codex、Claude Code、Trae / Trae CN 等 coding agent 提供跨会话开发记忆能力。

开发知识存储在用户目录，并按 **仓库身份 + Git 分支** 隔离。分支层保存当前工作的目标、约束和上下文，repo 层保存跨分支长期有效的规则与资料入口。Git 负责提交与代码历史，dev-memory 负责对后续开发仍有价值的语义信息。

![Dev Memory 工作方式](docs/diagrams/overview.png)

## 核心能力

- **会话恢复**：`SessionStart` 自动注入当前 repo + branch 的浓缩记忆和权威文件路径。
- **后台语义沉淀**：定期读取完整会话，对照已有记忆后执行 append、rewrite、delete 或明确 skip。
- **纠错优先**：修正旧记忆时定位并改写原 entry，避免同时保留互相冲突的新旧结论。
- **分支隔离**：同一仓库的不同分支拥有独立工作记忆，可 fork、rename、reset 或归档。
- **跨分支共享**：稳定规则进入 repo 层；候选知识可在分支完成时提炼上提。
- **记忆维护**：支持未分类条目整理、批量校准、备份和归档。
- **多仓库工作区**：一个 workspace 下可同时加载多个 repo，并为主仓库保留更完整的上下文。
- **本地管理面板**：浏览、编辑已存储记忆，预览 SessionStart 注入文本，并查看会话扫描与 token 用量。

## 一个常驻 Skill

| Skill | 职责 | 适用场景 |
| --- | --- | --- |
| `dev-memory-read` | 定位并搜索当前 repo/branch 的权威记忆，只读不写 | 主动恢复既有记忆、查询历史 TODO |

普通开发会话不再常驻 capture/setup/tidy/graduate Skill。会话写入由 session-scan 后台处理；整理和归档由 CLI 临时启动专用维护 Agent，并只把对应维护 prompt 放进那个独立会话。

## 安装

运行环境：Node.js 18+、Python 3；Git 仓库模式依赖 Git。

### 安装 Skill

列出仓库提供的 skill：

```bash
npx skills add xluos/dev-memory-skill-suite --list
```

安装到 Codex 全局：

```bash
npx skills add xluos/dev-memory-skill-suite --skill dev-memory-read -a codex -g -y
```

安装到检测到的所有 agent：

```bash
npx skills add xluos/dev-memory-skill-suite --skill dev-memory-read --all -g -y
```

### 安装 CLI 与 Hook

```bash
npm install -g dev-memory-cli

# 在当前仓库安装生命周期 hook
dev-memory-cli install-hooks codex
dev-memory-cli install-hooks claude
dev-memory-cli install-hooks trae
dev-memory-cli install-hooks trae-cn

# 或一次安装全部支持的客户端
dev-memory-cli install-hooks --all
```

安装到 agent 用户级配置：

```bash
dev-memory-cli install-hooks --all --global
```

CLI 也可通过 `npx -y dev-memory-cli ...` 按需执行。Skill 与 hook 相互独立，安装 skill 不会修改本地 hook 配置。

Trae 的 Hook 配置按客户端隔离：国际版写入 `.trae/hooks.json`，国内版写入 `.trae-cn/hooks.json`；加 `--global` 时分别写入用户目录下的同名路径。已有 Hook 会保留，重复安装只更新 dev-memory 自己的条目。

Codex Desktop 没有本仓库依赖的项目生命周期 hook。安装每日扫描任务可覆盖 Codex CLI 和 Desktop 共同写入的本机会话文件：

```bash
dev-memory-cli session-scan install
dev-memory-cli session-scan status
```

扫描任务默认在本地时间 03:00 和 13:00 运行，时间列表可配置。LaunchAgent 触发时会读取 macOS HID 空闲时长；最近 10 分钟有键鼠输入则记录 `skipped_active` 并退出，不扫描文件或调用模型。活跃检测失败时默认保守跳过。手工执行 `session-scan run` 不受该检测影响。`install-hooks codex` 只安装 CLI hook，不会隐式安装定时任务。

## 基本使用

记忆是显式 opt-in。首次使用先初始化，然后由生命周期 hook 恢复、由定期 session-scan 沉淀新会话语义。

```bash
# 为当前 repo + branch 初始化记忆骨架
dev-memory-cli init

# 查看当前仓库和分支对应的记忆路径
dev-memory-cli read show

# 只在当前 repo 的记忆范围内搜索
dev-memory-cli read search --query "发布流程" --query "回滚"

# 启动独立整理 Agent（不会把整理 prompt 放进普通开发会话）
dev-memory-cli maintain tidy

# 启动独立归档 Agent
dev-memory-cli maintain archive --branch feature/example
```

`read search` 默认搜索当前 branch + repo 共享层。跨分支和归档范围通过 `--scope` 显式指定：

```bash
dev-memory-cli read search --scope all-branches --query "关键词"
dev-memory-cli read search --scope archived --query "关键词"
```

### 底层写入路由

![Capture 写入路由](docs/diagrams/capture.png)

`capture` 是 session-scan 和维护 Agent 使用的底层写入引擎，同时保留为高级管理 CLI；它不再对应常驻 Skill。`capture record` 支持三种输入方式：

- **显式 kind**：调用方明确指定内容语义，直接写目标 section。
- **自动分类**：`--auto` 根据文本信号判断 decision、risk、glossary；无法明确分类时进入 `unsorted.md`。
- **结构化批量输入**：`--summary-json` 或 `apply-summary-output` 把一次会话产生的多类知识批量落盘。

主要 kind 与落点：

| Kind | 落点 | 写入方式 |
| --- | --- | --- |
| `decision` | `branches/<branch>/decisions.md` | append |
| `risk` | `branches/<branch>/risks.md` | append |
| `glossary` / `source` | `branches/<branch>/glossary.md` | append |
| `overview` / `scope` / `constraint` | `branches/<branch>/overview.md` | upsert |
| `filemap` | `branches/<branch>/progress.md` | upsert |
| `unsorted` / `pending` | 分支层对应文件 | append |
| `shared-decision` | `repo/decisions.md` | append |
| `shared-context` / `shared-source` | `repo/glossary.md` | append |
| `shared-overview` / `shared-constraint` | `repo/overview.md` | upsert |

`progress.md` 用于 Git 派生导航和功能文件索引，不承载人工维护的进度流水账。临时工作状态归属于当前会话、任务系统或 Git 工作区；dev-memory 仅记录跨会话仍然有效的稳定信息。

append 类写入执行相似 entry 检查。旧内容修订采用“读取原条目，再改写或删除”的流程：

```bash
dev-memory-cli capture list-entries --kind decision --tail
dev-memory-cli capture find-candidates --kind decision --query "旧结论"
dev-memory-cli capture rewrite-entry --id <entry-id> --content "修正后的完整结论"
dev-memory-cli capture delete-entry --id <entry-id>
```

符合跨分支复用判定的内容会同时进入 `pending-promotion.md`，由归档维护 Agent 在分支收尾阶段审核；候选内容不会直接写入 repo 共享层。

### 整理与归档

普通入口只有两个，都会启动独立交互式 Agent：

| 命令 | 处理对象 | 结果 |
| --- | --- | --- |
| `dev-memory-cli maintain tidy` | `unsorted.md` 与已结构化但漂移的内容 | 分类、proposal HTML 审核、备份后 edit/delete/reset |
| `dev-memory-cli maintain archive` | 已完成分支的有效记忆 | dry-run、上提跨分支知识、确认后移入 `_archived` |

维护 Agent 内部使用 `setup`、`tidy`、`graduate` 等低层子命令。`tidy apply` 和 `graduate apply` 会改变或移动已有记忆，仍然受人工审核和显式确认门禁保护。用 `--print-prompt` 可以只查看将传给独立 Agent 的完整维护指令。

### 分支记忆操作

```bash
dev-memory-cli branch                         # 交互式操作
dev-memory-cli branch list
dev-memory-cli branch inspect --branch feature/example
dev-memory-cli branch fork --source main --target feature/example
dev-memory-cli branch rename --source old --target new
dev-memory-cli branch init --branch feature/example --backup
dev-memory-cli branch delete --branch feature/example --backup
dev-memory-cli branch inherit-worktree-base
```

目标分支已有内容时默认拒绝覆盖。`--backup` 会先归档目标记忆；`--force` 用于明确接受覆盖风险的场景。

linked worktree 首次创建记忆时会尝试从 reflog 识别源分支并继承记忆。append 型知识写回源分支由以下配置显式启用：

```bash
git config --local dev-memory.worktreeWriteback true
```

## 生命周期 Hook

| 事件 | Codex CLI | Claude Code CLI | Trae | Trae CN | 行为 |
| --- | :-: | :-: | :-: | :-: | --- |
| `SessionStart` | ✓ | ✓ | ✓ | ✓ | 刷新 Git 派生索引，注入浓缩记忆和完整文件路径；同一 session 幂等 |
| `Stop` | ✓ | ✓ | ✓ | ✓ | 记录轻量 HEAD marker；有可识别会话信息时登记扫描候选，不启动模型 |
| `PreCompact` |  | ✓ |  |  | 兼容占位，当前不执行额外刷新 |
| `SessionEnd` |  | ✓ |  |  | 记录最终 HEAD，并把 transcript 总结任务放入后台队列 |

### 会话总结

Claude Code CLI 通过 `SessionEnd` 创建后台总结任务。Codex、Trae 和 Trae CN 当前没有该事件：它们的 `Stop` hook 只做轻量记录，并在 payload 带有可识别会话信息时登记扫描候选，不会在每轮回复后启动总结模型。现有定时扫描器只解析 Codex CLI / Desktop 写入的 `~/.codex/sessions` 和 `~/.codex/archived_sessions`；Trae Hook 支持当前覆盖 SessionStart 恢复与轻量 HEAD 刷新，不宣称扫描其私有会话存储。

任务触发端与总结执行端相互独立。Claude 的即时 worker 和 Codex 定时扫描器都可以使用 `coco`、`codex` 或 `claude` CLI。扫描器在 `~/.dev-memory/config.json` 的 `session_scan` 中内置三个可配置 preset，默认按 `claude → codex` 选择第一个可用命令；`coco` preset 继续保留，可显式启用或加入顺序。每个 preset 可指定模型、profile、额外参数和环境变量。

总结输入包含全部尚未处理的 user/assistant 语义消息和现有 dev-memory，不按“最近几条”截尾，也不截断单条消息。长会话按顺序分块并最终归并；工具调用流水账、system 消息和 reasoning 不参与语义总结。输出限定为结构化 JSON，用于新增或修正 decisions、risks、glossary、file map 和 repo 共享记忆；时效性的“当前进展”“下一步”“当前阶段”不会写入。

会话总结不是纯追加流程。最终归并会读取当前 branch 和 repo 的已有记忆，再根据新材料选择对应操作：

- `append`：增加新的决策、风险、术语或资料入口。
- `upsert`：更新 overview、file map 等快照型内容。
- `rewrite`：旧结论已经失效或需要纠正时，改写原 entry。
- `delete`：已有 entry 已过期、错误或被新结论取代时删除。
- `skip`：与已有记忆相比没有有效变化时不写入。

模型只生成结构化操作建议，不直接编辑记忆文件。`apply-summary-output` 会校验目标 entry、执行去重并应用变更；没有充分依据时保留现有条目，不为了“整理”而自动删除。

累积型语义 section 默认最多保留最新 200 条，可通过 `DEV_MEMORY_MAX_ENTRIES` 调整；repo 共享决策和长期背景使用更严格的 20 条上限。Markdown 文件维持 oldest-to-newest 的稳定存储顺序，SessionStart 注入时按 newest-to-oldest 选取并排列内容，因此有限的注入窗口始终优先包含最新记忆。overview、file map 等快照型 section 采用覆盖更新，不累积历史版本。事件日志和 artifacts 不计入这项语义条目上限。

扫描游标只在所有分块完成并成功落盘后推进。Codex 执行器强制使用 `--ephemeral`，内部总结 session ID 和 prompt marker 也会被发现阶段排除，避免扫描器递归总结自己产生的会话。完整配置、账本和队列说明见 [hooks/README.md](hooks/README.md#codex-定时扫描)。

常用扫描命令：

```bash
dev-memory-cli session-scan run --dry-run --json
dev-memory-cli session-scan run
dev-memory-cli session-scan stats --json
dev-memory-cli session-scan history --limit 20
dev-memory-cli session-scan replay --run-id <run-id> --session-id <session-id> [--session-id <session-id> ...] [--executor codex]
dev-memory-cli session-scan config show
dev-memory-cli session-scan config set-executor codex
dev-memory-cli session-scan config set-model codex <model>
dev-memory-cli session-scan config set-schedule 03:00 13:00
dev-memory-cli session-scan config set-active-minutes 10
dev-memory-cli session-scan config set-timeout 360
```

已安装定时任务时，`set-schedule` 会自动重载 LaunchAgent，使新的时间列表立即生效。

`replay` 会按历史 run 记录的 `cursor_before` / `cursor_after` 精确重放指定会话，不回退当前会话游标；`--executor` 只覆盖本次运行，不修改持久配置。单分块会话只调用一次模型；最终结果必须包含至少一个记忆变更，或提供非空 `skip_reason`。空对象、只有 `title`、以及没有产生语义 action 且未说明原因的结果都会标记失败并保留重试空间。单次模型调用默认 360 秒超时，超时不会立即重试。run 账本会记录输出字段计数、`skip_reason`、payload 哈希、有效/观测字节数和语义 action 数，不保存原始总结正文。

## 运行模式

| 模式 | 检测条件 | 行为 |
| --- | --- | --- |
| 单 repo | 当前目录位于 Git 仓库内 | 读取和写入当前 repo + branch |
| Workspace | 当前目录不是 Git 仓库，但一级子目录包含 Git 仓库 | 主仓库注入完整上下文，其余仓库按数量注入精简 brief |
| No-git | 当前目录和一级子目录都不是 Git 仓库 | 使用 `.dev-memory-id` 建立目录身份，分支层折叠到 repo 层 |

Workspace 主仓库配置持久化在 workspace 根目录：

```bash
dev-memory-cli workspace show
dev-memory-cli workspace primary <repo-basename>
```

`DEV_MEMORY_PRIMARY_REPO` 可作为临时 override。维护命令在 workspace 中应使用 `--repo <repo-path-or-basename>` 明确目标仓库。

## 存储结构

默认存储根目录是 `~/.dev-memory/repos`，可用 `DEV_MEMORY_ROOT` 或 Git 配置 `dev-memory.root` 覆盖。

![Dev Memory 存储结构](docs/diagrams/storage.png)

```text
~/.dev-memory/repos/<repo-key>/
  repo/
    manifest.json
    overview.md                  # 长期目标和仓库级约束
    decisions.md                 # 跨分支通用决策
    glossary.md                  # 长期背景和共享入口
    log.md                       # repo 级事件日志
    artifacts/
  branches/
    <branch-key>/
      manifest.json
      overview.md                # 当前目标、范围、约束
      progress.md                # Git 派生导航和功能文件索引
      decisions.md               # 分支决策及原因
      risks.md                   # 阻塞、风险和注意点
      glossary.md                # 有效上下文与源资料入口
      unsorted.md                # 待人工分类内容
      pending-promotion.md       # 跨分支候选
      log.md                     # 分支事件日志
      artifacts/history/
    _archived/
      <archived-branch>/
      INDEX.md
```

`repo-key` 优先根据 Git remote identity 计算，多个 clone 和 worktree 由此共享同一套记忆。分支名会转换为文件系统安全的 `branch-key`。

Codex 原始会话和扫描审计账本位于：

```text
~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
~/.codex/archived_sessions/

~/.dev-memory/jobs/session-scan/
  candidates/                    # Codex Stop 登记的轻量候选
  state/                         # 每个会话的已处理字节游标
  sessions/                      # 会话大小、分块和用量摘要
  runs/                          # 每次扫描的完整指标
  logs/                          # LaunchAgent 标准输出与错误日志
  events.jsonl
  internal-sessions.jsonl        # 递归扫描排除表
```

仓库级总结任务仍位于 `~/.dev-memory/repos/<repo-key>/jobs/session-summary/`。扫描审计只保存路径、哈希、字节范围和结构化摘要，不复制完整会话原文。

## 本地管理面板

```bash
dev-memory-cli ui
dev-memory-cli ui --port 7878
dev-memory-cli ui --no-open
dev-memory-cli ui --read-only
```

管理面板默认只监听 `127.0.0.1`，提供 repo/branch 文件浏览、已有 Markdown 或 JSON 编辑、目标分支完整注入预览，以及按仓库和扫描运行聚合的原始大小、处理字节数与 token 用量。未返回 usage 的执行器调用单独标记，不按 0 token 处理。命令行预览入口如下：

```bash
dev-memory-cli context injection-preview \
  --repo-key <repo-key> \
  --branch <branch-name> \
  --context-dir ~/.dev-memory/repos
```

## CLI 概览

```text
dev-memory-cli read <show|search>
dev-memory-cli init [--repo PATH] [--branch NAME]
dev-memory-cli maintain <tidy|archive> [--repo PATH] [--branch NAME] [--executor auto|codex|coco]
# low-level mutation/admin commands:
dev-memory-cli capture <show|suggest-kind|classify|record|list-entries|find-candidates|rewrite-entry|delete-entry|apply-summary-output|sync-working-tree|record-head>
dev-memory-cli setup <init|merge-unsorted|mark-completed>
dev-memory-cli tidy <prepare|apply>
dev-memory-cli graduate <dry-run|apply|index>
dev-memory-cli branch [list|inspect|rename|fork|delete|init|inherit-worktree-base]
dev-memory-cli context <show|sync|injection-preview>
dev-memory-cli workspace <show|primary>
dev-memory-cli summary <extract-core>
dev-memory-cli session-scan <run|replay|install|status|stats|history|show|uninstall|config>
dev-memory-cli hook <session-start|pre-compact|stop|session-end>
dev-memory-cli ui
dev-memory-cli install-hooks <codex|claude|trae|trae-cn|--all>
```

具体参数以各子命令的 `--help` 为准。

## 设计边界

dev-memory 的能力边界不包括：

- 复制完整 PRD、会议记录或外部文档正文
- 替代 Git 提交历史和 diff
- 保存完整会话流水账
- 自动归档图片、录音、附件等非文本资产
- 把 branch-specific 的当前工作态写进 repo 共享层

存储内容限定为经过提炼、对后续开发仍有价值的目标、约束、决策、风险、术语、资料入口和文件导航。

## 仓库结构

```text
bin/dev-memory.js              # CLI 入口与 Node 侧命令
lib/dev_memory_*.py            # 记忆、分支、会话扫描、整理与归档实现
lib/maintenance/               # 按需注入独立维护 Agent 的 prompt
lib/ui-server.js               # 本地管理面板服务
lib/ui-app.html                # 管理面板前端
scripts/hooks/                 # 生命周期 hook 实现
hooks/                         # Codex / Claude Code / Trae hook 模板
skills/dev-memory-read/        # 唯一常驻 Skill
tests/                         # Python 测试
docs/diagrams/                 # README 图表及源文件
```

## License

[MIT](LICENSE)
