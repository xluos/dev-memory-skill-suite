# Diagram sources

README 中的架构图和流程图由两类可维护产物组成：

- `svg-source/*.svg`：可直接编辑、渲染和预览的主源文件。
- `whiteboard-source/*.json`：由飞书画板 CLI 转换的 OpenAPI 节点数据，可用于画板导入或后续同步。

根目录下的 `*.png` 是 README 使用的渲染结果。修改 SVG 后，使用 `@larksuite/whiteboard-cli` 重新执行检查、PNG 渲染和 OpenAPI 转换，避免手工维护多份图形内容。

```bash
npx -y @larksuite/whiteboard-cli@^0.2.12 -i docs/diagrams/svg-source/overview.svg -f svg --check
npx -y @larksuite/whiteboard-cli@^0.2.12 -i docs/diagrams/svg-source/overview.svg -o docs/diagrams/overview.png -f svg
npx -y @larksuite/whiteboard-cli@^0.2.12 -i docs/diagrams/svg-source/overview.svg -f svg --to openapi --format json
```
