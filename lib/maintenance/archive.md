# 分支记忆归档维护流程

目标是把已完成分支中真正跨分支复用的知识提炼到 repo 共享层，然后归档该分支的记忆目录。归档是破坏性显式动作，必须由用户确认。

## 硬规则

- 始终只处理提示中指定的 repo + branch。
- 所有命令都使用提示中给出的 CLI 路径，并显式传 `--repo`、必要时传 `--branch`。
- 禁止跳过 dry-run 直接 apply。
- 当前分支存在未提交改动、领先默认基线且尚未 merge，或仍在开发时，不得擅自归档；先向用户说明并确认。
- pending-promotion 只是候选，不是必选。业务专用事实不得上提到 repo 共享层。
- 上提决策必须保留 Why 与影响范围，并改写成脱离当前分支名称仍可理解的最终事实。

## 第一阶段：pre-flight

运行：

```bash
<CLI> graduate dry-run --repo <REPO> [--branch <BRANCH>]
```

检查并向用户报告：

- `git_status.ahead`
- `git_status.uncommitted`
- 默认基线与当前分支
- `archive_destination`
- 主审核面 `primary_sources.pending-promotion.md`
- 主审核面 `primary_sources.decisions.md`
- 其他 `cross_check_sources` 中可能遗漏的跨分支经验

若 pre-flight 暴露未 merge 或未提交状态，停止在确认点，不要继续生成 apply。

## 第二阶段：生成 harvest 草案

只提炼真正跨分支稳定成立的内容，生成：

```json
{
  "repo_overview": [
    {"section": "长期目标与边界", "body": "...", "mode": "append"}
  ],
  "repo_decisions": [
    {"section": "跨分支通用决策", "body": "...", "mode": "append"}
  ],
  "repo_glossary": [
    {"section": "长期有效背景", "body": "...", "mode": "append"},
    {"section": "共享入口", "body": "...", "mode": "append"}
  ],
  "notes": "从目标分支提炼",
  "archive": true
}
```

提炼时：

- 去掉当前需求名、临时人员分工、一次性状态和提交流水。
- 保留通用约束、反直觉风险、长期资料入口和可复用流程。
- 对照现有 repo 共享记忆去重；旧共享结论失效时应改成最终口径，不能制造两条冲突规则。
- 默认 `mode=append`，除非已经确认需要更新已有 section。

把 harvest 草案完整展示给用户，包括“会上提什么”和“只归档、不做上提什么”。获得明确确认之前禁止 apply。

## 第三阶段：apply

用户确认草案与归档目标后运行：

```bash
<CLI> graduate apply --repo <REPO> [--branch <BRANCH>] --harvest-file <HARVEST>
```

apply 会串行写入 repo 共享层，生成 `archive_summary.md`，并把分支目录移动到 `branches/_archived/<branch>__<date>/`，同时更新 `_archived/INDEX.md`。

## 第四阶段：复核

完成后必须：

1. 读取 `archive_summary.md`。
2. 运行 `<CLI> graduate index --repo <REPO>` 确认归档索引存在。
3. 检查原 branch_dir 已移动、archive destination 存在。
4. 汇总实际上提条目、归档路径和任何跳过项。

如果 apply 报 branch 不存在、schema 漂移或并发归档冲突，停止并报告真实错误；不得强行移动目录或手写 INDEX 绕过 CLI。
