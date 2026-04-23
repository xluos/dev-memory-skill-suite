# Dev Assets Skill Suite 使用说明

## 套件作用

`dev-assets-skill-suite` 是一套围绕 Git 仓库和 Git 分支工作的开发资产技能。

它解决的问题不是“怎么写代码”，而是：

- 怎么让当前分支的工作记忆跨会话可恢复
- 怎么让同一个仓库下多个分支共享稳定资料入口和长期背景
- 怎么避免把仓库内目录变成会被误提交的本地状态目录

## 新的存储模型

这套设计现在使用“用户目录下的 repo + branch 双层存储”，而不是把主记忆写在仓库里的 `.dev-assets/`。

默认目录结构：

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
```

其中：

- `repo/`：仓库级共享记忆
- `branches/<branch>/`：分支级工作记忆
- `repo-key`：根据仓库身份生成，优先参考 remote，不只依赖目录名

## 为什么不用仓库内 `.dev-assets/`

主记忆放在仓库里有几个问题：

- 会污染工作区
- 多个 clone 或 worktree 难以共享
- 一直需要处理“要不要提交本地记忆目录”
- repo 级共享层一旦出现，继续放在仓库里意义会下降

所以新的默认选择是：

- 主记忆放到用户目录
- 仓库内 Git 历史继续由 Git 管
- 当前有效记忆由 dev-assets 管

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

## 各 Skill 现在的职责

### `using-dev-assets`

总入口，只负责判断当前应该走哪条路：

- 继续已有分支工作 → `dev-assets-context`
- 新分支第一次开始 → `dev-assets-setup`
- 当前记忆需要补充或纠正 → `dev-assets-update`
- 到达提交相关检查点或阶段性里程碑 → `dev-assets-sync`

### `dev-assets-setup`

初始化用户目录下的 repo+branch 结构。

它会：

- 创建 repo 层和 branch 层骨架
- 为当前仓库记录 storage root
- 在存在旧 `.dev-assets/<branch>/` 时迁移 branch 资料

### `dev-assets-context`

恢复当前分支记忆，并轻量刷新 branch 的 Git 自动区。

它不会重建整个记忆，只负责：

- 找到当前 repo-key / branch 目录
- 读已有记忆
- 刷新 focus areas、scope summary、HEAD 元信息

### `dev-assets-update`

把当前会话里已经稳定的新理解写回记忆。

默认：

- 分支级目标、进展、风险、下一步 → branch 文件
- 共享资料入口 → repo `sources.md`

另外也支持显式写共享层：

- `shared-overview`
- `shared-constraint`
- `shared-context`
- `shared-decision`
- `shared-source`

### `dev-assets-sync`

在提交相关检查点沉淀 durable memory。

它的重点不是记历史，而是保留：

- 当前进展
- 风险
- 下一步
- 关键决策
- 新增资料入口

Git 自动导航仍然主要落在 branch 层，repo 层只更新轻量元信息。

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

不适合让 hook 自动重写高语义正文。

### 3. Git 历史留在 Git

这条规则没有变：

- 做了什么
- 改了哪些文件
- 什么时候改的

这些都优先回 Git 看。

dev-assets 只保留“下次继续时最需要先知道什么”。

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
