---
name: dev-assets-setup
description: Use when a branch starts a new requirement stream or when the current repository has no dev-assets entry for the current branch yet, and Codex should initialize user-home repo+branch memory storage, then collect the branch's current summary and shared source entry points. Also use in non-git working directories that need persistent project memory — setup detects no-git mode and degrades to a single repo-shared layer keyed off a `.dev-assets-id` dotfile, no branch layer is created.
---

# Dev Assets Setup

为当前 Git 仓库初始化用户目录下的 repo+branch 开发记忆骨架，并在初始化后主动向用户收集最小但关键的资料。

**Workspace mode：** 初始化始终针对单个仓库。cwd 是多 repo workspace 时，必须通过 `--repo <basename>` 明确指定目标仓库，每个新仓库分别调用一次；绝不做批量自动初始化，避免用户意外污染不相关的仓库。

**Announce at start:** 用一句简短的话说明将先初始化当前仓库的 repo+branch 记忆目录。

## Workflow

1. 确认当前目录位于 Git 仓库内。
2. 运行 `scripts/init_dev_assets.py --repo <repo-path>` 初始化 repo 共享层和当前 branch 层。
3. 告诉用户创建出的 `repo_dir`、`branch_dir` 和关键文件清单。
4. 主动索要缺失资料，优先顺序：
   - 当前目标
   - 范围边界
   - 当前阶段
   - 关键约束
   - 已知风险或阻塞
   - 共享源文档 / 链接 / 代码入口
5. 收到资料后，优先调用 `dev-assets-update` 重写最合适的 section。

## Command

```bash
python3 /absolute/path/to/dev-assets-setup/scripts/init_dev_assets.py --repo <repo-path>
```

可选参数：

- `--context-dir ~/.dev-assets/repos`
- `--branch <branch-name>`

## What Gets Created

默认目录结构：

```text
~/.dev-assets/repos/<repo-key>/
  repo/
  branches/<branch>/
```

脚本会：

- 为当前 repo 生成稳定的 `repo-key`
- 初始化 repo 共享层和当前 branch 层
- 将 storage root 写入本地 Git config：`dev-assets.root`
- 在检测到旧 `.dev-assets/<branch>/` 时迁移 branch 文件

## Intake Rules

- 主动问用户要源文档或链接，不要要求用户把整份文档重新手打一遍。
- branch 层只保留当前分支有效记忆。
- repo 层只保留跨分支共享资料和长期背景。
- 如果用户只给零散信息，先整理成当前有效摘要，再写入对应文件。

## Always / Never

**Always:**

- 初始化 repo 共享层和当前 branch 层
- 初始化后立即告诉用户缺哪些关键入口信息
- 优先收“当前摘要 + 共享资料入口”
- 让用户目录下的 dev-assets 成为主记忆入口

**Never:**

- 只建目录不做资料 intake
- 继续复制 `prd / review / frontend / backend / test` 一整套正文
- 把仓库工作区当成本地记忆主目录
