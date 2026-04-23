# Dev Assets Skill Suite

面向 Codex、Claude 等 agent 运行时的 **repo + branch 双层开发记忆** 技能套件。

这套仓库只做一件事：把"开发记忆"从 Git 工作区里拿出来，放到用户目录下，按 `(仓库身份, 分支)` 作为主 key 维护，让跨会话的开发上下文可恢复、可修正、可沉淀、可归档，同时不污染工作区、不和 Git 历史互相干扰。

## v2 架构：5 个 Skill

v2 把旧的 sync + update 合并成统一的 capture，整套从 6 个 skill 降到 5 个，同时改 setup 为 merge 动作、加 lazy init、加 v1→v2 自动迁移。

| Skill | 定位 | 典型触发 |
| --- | --- | --- |
| `using-dev-assets` | 总入口路由器，决定走哪个子 skill | 任何 Git 仓库 / no-git 项目开发对话开头 |
| `dev-assets-setup` | 整理 unsorted.md + 补元信息 + 标 setup_completed（不再是前置门禁） | unsorted.md 累积、用户明确说"整理一下" |
| `dev-assets-context` | 恢复当前分支记忆，按 tiered lookup 顺序读 | 继续已有分支开发 |
| `dev-assets-capture` | **统一写入入口**（合并 sync + update） | 本轮产生稳定结论 / checkpoint / 用户手动记一笔 / 改写旧条目 |
| `dev-assets-graduate` | 分支收尾：从 pending-promotion 提炼上提 + 归档 branch | 用户显式说"归档 / 分支收尾 / merge 完清一下" |

详细设计与语义见：

- [docs/dev-assets-skill-suite-guide.md](docs/dev-assets-skill-suite-guide.md) — 套件整体说明
- [docs/workspace-mode.md](docs/workspace-mode.md) — 多 repo workspace 模式

### Lazy init 与 setup 的新关系

v2 里 capture 写入永远先 lazy init（骨架不存在就自动建），不再需要前置 setup。setup 的新职责：扫 `unsorted.md` 把未分类条目按用户选择 merge 到 decisions/progress/risks/glossary，再标 `manifest.setup_completed = true`。setup 之前 / 之后的区别是 capture 的 heuristic 兜底策略：之前"不确定 → unsorted"，之后"不确定 → progress"。

### Graduate 为什么必须显式

`dev-assets-graduate` 会做 destructive move（把 `branches/<key>/` 搬到 `branches/_archived/<key>__<date>/`），同时把 branch 记忆里跨分支可复用的知识（剥离业务命名后）上提到 repo 共享层。**只接受用户显式触发**，不做 implicit 调用。在 no-git 模式下直接拒绝（没有分支概念）。

## 运行模式

套件会根据当前工作目录自动切换运行模式，存储布局 key 始终是 `(仓库身份, 分支)`：

| 模式 | 触发条件 | 行为 |
| --- | --- | --- |
| 单 repo | cwd 本身是 git 仓库 | 最原始行为，所有 hook/skill 直接作用于当前 repo+branch |
| Workspace | cwd 不是 git 仓库，但第一级子目录里至少有一个 git 仓库 | SessionStart 为 primary 仓库注入完整记忆 + 其它仓库简短概览；Stop/PreCompact/SessionEnd 对每个仓库各记一次 HEAD；skill 通过 `--repo <basename>` 明确目标仓库，或读 `DEV_ASSETS_PRIMARY_REPO` 作为默认 |
| No-git | cwd 不是 git 仓库，也不是 workspace | 在当前目录落一个 `.dev-assets-id` dotfile 作为仓库身份，分支层退化成单一共享层（sentinel `_no_git`），`dev-assets-graduate` 此模式下直接拒绝 |

`DEV_ASSETS_PRIMARY_REPO` 接受**仓库目录 basename**（不是绝对路径）。

## 存储布局

默认存储在仓库外的用户目录：

```text
~/.dev-assets/repos/<repo-key>/
  repo/
    overview.md
    context.md
    sources.md
    manifest.json
  branches/
    <branch>/
      overview.md
      development.md
      context.md
      sources.md
      manifest.json
      artifacts/
        history/
    _archived/
      <branch>__<YYYYMMDD>/
      INDEX.md
```

- `repo/`：跨分支稳定成立的共享记忆（长期目标、关键约束、共享资料入口等）
- `branches/<branch>/`：分支级工作记忆（当前目标、进展、风险、下一步等）
- `branches/_archived/`：`dev-assets-graduate` 归档产物
- `repo-key`：优先按仓库 remote 身份派生，不只看目录名；支持多 clone / worktree 共享同一套记忆
- `DEV_ASSETS_ROOT`：覆盖默认 `~/.dev-assets/repos`；CLI、所有 hook 脚本、所有 skill 脚本都尊重此环境变量

## 安装

### 1. 通过 `npx skills` 安装 skill 套件

列出可用 skill：

```bash
npx skills add xluos/dev-assets-skill-suite --list
```

全量装到 Codex 全局：

```bash
npx skills add xluos/dev-assets-skill-suite --skill '*' -a codex -g -y
```

为检测到的所有 agent 装一遍：

```bash
npx skills add xluos/dev-assets-skill-suite --all -g -y
```

### 2. 安装生命周期 hook

推荐先把 `@xluos/dev-assets-cli` 装成全局命令，再在目标仓库合并 hook：

```bash
npm install -g @xluos/dev-assets-cli                 # 一次
dev-assets install-hooks codex                       # 在目标仓库内（默认 cwd）
dev-assets install-hooks claude
```

装到 agent 用户级配置而不是每个 repo：

```bash
dev-assets install-hooks codex --global              # 写入 ~/.codex/hooks.json
dev-assets install-hooks claude --global             # 写入 ~/.claude/settings.json
```

用 `--all` 一次装两种 agent：

```bash
dev-assets install-hooks --all                       # 两个 agent，repo 级
dev-assets install-hooks --all --global              # 两个 agent，用户级
```

没装全局 CLI 时，也可以 `npx` 按需下载：

```bash
npx -y @xluos/dev-assets-cli install-hooks codex
npx -y @xluos/dev-assets-cli install-hooks claude --global
```

Shell 包装器（`scripts/install_codex_hooks.sh`、`scripts/install_claude_hooks.sh`）只是上面命令的 shell 入口，适合偏好 shell 的环境。

## 生命周期 Hook

这套不再使用 Git hook，改用 ECC 风格的生命周期 hook，Claude 和 Codex 都支持：

| 事件 | Claude | Codex | 做什么 |
| --- | :-: | :-: | --- |
| `SessionStart` | ✅ | ✅ | 恢复 repo+branch 记忆并注入新会话 |
| `PreCompact` | ✅ | ✕ | 压缩前刷新 working-tree 派生导航 |
| `Stop` | ✅ | ✅ | 每次回复后落一个轻量 HEAD marker |
| `SessionEnd` | ✅ | ✕ | 会话结束时再落一次最终 HEAD |

重要边界：

- 本仓库只提供**模板 + CLI**，真正生效的是你本地 `.codex/hooks.json` / `.claude/settings.local.json` / `~/.codex/hooks.json` / `~/.claude/settings.json` 里有没有合并进来
- hook 运行时统一走 `dev-assets hook ...`，所以 CLI 必须在 PATH 上或可被 `npx` 解析
- hook 只做**低摩擦恢复 + 轻量刷新**，不在 hook 里重写高语义正文
- 全局 skill 安装不会自动加载 hook —— 这是一个 skill suite，不是独立 agent 插件

## 两条调用链（别混淆）

同一个套件里有两个看起来相似但不应混淆的入口：

- **生命周期 hook** → 走 `dev-assets hook <session-start|pre-compact|stop|session-end>`，由 `.codex/hooks.json` / `.claude/settings.local.json` 自动触发；这是唯一正确使用 `dev-assets` CLI 的场景
- **对话内 skill 工作流**（`setup` / `context` / `update` / `sync` / `graduate`）→ 调用每个 skill 自己的 `scripts/<name>.py`；CLI 不包这一层，因为它们是**带 skill 专属参数的交互式工作流**，不是后台 hook 动作

SKILL.md 里写的脚本路径是 `python3 /absolute/path/to/<skill>/scripts/<name>.py` —— agent 在运行时把 `/absolute/path/to/` 替换成运行期 harness 装载该 skill 的真实目录，不要原样把占位符传过去。

## 仓库目录结构

```text
bin/
  dev-assets.js              # `npx dev-assets` CLI 入口（hook + 安装助手）
hooks/
  hooks.json                 # Claude hook 模板（.claude/settings.local.json）
  codex-hooks.json           # Codex hook 模板（.codex/hooks.json）
  README.md
lib/
  dev_asset_common.py        # 所有 hook / skill 脚本共享的公共库
scripts/
  hooks/                     # session_start / pre_compact / stop / session_end — 通过 `dev-assets hook ...` 调用
  install_codex_hooks.sh     # 一键安装 shell 入口；install_claude_hooks.sh 是它的 symlink
  install_claude_hooks.sh -> install_codex_hooks.sh
  install_suite.py           # 本地开发用的 symlink 安装器
  npm/                       # 打包 check/build 助手
skills/
  using-dev-assets/
  dev-assets-setup/
  dev-assets-context/
  dev-assets-capture/
  dev-assets-graduate/
suite-manifest.json          # 套件 + 历史遗留 skill 命名的唯一表
```

每个 skill 内部大致是这样的结构：

```text
skills/<skill-name>/
  SKILL.md                   # 声明 name / description / 工作流（运行时会被 agent 读到）
  scripts/<name>.py          # 对话内工作流的 Python 脚本
  references/*.md            # 辅助参考，仅在 SKILL.md 明确引用时读取
  agents/openai.yaml         # OpenAI 风格 agent 的附加元信息（可选）
```

## 设计要点

- **repo 层不是 branch 层的替代**：同仓库不同分支的目标、阶段、阻塞通常会分叉，所以 branch 记忆仍是主执行上下文，repo 是跨分支稳定背景
- **Git 历史留在 Git**：做了什么、改了哪些文件、什么时候改的 —— 都优先看 `git log` / `git show`，不往 dev-assets 里复制提交流水账
- **共享资料入口放 repo 层**：评审文档、长期设计链接、跨分支规范入口放 `repo/sources.md`；分支独占的热路径 / 优先阅读清单放 `branches/<branch>/sources.md`
- **hook 只保底，不主写**：高语义正文靠 `update` / `sync` / `graduate` 在对话里写，不依赖 hook 自动重写
- **destructive 动作一律显式**：`graduate` 的归档必须用户明确授权，不接受 implicit 触发

## 设计边界

这套**不**负责：

- 替代源文档系统
- 长期保存完整会话流水账
- 在 dev-assets 里复制提交历史
- 自动抓取外部链接正文或做全文归档
- 自动理解图片、附件、录音等非文本资产

它最适合：

- 同一仓库下长期推进多个分支
- 同一需求跨会话继续
- 多分支共享稳定资料入口，但不共享当前工作态
- 跨多 repo workspace 里保持各仓库记忆的隔离 + 聚合

## 许可

见 [LICENSE](LICENSE)。
