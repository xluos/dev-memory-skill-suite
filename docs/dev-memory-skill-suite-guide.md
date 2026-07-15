# Dev Memory Skill Suite 使用说明

## 套件作用

`dev-memory-skill-suite` 是一套围绕 Git 仓库和 Git 分支工作的开发资产技能。

它解决的问题不是“怎么写代码”，而是：

- 怎么让当前分支的工作记忆跨会话可恢复
- 怎么让同一个仓库下多个分支共享稳定资料入口和长期背景
- 怎么避免把仓库内目录变成会被误提交的本地状态目录

## 新的存储模型

这套设计现在使用“用户目录下的 repo + branch 双层存储”，而不是把主记忆写在仓库里的 `.dev-memory/`。

默认目录结构：

```text
~/.dev-memory/repos/<repo-key>/
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
```

其中：

- `repo/`：仓库级共享记忆
- `branches/<branch>/`：分支级工作记忆
- `repo-key`：根据仓库身份生成，优先参考 remote，不只依赖目录名

## 为什么不用仓库内 `.dev-memory/`

主记忆放在仓库里有几个问题：

- 会污染工作区
- 多个 clone 或 worktree 难以共享
- 一直需要处理“要不要提交本地记忆目录”
- repo 级共享层一旦出现，继续放在仓库里意义会下降

所以新的默认选择是：

- 主记忆放到用户目录
- 仓库内 Git 历史继续由 Git 管
- 当前有效记忆由 dev-memory 管

## 两层记忆分别放什么

### `repo/`

只放跨分支稳定成立的东西，例如：

- 长期目标与边界
- 仓库级关键约束
- 长期有效背景
- 跨分支通用决策
- 共享文档入口、评审入口、资料链接

### `branches/<branch>/`

只放当前分支独有的东西，例如：

- 当前目标
- 范围边界
- 当前阶段
- 当前进展
- 阻塞与注意点
- 下一步
- 分支级 workaround / handoff
- 分支级 Git 导航信息

## Reading Order

默认恢复顺序不是“一次把所有文件读完”，而是分层：

1. 分支 `overview.md`
2. 分支 `development.md`
3. 分支 `context.md`
4. 需要跨分支稳定背景时，再读 repo `overview.md` / `context.md`
5. 需要原始事实或入口时，再读分支 `sources.md` / repo `sources.md`

核心原则：

- 当前执行先看 branch
- 共享背景按需看 repo
- 原始事实最后才回源

## 常驻 Skill 与按需维护

当前套件只提供一个常驻 Skill：`dev-memory-read`。它只负责主动定位和搜索当前 repo 的记忆文件，不承担 SessionStart 注入，也不写入。

会话里的稳定语义由定期 `session-scan` 在后台对照已有记忆后统一写入。原来的 capture/setup/tidy/graduate 不再作为全局 Skill 暴露：底层 CLI 仍然保留，整理与归档说明由 `maintain` 命令临时注入独立 Agent 会话。

### `dev-memory-read`

主动读取入口。用户说“重新读一下记忆”“之前记的 todo 有更新”“按记忆里的最新口径”时，先跑：

```bash
dev-memory-cli read show --repo <repo-path>
dev-memory-cli read search --repo <repo-path> --query "关键词"
```

它按当前 repo identity 和 branch name 精确计算 `repo_dir` / `branch_dir`，默认只搜当前 branch + repo 共享层。需要跨旧分支排查时，再显式用 `--scope all-branches` 或 `--scope archived`。

它不创建新骨架，不修改文件，不扫描整个用户目录。

### 初始化

记忆是 repo 级 opt-in。首次启用运行：

```bash
dev-memory-cli init --repo <repo-path>
```

初始化是确定性 CLI 动作，不需要 Agent Skill。

### 整理

```bash
dev-memory-cli maintain tidy --repo <repo-path> --scope branch
```

CLI 启动独立交互式 Agent，并注入完整整理流程。该 Agent 处理 unsorted 分类、结构化条目 proposals、HTML 人工审核和 apply 后复核。破坏性修改不得跳过 HTML review。

### 归档

```bash
dev-memory-cli maintain archive --repo <repo-path> --branch <branch-name>
```

CLI 启动独立交互式 Agent，执行 graduate dry-run、harvest 草案审核、显式确认和归档复核。普通开发会话不会因“需求做完了”等自然语言自动触发归档。

### 底层写入

`capture`、`setup`、`tidy`、`graduate` 子命令继续存在，供 session-scan、维护 Agent、脚本和故障修复使用。它们是 CLI 原语，不再是常驻 Skill。

## 设计重点

### 1. repo 共享层不是 branch 的替代品

这套设计不是把 branch memory 取消掉，改成纯 repo memory。

原因很简单：

- 同一个 repo 下不同分支的目标会分叉
- 不同分支的当前阶段会不同
- 下一步和阻塞往往不共享

所以 branch memory 仍然是主执行上下文。

### 2. hook 可以借，但只作为保底刷新层

这里借的是 ECC 那种生命周期 hook，不是 Git hook。

当前这套默认映射是：

- Claude:
  - `SessionStart`：恢复 branch 主记忆，并在需要时补 repo 共享层
  - `PreCompact`：刷新 working-tree 导航，避免 compact 前丢掉最新关注目录
  - `Stop` / `SessionEnd`：只写轻量 HEAD marker 和最近访问分支
- Codex:
  - `SessionStart`：恢复 branch 主记忆，并在需要时补 repo 共享层
  - `Stop`：只写轻量 HEAD marker 和最近访问分支
- Trae / Trae CN:
  - `SessionStart`：恢复 branch 主记忆，并在需要时补 repo 共享层
  - `Stop`：只写轻量 HEAD marker；不伪造当前客户端没有提供的 `SessionEnd`

Trae 国际版和国内版分别使用 `.trae/hooks.json` 与 `.trae-cn/hooks.json`，安装器将它们视为两个独立目标。

不适合让 hook 自动重写高语义正文。

### 3. Git 历史留在 Git

这条规则没有变：

- 做了什么
- 改了哪些文件
- 什么时候改的

这些都优先回 Git 看。

dev-memory 只保留“下次继续时最需要先知道什么”。

## 这套设计的边界

它当前不负责：

- 替代源文档系统
- 长期保存完整会话流水账
- 在 dev assets 里复制所有提交历史
- 自动抓取外部链接正文并全文归档
- 自动理解图片、附件、录音等非文本资产

它最适合：

- 同一仓库下长期推进多个分支
- 同一需求会跨会话继续
- 希望同仓库分支共享稳定资料入口，但不共享当前工作态
