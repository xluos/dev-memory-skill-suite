# Lifecycle Hooks

本项目使用 agent 生命周期 hook，不使用 Git hook。Claude Code、Codex、Trae 与 Trae CN 的可用事件和配置目录不同，模板分别维护。

## 当前仓库里的接入方式

- repo-local 配置位置：
  - Claude: `.claude/settings.local.json`
  - Codex: `.codex/hooks.json`
  - Trae: `.trae/hooks.json`
  - Trae CN: `.trae-cn/hooks.json`
- user-level 配置位置：
  - Claude: `~/.claude/settings.json`
  - Codex: `~/.codex/hooks.json`
  - Trae: `~/.trae/hooks.json`
  - Trae CN: `~/.trae-cn/hooks.json`
- 可复用模板：
  - Claude: [hooks/hooks.json](hooks.json)
  - Codex: [hooks/codex-hooks.json](codex-hooks.json)
  - Trae / Trae CN: [hooks/trae-hooks.json](trae-hooks.json)
- `dev-memory-cli` 是所有配置共用的稳定执行入口
- hook 仅在模板内容合并到对应 agent 配置后生效

### Codex 快速安装

在目标仓库根目录执行：

```bash
sh scripts/install_codex_hooks.sh
```

如果只是想直接从 GitHub 拉脚本并把模板 merge 到当前目录的 `.codex/hooks.json`：

```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/xluos/dev-memory-skill-suite/main/scripts/install_codex_hooks.sh)"
```

这个脚本会先确保 `dev-memory-cli` 可用，然后再 merge Codex hooks。

如果 CLI 已经存在，也可以直接执行：

```bash
dev-memory-cli install-hooks codex
dev-memory-cli install-hooks claude
dev-memory-cli install-hooks trae
dev-memory-cli install-hooks trae-cn
dev-memory-cli install-hooks --all
```

已有旧配置如果还写着 `dev-memory hook ...` 或 `npx dev-memory hook ...`，重新执行安装命令会按 hook id 覆盖为 `dev-memory-cli hook ...`。

## Hook 行为

| 事件 | Codex CLI | Claude Code CLI | Trae | Trae CN | 行为 |
| --- | :-: | :-: | :-: | :-: | --- |
| `SessionStart` | ✓ | ✓ | ✓ | ✓ | 读取当前 repo+branch 记忆并注入会话；同一 session 的重复触发幂等跳过 |
| `Stop` | ✓ | ✓ | ✓ | ✓ | 每次回复后记录轻量 HEAD marker；payload 有会话信息时登记扫描候选 |
| `PreCompact` |  | ✓ |  |  | 兼容占位；当前不执行额外刷新 |
| `SessionEnd` |  | ✓ |  |  | 记录最终 HEAD，创建 transcript 总结任务并启动后台 worker |

`SessionStart` 的幂等记录位于 `<repo-memory>/jobs/session-start/injected/*.json`。重复触发只记录 skip 日志，不重复注入上下文。

## 会话总结

### 生效范围

`SessionEnd` 自动总结仅由 **Claude Code CLI** 的生命周期事件触发。Codex、Trae 和 Trae CN 模板都不注册 `SessionEnd`；它们的 `Stop` 只做轻量记录，不在每轮回答后启动模型。Codex Desktop 不使用这组项目 hook。

总结任务的触发端与执行端是两个独立概念：

- **触发端**：Claude Code CLI 的 `SessionEnd` hook。
- **执行端**：本机可用的 `coco`、`codex` 或 `claude` CLI。

因此，执行总结所用的 CLI 与产生原会话的客户端没有绑定关系。

### 总结工具选择

`install-hooks` 检测本地命令并初始化 `~/.dev-memory/config.json`。已有非空 `session_summary.command` 时保留现有配置，否则按以下顺序选择第一个可用工具：

单独执行 `install-hooks codex` 也会初始化这项 CLI 配置，但不会为 Codex CLI 增加 `SessionEnd` 事件；没有 Claude Code `SessionEnd` 或其它显式 enqueue 来源时，该配置不会自动启动总结任务。

| 优先级 | 工具 | 默认命令 |
| --- | --- | --- |
| 1 | `coco` | `coco -p --yolo --session-id {summary_session_id} {prompt}` |
| 2 | `codex` | `codex exec --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --sandbox danger-full-access {prompt}` |
| 3 | `claude` | `claude -p --permission-mode bypassPermissions --session-id {summary_session_uuid} {prompt}` |

示例配置：

```json
{
  "session_summary": {
    "provider": "codex",
    "command": "codex exec --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --sandbox danger-full-access {prompt}",
    "max_attempts": 3
  }
}
```

该机制依赖本地 CLI 命令，模型、账号和认证配置沿用被选 CLI；dev-memory 不直接集成 provider HTTP API。`session_summary.command` 支持自定义其它命令，`DEV_MEMORY_SESSION_SUMMARY_CMD` 提供进程级临时 override；`DEV_MEMORY_DISABLE_SESSION_SUMMARY_AGENT=1` 禁用后台总结执行。

### 总结作用

`SessionEnd` 总结用于从一次会话中提取对后续开发仍然有效的语义信息，而不是保存 transcript 副本或生成 changelog。处理流程如下：

1. `SessionEnd` 记录最终 HEAD，并把 job 写入 `<repo-memory>/jobs/session-summary/pending/`。
2. worker 从 transcript 中提取核心 user/assistant 文本，并加载现有 branch/repo 记忆。
3. 总结 CLI 只生成 summary-output JSON，不直接调用 dev-memory 命令。
4. worker 校验 JSON，最多重试 `max_attempts` 次，再通过代码应用结构化 patch。
5. job 根据结果移动到 `done/`、`skipped/` 或 `failed/`。

允许写入或修正的内容包括：

- decisions、risks、glossary
- 功能文件索引 `file_map`
- repo 级 shared decisions、context、sources
- 已有 entry 的 rewrite 或 delete

以下内容不会写入：

- 工具调用流水账、system 消息、reasoning
- 完整 transcript 或提交历史副本
- “当前进展”“下一步”“当前阶段”等时效性状态
- 与现有记忆相比没有有效变化的内容

无有效变化的 job 进入 `skipped/`，不会刷新 capture manifest，也不计为真实记忆写入。hook 采用后台执行，不等待总结完成。

## Codex 定时扫描

Codex CLI 和 Desktop 共用本地 rollout 文件：

```text
~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
~/.codex/archived_sessions/
```

安装 macOS LaunchAgent：

```bash
dev-memory-cli session-scan install
dev-memory-cli session-scan status
```

任务默认每天本地时间 03:00 和 13:00 运行。LaunchAgent 使用 `session-scan run --scheduled`，运行前检查 macOS HID 空闲时长；最近 10 分钟有键鼠输入时写入 `skipped_active` 记录并退出，不读取会话正文、不调用模型。检测不可用时默认保守跳过。手工执行 `session-scan run` 不启用活跃检测。

首次扫描只回看最近 3 天；后续使用持久化字节游标补扫上次成功处理后产生的全部数据。`install-hooks codex` 与 `session-scan install` 相互独立。

扫描器只提取尚未处理的 user/assistant 语义消息，不限制消息数量，也不截断单条消息。材料超过单次模型上下文时按顺序分块，每个分块都进入中间摘要，再结合现有 memory 生成最终结构化结果。游标只在最终结果成功应用后推进。

### 执行器配置

`~/.dev-memory/config.json` 中的 `session_scan` 独立配置扫描执行器。默认提供 `coco`、`codex`、`claude` 三个 preset，并按 `claude → codex` 选择第一个已安装且启用的命令；`coco` preset 为兼容保留，可显式加入 `order`：

```json
{
  "session_scan": {
    "executor": "auto",
    "order": ["claude", "codex"],
    "schedule_times": ["03:00", "13:00"],
    "skip_when_computer_active": true,
    "active_within_minutes": 10,
    "activity_check_fail_closed": true,
    "chunk_chars": 60000,
    "idle_minutes": 60,
    "first_lookback_days": 3,
    "executors": {
      "coco": {"enabled": true, "command": "coco", "model": null, "profile": null, "extra_args": [], "env": {}},
      "codex": {"enabled": true, "command": "codex", "model": null, "profile": null, "extra_args": [], "env": {}},
      "claude": {"enabled": true, "command": "claude", "model": null, "profile": null, "extra_args": [], "env": {}}
    }
  }
}
```

`model` 由内置适配器转换为对应 CLI 参数。`profile`、`extra_args` 和 `env` 用于选择账号、模型供应商、本地 provider 或代理。也可以增加自定义 preset；自定义命令需要自行保证结构化 JSON 输出。

```bash
dev-memory-cli session-scan config show
dev-memory-cli session-scan config set-executor codex
dev-memory-cli session-scan config set-model codex <model>
dev-memory-cli session-scan config set-profile codex <profile>
dev-memory-cli session-scan config set-schedule 03:00 13:00
dev-memory-cli session-scan config set-active-minutes 10
dev-memory-cli session-scan config set-active-check on
dev-memory-cli session-scan config validate
```

已安装定时任务时，`set-schedule` 会自动重载 LaunchAgent；活跃阈值和开关由每次运行动态读取，无需重装。

`auto` 只在命令不存在或 preset 被禁用时选择下一个执行器。模型调用失败不会自动换供应商，以免重复产生不可控费用。

### 递归防护

Codex preset 固定使用 `codex exec --ephemeral --json`，总结调用不会写入新的 rollout。扫描器还会登记内部 session/thread ID，并排除 `dev-memory-summary-` session 和带内部 prompt marker 的会话。即使自定义执行器产生持久化会话，也不会被下一轮当成业务会话重复总结。

### 账本与用量

```text
~/.dev-memory/jobs/session-scan/
  candidates/                    # Codex Stop 登记
  state/                         # 会话字节游标
  sessions/                      # 原始大小、分块和用量摘要
  runs/                          # 每次扫描记录
  logs/                          # LaunchAgent stdout/stderr
  events.jsonl
  internal-sessions.jsonl
```

每个 run 记录原文件大小、本轮新增字节、语义消息和字符数、原会话累计 token、每次总结调用的执行器/模型/耗时/token，以及成功、跳过和失败状态。执行器没有返回 usage 时写为 `unavailable`，不会伪装成 0。

```bash
dev-memory-cli session-scan run --dry-run --json
dev-memory-cli session-scan run
dev-memory-cli session-scan stats --json
dev-memory-cli session-scan history --limit 20
dev-memory-cli session-scan show <run-id>
dev-memory-cli session-scan uninstall
```

`dev-memory-cli ui` 的“会话扫描”视图读取同一账本，展示仓库扫描次数、会话数量、原始大小、新增数据量和每次总结 token。

## 接入边界

这个仓库是 skill suite，不是自动注入所有项目的独立插件：

- Claude 把 [hooks/hooks.json](hooks.json) 合并到 `.claude/settings.local.json`。
- Codex CLI 把 [hooks/codex-hooks.json](codex-hooks.json) 合并到 `.codex/hooks.json`。
- Trae 把 [hooks/trae-hooks.json](trae-hooks.json) 合并到 `.trae/hooks.json`。
- Trae CN 把同一模板合并到 `.trae-cn/hooks.json`；模板声明 Trae 要求的 `version: 1`。
- Codex Desktop 通过本地 rollout 定时扫描覆盖，不依赖项目 hook。
- 其他仓库需要先安装 CLI，再安装对应 hook；hook 运行时统一调用 `dev-memory-cli hook ...`。

## 原则

- hook 只做低摩擦恢复和轻量刷新
- 不在 hook 里重写高语义正文
- 不把实现流水账和 Git 历史复制进 dev-memory
