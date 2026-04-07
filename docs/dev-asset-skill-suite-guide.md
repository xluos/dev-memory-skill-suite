# Dev Asset Skill Suite 使用说明

## 套件作用

`dev-asset-skill-suite` 是一套围绕 Git 分支工作的开发资产技能。

它解决的核心问题不是“怎么写代码”，而是“怎么让一个分支上的需求背景持续可复用”。当一个需求跨多个会话、多个阶段推进时，用户不需要每次重新补充 PRD、评审结论、技术方案、测试口径和当前实现状态，技能会围绕 `.dev-assets/<branch>/` 这套目录持续读取、补充和同步这些信息。

适合的场景：

- 一个分支会持续推进同一个需求或同一类需求点
- 需求背景、技术约束、测试口径会在多个会话中反复引用
- 希望把“聊天里说过的重要信息”沉淀成可复用资产，而不是只留在对话历史里
- 提交代码时，希望顺手把开发过程和提交记录同步下来

## 套件整体工作方式

套件以当前 Git 分支为边界，在仓库里维护：

```text
.dev-assets/<branch>/
  overview.md
  prd.md
  review-notes.md
  frontend-design.md
  backend-design.md
  test-cases.md
  development.md
  decision-log.md
  commits.md
  artifacts/
  manifest.json
```

其中：

- `overview.md`：高层摘要、当前阶段、重要背景
- `prd.md`：需求目标、范围、验收口径
- `review-notes.md`：评审结论、争议点、action items
- `frontend-design.md`：前端页面范围、交互、状态说明
- `backend-design.md`：接口、模型、兼容性、发布影响
- `test-cases.md`：主流程、边界场景、回归口径
- `development.md`：当前需求点、实现备注、风险与 Git 自动同步区
- `decision-log.md`：后续会反复引用的结论、约束、取舍
- `commits.md`：提交记录
- `manifest.json`：分支级元信息

## 每个 Skill 的作用

### `using-dev-assets`

套件总入口。  
它不直接写资产，而是负责在 Git 仓库对话开始时判断当前应该走哪条路径。

典型职责：

- 继续已有分支工作时，路由到 `dev-assets-context`
- 新分支第一次开始时，路由到 `dev-assets-setup`
- 用户主动补充背景信息时，路由到 `dev-assets-update`
- 用户提到提交时，路由到 `dev-assets-sync`

### `dev-assets-setup`

初始化当前分支的资产目录，并主动向用户收集后续会复用的资料。

典型会收集：

- PRD / 需求文档
- 评审记录
- 前端方案
- 后端方案
- 测试用例
- 相关链接、限制条件、背景摘要

它的重点不是“建目录”，而是“把后续要反复引用的材料第一次收进来”。

### `dev-assets-context`

在继续开发前恢复当前分支上下文。

它会：

- 定位当前分支资产目录
- 刷新 `development.md` 的 Git 自动区
- 默认先读 `overview.md` 和 `development.md`
- 按需补读 `prd.md`、`review-notes.md`、`frontend-design.md`、`backend-design.md`、`test-cases.md`

它解决的问题是：开始工作前，先把“这个分支已经知道什么、还缺什么”搞清楚，而不是直接冲进代码。

### `dev-assets-update`

用于主动补充、修正、沉淀新信息。

当用户在会话中补充了新的背景、结论、约束、风险、测试口径、链接或方案时，这个 skill 会把信息整理后写入最合适的资产文件，而不是只留在聊天记录里。

它解决的问题是：需求推进过程中新增的信息，不能只靠初始化一次收集完，必须支持中途持续沉淀。

### `dev-assets-sync`

用于提交相关检查点的同步。

当用户提到提交、commit、stage、commit message 时，这个 skill 会：

- 刷新 `development.md`
- 更新 `manifest.json`
- 在有新提交时把最新 commit 写入 `commits.md`

它解决的问题是：提交不只是 Git 动作，也是开发资产的自然归档时机。

## 推荐使用流程

### 流程 1：新分支第一次开始

1. 进入仓库并开始处理一个新分支上的需求
2. `using-dev-assets` 判断当前分支还没有资产目录
3. 路由到 `dev-assets-setup`
4. 初始化 `.dev-assets/<branch>/`
5. 向用户收集 PRD、评审、前后端方案、测试用例等信息
6. 把资料整理写入对应文件

### 流程 2：继续已有分支工作

1. 开始继续一个已经做过的需求
2. `using-dev-assets` 路由到 `dev-assets-context`
3. 刷新 `development.md` 的 Git 自动区
4. 先读取 `overview.md` 和 `development.md`
5. 再按需读取专项文档
6. 明确当前已知信息、缺失信息，再开始改代码或分析问题

### 流程 3：中途主动补充信息

1. 用户补充了新的背景、约束、评审结论、测试口径或方案
2. `using-dev-assets` 路由到 `dev-assets-update`
3. 选择最合适的目标文件
4. 把信息整理成后续可复用的表达并写入
5. 刷新 `manifest.json`

### 流程 4：提交前后同步

1. 用户提到提交、生成 commit message、准备 stage 或提交后补记录
2. `using-dev-assets` 路由到 `dev-assets-sync`
3. 先同步 working tree
4. 有新提交时记录到 `commits.md`
5. 必要时把关键结论补到 `decision-log.md`

## 这套设计的边界

这套技能的前提是“分支名 = 资产目录名”。这让触发和读取都更直接，但也意味着它更适合“一个分支持续围绕同一需求推进”的工作方式。

它当前不负责的事情包括：

- 自动抓取外部链接正文并结构化归档
- 自动理解图片、附件、录音等非文本资产
- 脱离 Git 分支维度做任务映射

如果后面要继续扩展，最有价值的方向不是继续堆目录，而是补一层 ingestion 能力，让文档链接、会议纪要、测试附件能更自动地进资产目录。

## 安装方式

查看仓库中的可用技能：

```bash
npx skills add xluos/dev-asset-skill-suite --list
```

为 Codex 全局安装整套技能：

```bash
npx skills add xluos/dev-asset-skill-suite --skill '*' -a codex -g -y
```

为所有已检测到的 agent 安装整套技能：

```bash
npx skills add xluos/dev-asset-skill-suite --all -g -y
```
