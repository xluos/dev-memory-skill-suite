# Lifecycle Hooks

这套仓库不再使用 Git hooks。

现在采用的是接近 ECC 的生命周期 hooks，并同时支持 Claude 和 Codex 两种 agent。

## 当前仓库里的接入方式

- 推荐的 repo-local 落地点：
  - Claude: `.claude/settings.local.json`
  - Codex: `.codex/hooks.json`
- 可复用模板：
  - Claude: [hooks/hooks.json](hooks.json)
  - Codex: [hooks/codex-hooks.json](codex-hooks.json)
- `@xluos/dev-assets-cli` 是这两套配置共用的稳定执行入口
- 是否自动生效取决于你本地是否把对应配置文件落到了各自约定位置，而不是模板文件本身

### Codex 快速安装

在目标仓库根目录执行：

```bash
sh scripts/install_codex_hooks.sh
```

如果只是想直接从 GitHub 拉脚本并把模板 merge 到当前目录的 `.codex/hooks.json`：

```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/xluos/dev-assets-skill-suite/main/scripts/install_codex_hooks.sh)"
```

这个脚本会先安装 `@xluos/dev-assets-cli`，然后再 merge Codex hooks。

如果 CLI 已经存在，也可以直接执行：

```bash
npx dev-assets install-hooks codex
npx dev-assets install-hooks claude
```

## 这些 hook 做什么

- Claude:
  - `SessionStart`
  - `PreCompact`
  - `Stop`
  - `SessionEnd`
- Codex:
  - `SessionStart`
  - `Stop`

语义分别是：

- `SessionStart`
  读取当前 repo+branch 的 dev-assets，并把可恢复上下文注入新会话
- `PreCompact`
  在上下文压缩前刷新 working-tree 派生导航信息
- `Stop`
  在每次回复后记录轻量 HEAD 标记
- `SessionEnd`
  在会话结束时再落一次最终 HEAD 标记

## 和 ECC 的差异

ECC 是插件形态，安装后可以靠插件机制自动加载 hook 配置。

这个仓库当前是 skill suite，不是独立 Claude 插件，所以：

- 对本仓库自身开发：
  - Claude 把 [hooks/hooks.json](hooks.json) 合并到本地 `.claude/settings.local.json`
  - Codex 把 [hooks/codex-hooks.json](codex-hooks.json) 放到或合并到本地 `.codex/hooks.json`
- 对其他仓库：
  - 先安装 `@xluos/dev-assets-cli`
  - 再把模板 merge 到对应 repo-local 配置
  - hook 运行时统一走 `npx dev-assets hook ...`
- 不能假装成“像插件一样对所有仓库自动加载”

## 原则

- hook 只做低摩擦恢复和轻量刷新
- 不在 hook 里重写高语义正文
- 不把实现流水账和 Git 历史复制进 dev-assets
