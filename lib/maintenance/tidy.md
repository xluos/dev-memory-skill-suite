# 记忆整理维护流程

目标是把未分类内容归位，并校准已经结构化但陈旧、重复、错误或残留模板的记忆。整理是显式维护任务，不在普通开发会话中自动触发。

## 硬规则

- 始终只处理提示中指定的单个 repo + branch。
- 所有命令都使用提示中给出的 CLI 路径，并显式传 `--repo`、必要时传 `--branch`。
- 未分类内容可以在用户确认分类后 merge；结构化内容的删除和改写必须先生成 HTML review，由用户审核并导出 plan，禁止直接 apply。
- 不复制 PRD、聊天流水或 Git 历史；只保留下次开发仍有价值的决策、风险、术语、约束、入口与文件导航。
- `progress.md` 的 `AUTO-GENERATED` 块不得删除或改写。

## 第一阶段：处理 unsorted

先运行：

```bash
<CLI> setup init --repo <REPO> [--branch <BRANCH>]
```

如果 `unsorted_entries` 非空：

1. 把相关条目聚合后给出高置信度分类建议。
2. 可选 kind：`decision`、`risk`、`glossary`、`source`、`shared-decision`、`shared-context`、`shared-source`、`skip`。
3. 让用户确认或修正分类；未确认前不要 merge。
4. 生成 `classifications` plan，并运行 `setup merge-unsorted --plan-file ...`。

如果 unsorted 为空，不需要为了修改 `setup_completed` 制造额外步骤；继续结构化整理即可。

## 第二阶段：扫描结构化记忆

运行：

```bash
<CLI> tidy prepare --repo <REPO> [--branch <BRANCH>] --scope <SCOPE>
```

`SCOPE` 使用启动提示中的 `branch` 或 `branch+repo`。第一次 prepare 后优先读取输出中的 `annotated_md`，不要只看 stdout 的扁平 entries。它保留了：

- entry id
- block 边界
- section 结构
- Why/How 等 orphan paragraph
- STALE / ORPHAN hints

## 第三阶段：生成事项级 proposals

把相关条目聚合成约 3～8 个 proposal，每个 proposal 必须包含 `id`、`priority`、`title`、`reason`、`actions`。优先使用粗粒度动作：

1. `reset-file`
2. `delete-section`
3. `delete-block`
4. `delete-entries`
5. `edit-entries`

不要把每个 entry 单独做成 proposal。同一原因、同一结果的动作应合并。删除 block 时尽量附带 prepare 输出的 `expected_content_hash`，防止用户中途修改文件后误删。

priority 口径：

- `P0`：继续保留会误导下一次开发
- `P1`：强烈建议清理
- `P2`：建议清理
- `P3`：可选
- `P4`：可有可无

把 proposals 写入临时 JSON，再次运行：

```bash
<CLI> tidy prepare --repo <REPO> [--branch <BRANCH>] --scope <SCOPE> --proposals-file <FILE>
```

## 第四阶段：人工审核门禁

把 `review_html` / `open_url` 告诉用户，引导用户逐个选择：

- accept：按原方案执行
- reject：保持不动
- custom：给出新的处理意见

用户审核后从 HTML 导出 plan.json，并把路径交回本会话。没有用户导出的 plan 文件，禁止调用 `tidy apply`。

## 第五阶段：apply 与复核

用户明确提供已审核 plan 后运行：

```bash
<CLI> tidy apply --repo <REPO> [--branch <BRANCH>] --plan-file <PLAN>
```

apply 会先备份，再按 reset → section → block → entry 的顺序执行。完成后必须读取生成的 `summary_*.md`，核对 accepted/rejected/custom、实际 rewritten/deleted 和 invalid actions。

custom proposal 不会自动 apply。根据反馈决定是直接精确改写、解释后再确认、保持不动，还是重新生成 proposals；不要机械重复整轮 prepare。

## 完成输出

最终向用户说明：

- unsorted 分类合并了多少条
- HTML 审核了多少个 proposal
- 实际改写、删除、重置的数量
- 备份和 summary 路径
- 因漂移校验而跳过的动作
