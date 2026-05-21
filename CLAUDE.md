# CLAUDE.md

本仓库的 agent 操作约定。

## 发布流程（npm + GitHub Release）

完全自动化 —— 推 `vX.Y.Z` tag 即触发 `.github/workflows/release.yml`：

```bash
npm version patch -m "chore(release): %s"   # 也可 minor / major
git push --follow-tags
```

workflow 跑：

1. `pytest -q` 测试套件
2. 校验 tag 版本号 == `package.json` 版本号（不对就 fail-fast）
3. `npm publish --provenance --access public`，OIDC trusted publisher 走签名 attestation，**不需要 NPM_TOKEN**
4. `softprops/action-gh-release` 自动建 GitHub Release，notes 由 GitHub 生成

也支持 Actions tab 的 workflow_dispatch 用现有 tag 手动重跑（npm 同版本号会拒，所以幂等）。

### 一次性配置（已完成，新克隆者参考）

1. **npm Trusted Publisher**（npmjs.com → 包页 → Settings → Trusted Publisher）：
   - Organization or user: `xluos`
   - Repository: `dev-memory-skill-suite`
   - Workflow filename: `release.yml`（不带路径）
   - Environment: 留空
2. workflow 必须用 **Node 24+**（npm >= 11.5.1 才支持 OIDC trusted publisher；低于此版本 publish 会落回非-OIDC 路径然后 registry 返回 PUT 404）

### `package.json` 必填字段（provenance 校验依赖）

trusted publisher 比老式 NPM_TOKEN publish 多一道校验：npm registry 会比对 attestation 里的 GitHub repo 跟 `package.json.repository.url` 是否一致。任何一项缺失或对不上 → 422 reject，即使 sigstore 那边已经签好。

- `repository.url` 必须是 `git+https://github.com/xluos/dev-memory-skill-suite.git` 这种格式
- `name`、`version`、`license` 不能空

**历史教训**：`v0.18.1` 因为漏 `repository` 字段 422 失败，留下孤儿 tag（npm 没有对应版本）。fix 提交 + bump 到 0.18.2 才成功。

### 提交 / 版本号风格

- conventional commits：`feat(scope): ...`、`fix(scope): ...`、`chore(release): X.Y.Z`、`ci: ...`、`docs(scope): ...`
- `npm version` 一律带 `-m "chore(release): %s"` 保留风格
- commit message **不带** Claude / agent 助手 footer（全局规则）

## 本地开发 / 测试

- Python 3.12+；pytest 没装到系统时用 venv：`/tmp/dms-venv` 是临时方便的位置（`python3 -m venv /tmp/dms-venv && /tmp/dms-venv/bin/pip install pytest`）
- 跑测试：`/tmp/dms-venv/bin/python -m pytest tests/ -q`
- CLI 入口：`bin/dev-memory.js`，下分 `capture` / `tidy` / `graduate` / `context` 等子命令；底层调 `lib/dev_memory_*.py`

## Skill / 设计参考

- `AGENTS.md` 是 v1-era 设计文档，部分概念（如 `dev-memory-context` / `dev-memory-sync` / `dev-memory-update`）已被淘汰
- 现行架构看 `README.md` 和 `docs/dev-memory-skill-suite-guide.md`
- 各 skill 的 SKILL.md 在 `skills/<skill-name>/SKILL.md`
