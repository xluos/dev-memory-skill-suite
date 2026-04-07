---
name: dev-asset-setup
description: Use when a branch starts a new requirement stream or when the current branch has no development asset directory yet, and Codex should initialize a branch-named asset directory, then actively collect reusable materials such as PRD, review notes, technical plans, test cases, links, and branch summary.
---

# Dev Asset Setup

为当前 Git 分支初始化一个“开发资产目录”，并在初始化后主动向用户索要后续会复用的资料。

**Announce at start:** `我先用 dev-asset-setup 为当前分支初始化开发资产目录，并补齐核心资料槽位。`

**Core principle:** 初始化不只是建目录，还要主动把后续会反复引用的资料收进来。

## Workflow

1. 确认当前目录位于 Git 仓库内。
2. 运行 `scripts/init_dev_assets.py --repo <repo-path>` 初始化当前分支目录。
3. 告诉用户创建出的目录和资产文件清单。
4. 主动索要缺失资料，优先顺序：
   - PRD / 需求文档
   - 评审记录
   - 前端方案
   - 后端方案
   - 测试用例
   - 相关链接与限制条件
5. 把用户给出的内容整理后写入对应文件，不要把所有内容都塞进一个文件。

## Command

```bash
python3 /absolute/path/to/dev-asset-setup/scripts/init_dev_assets.py --repo <repo-path>
```

可选参数：

- `--context-dir .dev-assets`
- `--branch <branch-name>`

## What Gets Created

目录结构：

`<repo>/.dev-assets/<branch>/`

关键文件：

- `overview.md`
- `prd.md`
- `review-notes.md`
- `frontend-design.md`
- `backend-design.md`
- `test-cases.md`
- `development.md`
- `decision-log.md`
- `commits.md`
- `artifacts/`

脚本还会：

- 将目录写入本地 Git config：`dev-assets.dir`
- 自动把 `.dev-assets/` 加入 `.git/info/exclude`

## Intake Rules

- 主动问用户要资料，不要只让用户自己回忆模板字段。
- 缺资料时优先问“你有文档/链接吗”，不要直接让用户重新手打一遍。
- 如果用户只给零散信息，先整理进正确文件，再在 `overview.md` 更新摘要。
- 不要假设 PRD、方案、测试用例一定同时存在；缺什么就标记什么。

## Always / Never

**Always:**

- 把目录按分支名初始化
- 初始化后立即告诉用户缺哪些核心资料
- 优先收文档、链接、现成记录，而不是要求用户重新组织一遍
- 将不同类型资料分散到对应文件，而不是堆在 `overview.md`

**Never:**

- 只建目录不做资料 intake
- 把评审记录、技术方案、测试口径混写在一个文件里
- 假装已经理解需求，只因为目录初始化完成了
