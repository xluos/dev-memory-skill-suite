# Storage Layout

Default storage root:

`~/.dev-assets/repos/<repo-key>/`

Core layout:

- `repo/overview.md`: 仓库共享概览
- `repo/context.md`: 仓库共享上下文
- `repo/sources.md`: 仓库共享资料入口
- `repo/manifest.json`: repo 级元信息
- `branches/<branch>/overview.md`: 分支目标 / 范围 / 阶段 / 约束
- `branches/<branch>/development.md`: 分支当前工作态与 Git 自动同步块
- `branches/<branch>/context.md`: 分支 why / caveat / handoff
- `branches/<branch>/sources.md`: 分支级资料入口与 Git 历史入口
- `branches/<branch>/manifest.json`: 分支级元信息
- `branches/<branch>/artifacts/history/`: 分支归档附件或历史产物
