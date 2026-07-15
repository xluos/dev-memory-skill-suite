#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const PACKAGE_ROOT = path.resolve(__dirname, "..");
const DEFAULT_STORAGE_ROOT = path.join(os.homedir(), ".dev-memory", "repos");
const DEFAULT_CONFIG_PATH = process.env.DEV_MEMORY_CONFIG_PATH || path.join(os.homedir(), ".dev-memory", "config.json");
const WORKSPACE_CONFIG_NAME = ".dev-memory-workspace.json";
const SUPPORTED_HOOK_AGENTS = ["codex", "claude", "trae", "trae-cn"];

function fail(message) {
  process.stderr.write(`ERROR: ${message}\n`);
  process.exit(1);
}

function parseArgs(argv) {
  const positional = [];
  const options = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      positional.push(arg);
      continue;
    }
    const key = arg.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith("--")) {
      options[key] = next;
      i += 1;
    } else {
      options[key] = true;
    }
  }
  return { positional, options };
}

function findPython() {
  for (const name of ["python3", "python"]) {
    const probe = spawnSync(name, ["--version"], { encoding: "utf8" });
    if (probe.status === 0) {
      return name;
    }
  }
  fail("python3 is required");
}

function packageScript(...parts) {
  return path.join(PACKAGE_ROOT, ...parts);
}

function runPython(scriptPath, args, cwd = process.cwd(), extraEnv = {}) {
  const python = findPython();
  // Only forward explicit storage-root env vars from the parent shell. Don't
  // inject DEFAULT_STORAGE_ROOT here — that would short-circuit Python's
  // fallback chain (which prefers legacy ~/.dev-assets if it has data and
  // ~/.dev-memory does not).
  const env = {
    ...process.env,
    DEV_MEMORY_CLI_PATH: process.env.DEV_MEMORY_CLI_PATH || path.resolve(process.argv[1]),
    ...extraEnv,
  };
  const result = spawnSync(python, [scriptPath, ...args], {
    cwd,
    env,
    encoding: "utf8",
    stdio: ["inherit", "pipe", "pipe"],
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function runPythonCapture(scriptPath, args, cwd = process.cwd(), extraEnv = {}) {
  const python = findPython();
  const env = { ...process.env, ...extraEnv };
  const result = spawnSync(python, [scriptPath, ...args], {
    cwd,
    env,
    encoding: "utf8",
    stdio: ["inherit", "pipe", "pipe"],
  });
  return { status: result.status, stdout: result.stdout || "", stderr: result.stderr || "" };
}

function buildSessionStartContext(repoRoot) {
  const script = packageScript("scripts", "hooks", "session_start.py");
  const env = {
    DEV_MEMORY_HOOK_REPO_ROOT: repoRoot,
    DEV_ASSETS_HOOK_REPO_ROOT: repoRoot,
  };
  runPython(script, [], repoRoot, env);
}

function runHookAction(action, repoRoot) {
  const scriptMap = {
    "session-start": packageScript("scripts", "hooks", "session_start.py"),
    "pre-compact": packageScript("scripts", "hooks", "pre_compact.py"),
    stop: packageScript("scripts", "hooks", "stop.py"),
    "session-end": packageScript("scripts", "hooks", "session_end.py"),
  };
  const script = scriptMap[action];
  if (!script) {
    fail(`unknown hook action: ${action}`);
  }
  const env = {
    DEV_MEMORY_HOOK_REPO_ROOT: repoRoot,
    DEV_ASSETS_HOOK_REPO_ROOT: repoRoot,
  };
  runPython(script, [], repoRoot, env);
}

function loadJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, data) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
}

function commandExists(command) {
  const result = spawnSync("sh", ["-lc", `command -v ${command}`], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
  return result.status === 0 && Boolean((result.stdout || "").trim());
}

function detectSessionSummaryCommand() {
  if (commandExists("coco")) {
    return {
      provider: "coco",
      command: "coco -p --yolo --session-id {summary_session_id} {prompt}",
    };
  }
  if (commandExists("codex")) {
    return {
      provider: "codex",
      command: "codex exec --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --sandbox danger-full-access {prompt}",
    };
  }
  if (commandExists("claude")) {
    return {
      provider: "claude",
      command: "claude -p --permission-mode bypassPermissions --session-id {summary_session_uuid} {prompt}",
    };
  }
  return null;
}

function ensureSessionSummaryConfig() {
  const configPath = DEFAULT_CONFIG_PATH;
  const existing = fs.existsSync(configPath) ? loadJson(configPath) : {};
  const config = existing && typeof existing === "object" ? existing : {};
  const sessionSummary = config.session_summary && typeof config.session_summary === "object"
    ? { ...config.session_summary }
    : {};
  if (typeof sessionSummary.command === "string" && sessionSummary.command.trim()) {
    return {
      path: configPath,
      changed: false,
      provider: sessionSummary.provider || null,
      command: sessionSummary.command,
    };
  }
  const detected = detectSessionSummaryCommand();
  if (!detected) {
    return { path: configPath, changed: false, provider: null, command: null };
  }
  config.session_summary = {
    ...sessionSummary,
    provider: detected.provider,
    command: detected.command,
    max_attempts: sessionSummary.max_attempts || 3,
    configured_at: new Date().toISOString(),
    source: "install-hooks:auto-detect",
  };
  writeJson(configPath, config);
  return {
    path: configPath,
    changed: true,
    provider: detected.provider,
    command: detected.command,
  };
}

function templatePathForAgent(agent) {
  if (agent === "codex") return packageScript("hooks", "codex-hooks.json");
  if (agent === "claude") return packageScript("hooks", "hooks.json");
  if (agent === "trae" || agent === "trae-cn") return packageScript("hooks", "trae-hooks.json");
  fail(`unsupported agent: ${agent}`);
}

function targetPathForAgent(agent, repoRoot) {
  if (agent === "codex") return path.join(repoRoot, ".codex", "hooks.json");
  if (agent === "claude") return path.join(repoRoot, ".claude", "settings.local.json");
  if (agent === "trae") return path.join(repoRoot, ".trae", "hooks.json");
  if (agent === "trae-cn") return path.join(repoRoot, ".trae-cn", "hooks.json");
  fail(`unsupported agent: ${agent}`);
}

function globalTargetPathForAgent(agent) {
  if (agent === "codex") return path.join(os.homedir(), ".codex", "hooks.json");
  if (agent === "claude") return path.join(os.homedir(), ".claude", "settings.json");
  if (agent === "trae") return path.join(os.homedir(), ".trae", "hooks.json");
  if (agent === "trae-cn") return path.join(os.homedir(), ".trae-cn", "hooks.json");
  fail(`unsupported agent: ${agent}`);
}

function hookListIdentity(item) {
  if (!item || typeof item !== "object") return null;
  if (typeof item.id === "string" && item.id) {
    return `id:${item.id}\u0000${item.matcher || ""}`;
  }
  const nestedHooks = Array.isArray(item.hooks) ? item.hooks : [];
  for (const hook of nestedHooks) {
    const command = hook && typeof hook.command === "string" ? hook.command.trim() : "";
    const match = command.match(/(?:^|\s)dev-memory-cli\s+hook\s+(session-start|pre-compact|stop|session-end)(?:\s|$)/);
    if (match) return `dev-memory-command:${match[1]}`;
  }
  return null;
}

function mergeHookLists(existingItems, incomingItems) {
  const merged = existingItems.map((item) => ({ ...item }));
  const index = new Map();
  merged.forEach((item, i) => {
    const key = hookListIdentity(item);
    if (key) index.set(key, i);
  });
  for (const item of incomingItems) {
    const copied = { ...item };
    const key = hookListIdentity(copied);
    if (key && index.has(key)) {
      merged[index.get(key)] = copied;
    } else {
      if (key) index.set(key, merged.length);
      merged.push(copied);
    }
  }
  return merged;
}

function mergeConfig(existingConfig, templateConfig) {
  const result = { ...templateConfig, ...existingConfig };
  const existingHooks = existingConfig.hooks || {};
  const templateHooks = templateConfig.hooks || {};
  const mergedHooks = {};
  for (const eventName of [...new Set([...Object.keys(existingHooks), ...Object.keys(templateHooks)])].sort()) {
    mergedHooks[eventName] = mergeHookLists(existingHooks[eventName] || [], templateHooks[eventName] || []);
  }
  result.hooks = mergedHooks;
  return result;
}

function commandHook(positional, options) {
  const action = positional[0];
  const repoRoot = path.resolve(options.repo || process.cwd());
  runHookAction(action, repoRoot);
}

function installHooksForAgent(agent, options) {
  const isGlobal = Boolean(options.global);
  const template = loadJson(templatePathForAgent(agent));
  let targetPath;
  let scope;
  let repoRoot = null;
  if (isGlobal) {
    targetPath = globalTargetPathForAgent(agent);
    scope = "global";
  } else {
    repoRoot = path.resolve(options.repo || process.cwd());
    targetPath = targetPathForAgent(agent, repoRoot);
    scope = "repo";
  }
  const existing = fs.existsSync(targetPath) ? loadJson(targetPath) : {};
  const merged = mergeConfig(existing, template);
  writeJson(targetPath, merged);
  const summaryConfig = ensureSessionSummaryConfig();
  const report = {
    agent,
    scope,
    target: targetPath,
    events: Object.keys(template.hooks || {}),
    session_summary_config: summaryConfig,
  };
  if (repoRoot) report.repo_root = repoRoot;
  return report;
}

function commandInstallHooks(positional, options) {
  const isAll = Boolean(options.all);
  const agents = isAll ? SUPPORTED_HOOK_AGENTS : [positional[0] || options.agent || "codex"];
  const reports = agents.map((agent) => installHooksForAgent(agent, options));
  process.stdout.write(`${JSON.stringify(isAll ? reports : reports[0], null, 2)}\n`);
}

function commandUi(_positional, options) {
  const { start } = require(path.join(PACKAGE_ROOT, "lib", "ui-server.js"));
  const host = options.host || "127.0.0.1";
  const port = options.port != null && options.port !== true ? Number(options.port) : 0;
  const openBrowserFlag = !options["no-open"];
  const readOnly = !!options["read-only"];
  start({ host, port, openBrowserFlag, readOnly });
}

function listWorkspaceRepos(workspaceRoot) {
  if (!fs.existsSync(workspaceRoot) || !fs.statSync(workspaceRoot).isDirectory()) {
    return [];
  }
  return fs.readdirSync(workspaceRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => path.join(workspaceRoot, entry.name))
    .filter((entryPath) => fs.existsSync(path.join(entryPath, ".git")))
    .sort((a, b) => path.basename(a).localeCompare(path.basename(b)));
}

function readWorkspaceConfig(workspaceRoot) {
  const configPath = path.join(workspaceRoot, WORKSPACE_CONFIG_NAME);
  if (!fs.existsSync(configPath)) return {};
  const data = loadJson(configPath);
  return data && typeof data === "object" && !Array.isArray(data) ? data : {};
}

function commandWorkspace(rawArgs) {
  const sub = rawArgs[0];
  const { positional, options } = parseArgs(rawArgs.slice(1));
  const workspaceRoot = path.resolve(options.workspace || options.repo || process.cwd());
  const repos = listWorkspaceRepos(workspaceRoot);
  const repoNames = repos.map((repoPath) => path.basename(repoPath));

  if (!sub || sub === "--help" || sub === "-h") {
    process.stdout.write(`Usage:
  dev-memory-cli workspace show [--workspace PATH]
  dev-memory-cli workspace primary <repo-basename> [--workspace PATH]
`);
    return;
  }

  if (sub === "show") {
    const config = readWorkspaceConfig(workspaceRoot);
    process.stdout.write(`${JSON.stringify({
      workspace: workspaceRoot,
      config_path: path.join(workspaceRoot, WORKSPACE_CONFIG_NAME),
      primary_repo: config.primary_repo || null,
      repos: repoNames,
    }, null, 2)}\n`);
    return;
  }

  if (sub === "primary") {
    const primary = positional[0];
    if (!primary || primary === true) {
      fail("workspace primary requires a repo basename");
    }
    if (!repoNames.includes(primary)) {
      fail(`repo '${primary}' not found under ${workspaceRoot}. Available: ${repoNames.join(", ") || "(none)"}`);
    }
    const configPath = path.join(workspaceRoot, WORKSPACE_CONFIG_NAME);
    const config = readWorkspaceConfig(workspaceRoot);
    config.primary_repo = primary;
    config.updated_at = new Date().toISOString();
    writeJson(configPath, config);
    process.stdout.write(`${JSON.stringify({
      workspace: workspaceRoot,
      config_path: configPath,
      primary_repo: primary,
    }, null, 2)}\n`);
    return;
  }

  fail(`unknown workspace command: ${sub}`);
}

// Subcommands that delegate straight to the Python lib/ scripts.
// Their CLIs (subcommands, flags) are owned by argparse on the Python
// side, so we deliberately bypass parseArgs and forward raw argv.
const PY_SUBCOMMAND_SCRIPTS = {
  read: "dev_memory_read.py",
  context: "dev_memory_context.py",
  capture: "dev_memory_capture.py",
  setup: "dev_memory_setup.py",
  graduate: "dev_memory_graduate.py",
  tidy: "dev_memory_tidy.py",
  summary: "dev_memory_summary.py",
  "session-scan": "dev_memory_session_scan.py",
};

function commandPySubcommand(name, rawArgs) {
  const scriptPath = packageScript("lib", PY_SUBCOMMAND_SCRIPTS[name]);
  if (!fs.existsSync(scriptPath)) {
    fail(`missing lib script: ${scriptPath}`);
  }
  runPython(scriptPath, rawArgs);
}

const MAINTENANCE_MODES = new Set(["tidy", "archive"]);
const MAINTENANCE_MARKER = "DEV_MEMORY_INTERNAL_MAINTENANCE_AGENT_V1";

function maintenanceHelp() {
  process.stdout.write(`Usage:
  dev-memory-cli maintain tidy [--repo PATH] [--branch NAME] [--scope branch|branch+repo]
                                  [--executor auto|codex|coco] [--model MODEL]
  dev-memory-cli maintain archive [--repo PATH] [--branch NAME]
                                     [--executor auto|codex|coco] [--model MODEL]
  dev-memory-cli maintain <tidy|archive> --print-prompt [other options]

The command starts a dedicated interactive maintenance-agent session. Tidy
must not apply destructive changes before HTML review; archive must not apply
before dry-run review and explicit confirmation.
`);
}

function resolveMaintenanceExecutor(requested) {
  const name = requested || process.env.DEV_MEMORY_MAINTENANCE_EXECUTOR || "auto";
  if (!new Set(["auto", "codex", "coco"]).has(name)) {
    fail(`unsupported maintenance executor: ${name}`);
  }
  if (name !== "auto") return name;
  for (const candidate of ["codex", "coco"]) {
    if (commandExists(candidate)) return candidate;
  }
  fail("no maintenance executor found; install codex/coco or use --print-prompt");
}

function buildMaintenancePrompt(mode, options, repoRoot) {
  const promptPath = packageScript("lib", "maintenance", `${mode}.md`);
  if (!fs.existsSync(promptPath)) fail(`missing maintenance prompt: ${promptPath}`);
  const workflow = fs.readFileSync(promptPath, "utf8").trim();
  const cliPath = path.resolve(process.argv[1]);
  const branch = options.branch || "<current-git-branch>";
  const scope = mode === "tidy" ? (options.scope || "branch") : "branch";
  return `${MAINTENANCE_MARKER}

你是 dev-memory 的专用维护 Agent。本会话只处理下面指定仓库的记忆维护，不承担普通开发任务。

目标仓库：${repoRoot}
目标分支：${branch}
维护模式：${mode}
整理范围：${scope}
本次必须使用的 CLI：node ${JSON.stringify(cliPath)}

不要依赖全局 dev-memory capture/setup/tidy/graduate Skill；完整维护流程已经随本提示提供。
涉及删除、改写或归档时，必须遵守下面流程中的人工审核和确认门禁。

${workflow}
`;
}

function commandMaintain(rawArgs) {
  const mode = rawArgs[0];
  if (!mode || mode === "--help" || mode === "-h") {
    maintenanceHelp();
    return;
  }
  if (!MAINTENANCE_MODES.has(mode)) {
    fail(`unknown maintenance mode: ${mode}`);
  }
  const { positional, options } = parseArgs(rawArgs.slice(1));
  if (positional.length) fail(`unexpected maintenance argument: ${positional[0]}`);
  const repoRoot = path.resolve(options.repo || process.cwd());
  if (!fs.existsSync(repoRoot) || !fs.statSync(repoRoot).isDirectory()) {
    fail(`maintenance repo does not exist: ${repoRoot}`);
  }
  if (mode === "tidy" && options.scope && !new Set(["branch", "branch+repo"]).has(options.scope)) {
    fail(`unsupported tidy scope: ${options.scope}`);
  }
  const prompt = buildMaintenancePrompt(mode, options, repoRoot);
  if (options["print-prompt"] || options["dry-run"]) {
    process.stdout.write(prompt);
    return;
  }

  const executor = resolveMaintenanceExecutor(options.executor);
  let args;
  if (executor === "codex") {
    const modelArgs = options.model ? ["--model", String(options.model)] : [];
    args = [
      "-C", repoRoot,
      "--sandbox", "danger-full-access",
      "--ask-for-approval", "on-request",
      "--dangerously-bypass-hook-trust",
      ...modelArgs,
      prompt,
    ];
  } else {
    const modelArgs = options.model ? ["--config", `model=${String(options.model)}`] : [];
    args = ["--permission-mode", "default", ...modelArgs, prompt];
  }
  const result = spawnSync(executor, args, {
    cwd: repoRoot,
    env: {
      ...process.env,
      DEV_MEMORY_MAINTENANCE_AGENT: "1",
      DEV_MEMORY_MAINTENANCE_MODE: mode,
    },
    stdio: "inherit",
  });
  if (result.error) fail(`unable to start ${executor}: ${result.error.message}`);
  if (result.status !== 0) process.exit(result.status || 1);
}

function branchScript() {
  return packageScript("lib", "dev_memory_branch.py");
}

function callBranchPython(args) {
  const result = runPythonCapture(branchScript(), args);
  if (result.status !== 0) {
    let message = result.stderr.trim() || `branch op failed (exit ${result.status})`;
    try {
      const parsed = JSON.parse(result.stderr.trim());
      if (parsed && parsed.error) message = parsed.error;
    } catch (_) {
      // not json — keep raw stderr
    }
    return { ok: false, error: message };
  }
  try {
    return { ok: true, data: JSON.parse(result.stdout.trim()) };
  } catch (err) {
    return { ok: false, error: `unable to parse branch output: ${err.message}` };
  }
}

function loadClack() {
  try {
    return require("@clack/prompts");
  } catch (_) {
    fail(
      "@clack/prompts is not installed. Run `npm install` inside the dev-memory-cli "
      + "package, or use the non-interactive forms: dev-memory-cli branch rename|fork ...",
    );
  }
}

function relativeAge(iso) {
  if (!iso) return "未初始化";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diff = Date.now() - t;
  const min = Math.round(diff / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} 天前`;
  const mon = Math.round(day / 30);
  return `${mon} 个月前`;
}

function describeBranchRow(row) {
  const tags = [];
  if (row.git_exists) tags.push("git");
  if (!row.memory_exists) tags.push("无记忆");
  else if (row.is_skeleton) tags.push("空骨架");
  else tags.push(`${row.entry_count} 条记忆`);
  if (row.last_updated) tags.push(relativeAge(row.last_updated));
  return tags.join(" · ");
}

async function runConflictPrompt(p, target) {
  const used = target.memory_exists && !target.is_skeleton;
  if (!used) return { mode: null };
  const choice = await p.select({
    message: `目标分支 ${target.name} 已有 ${target.entry_count} 条记忆，怎么处理？`,
    options: [
      { value: "backup", label: "备份后覆盖", hint: "移到 _archived/ 后再迁移（推荐）" },
      { value: "force", label: "强制覆盖", hint: "直接删除，无法恢复" },
      { value: "cancel", label: "返回上一步" },
    ],
    initialValue: "backup",
  });
  if (p.isCancel(choice) || choice === "cancel") return { cancelled: true };
  return { mode: choice };
}

function buildPlanArgs(plan) {
  const args = [plan.op];
  if (plan.op === "rename" || plan.op === "fork") {
    args.push("--source", plan.source, "--target", plan.target);
  } else if (plan.op === "delete" || plan.op === "init") {
    args.push("--branch", plan.branch);
  }
  if (plan.mode === "force") args.push("--force");
  if (plan.mode === "backup") args.push("--backup");
  return args;
}

function summarizePlan(plan) {
  if (plan.op === "rename" || plan.op === "fork") {
    return `${plan.op}: ${plan.source} → ${plan.target}` + (plan.mode ? ` · 冲突处理: ${plan.mode}` : "");
  }
  return `${plan.op}: ${plan.branch} · 模式: ${plan.mode}`;
}

function describePlanResult(plan, data) {
  if (plan.op === "rename" || plan.op === "fork") {
    return `已完成 ${plan.op}：${data.source} → ${data.target}`;
  }
  if (plan.op === "delete") {
    return `已删除 ${data.branch} 的记忆（${data.mode}）`;
  }
  return `已重置 ${data.branch}（${data.mode}）`;
}

async function pickBranchAutocomplete(p, message, options, { allowManual = false } = {}) {
  const finalOptions = options.slice();
  if (allowManual) {
    finalOptions.push({
      value: "__manual__",
      label: "手动输入分支名…",
      hint: "新分支或不在列表里",
    });
  }
  const picked = await p.autocomplete({
    message,
    options: finalOptions,
    placeholder: "输入关键字过滤，↑↓ 选择，回车确认",
    maxItems: 8,
  });
  if (p.isCancel(picked)) return null;
  if (picked === "__manual__") {
    const typed = await p.text({
      message: "输入分支名",
      validate: (v) => (!v || !v.trim() ? "不能为空" : undefined),
    });
    if (p.isCancel(typed)) return null;
    return typed.trim();
  }
  return picked;
}

async function pickDestructiveMode(p, message) {
  const choice = await p.select({
    message,
    options: [
      { value: "backup", label: "备份后执行", hint: "移到 _archived/，可恢复（推荐）" },
      { value: "force", label: "强制执行", hint: "直接销毁，无法恢复" },
      { value: "cancel", label: "返回上一步" },
    ],
    initialValue: "backup",
  });
  if (p.isCancel(choice) || choice === "cancel") return null;
  return choice;
}

async function interactiveFromUsedBranch(p, snapshot) {
  const action = await p.select({
    message: `当前分支 ${snapshot.current_branch} 已有 ${snapshot.current.entry_count} 条记忆，要做什么？`,
    options: [
      { value: "rename", label: "迁移到另一个分支", hint: "rename — 当前分支记忆消失" },
      { value: "fork", label: "复制到另一个分支", hint: "fork — 当前分支保留" },
      { value: "init", label: "重置当前分支", hint: "回到空骨架" },
      { value: "delete", label: "删除当前分支的记忆", hint: "整个目录移走/销毁" },
      { value: "cancel", label: "取消" },
    ],
    initialValue: "fork",
  });
  if (p.isCancel(action) || action === "cancel") return null;

  if (action === "delete" || action === "init") {
    const mode = await pickDestructiveMode(
      p,
      action === "delete"
        ? `确认删除 ${snapshot.current_branch} 的 ${snapshot.current.entry_count} 条记忆？`
        : `确认重置 ${snapshot.current_branch}（${snapshot.current.entry_count} 条记忆将丢失）？`,
    );
    if (!mode) return null;
    return { op: action, branch: snapshot.current_branch, mode };
  }

  // rename / fork
  const candidates = snapshot.branches
    .filter((b) => b.name !== snapshot.current_branch)
    .map((b) => ({ value: b.name, label: b.name, hint: describeBranchRow(b) }));

  const target = await pickBranchAutocomplete(p, "目标分支：", candidates, { allowManual: true });
  if (!target) return null;

  const targetRow = snapshot.branches.find((b) => b.name === target) || {
    name: target,
    memory_exists: false,
    is_skeleton: true,
    deviations: [],
    entry_count: 0,
  };
  const conflict = await runConflictPrompt(p, targetRow);
  if (conflict.cancelled) return null;

  return { op: action, source: snapshot.current_branch, target, mode: conflict.mode };
}

async function interactiveFromEmptyBranch(p, snapshot) {
  const candidates = snapshot.branches
    .filter((b) => b.name !== snapshot.current_branch && b.memory_exists && !b.is_skeleton)
    .map((b) => ({ value: b.name, label: b.name, hint: describeBranchRow(b) }));
  if (candidates.length === 0) {
    p.log.warn("没找到其他分支有可迁移的记忆，无事可做。");
    return null;
  }
  const source = await pickBranchAutocomplete(
    p,
    `当前分支 ${snapshot.current_branch} 还没用过，从哪个分支接力？`,
    candidates,
  );
  if (!source) return null;

  const op = await p.select({
    message: "方式：",
    options: [
      { value: "fork", label: "fork — 复制过来", hint: `${source} 保留` },
      { value: "rename", label: "rename — 搬过来", hint: `${source} 消失` },
    ],
    initialValue: "fork",
  });
  if (p.isCancel(op)) return null;

  // Current branch is, by construction, an empty skeleton, so no conflict prompt
  // is needed — python's _resolve_conflict will silently overwrite it.
  return { op, source, target: snapshot.current_branch, mode: null };
}

async function commandBranchInteractive() {
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    fail(
      "interactive branch flow requires a TTY. Use the non-interactive form instead:\n"
      + "  dev-memory-cli branch list\n"
      + "  dev-memory-cli branch rename --source A --target B [--force | --backup]\n"
      + "  dev-memory-cli branch fork   --source A --target B [--force | --backup]",
    );
  }
  const p = loadClack();
  p.intro("dev-memory · 分支记忆迁移");

  const listed = callBranchPython(["list"]);
  if (!listed.ok) {
    p.cancel(listed.error);
    process.exit(1);
  }
  const snapshot = listed.data;
  if (!snapshot.current_branch) {
    p.cancel("当前 HEAD 处于游离状态，请先 checkout 一个分支再试。");
    process.exit(1);
  }
  const current = snapshot.branches.find((b) => b.name === snapshot.current_branch);
  if (!current) {
    p.cancel(`未在分支列表中找到当前分支 ${snapshot.current_branch}。`);
    process.exit(1);
  }
  snapshot.current = current;

  let plan;
  if (current.memory_exists && !current.is_skeleton) {
    plan = await interactiveFromUsedBranch(p, snapshot);
  } else {
    plan = await interactiveFromEmptyBranch(p, snapshot);
  }
  if (!plan) {
    p.cancel("已取消。");
    process.exit(0);
  }

  const confirmed = await p.confirm({
    message: `确认执行 ${summarizePlan(plan)}？`,
    initialValue: true,
  });
  if (p.isCancel(confirmed) || !confirmed) {
    p.cancel("已取消。");
    process.exit(0);
  }

  const result = callBranchPython(buildPlanArgs(plan));
  if (!result.ok) {
    p.log.error(result.error);
    p.outro("失败");
    process.exit(1);
  }
  p.log.success(describePlanResult(plan, result.data));
  const dirField = result.data.target_dir || result.data.branch_dir;
  if (dirField) p.log.info(`目录：${dirField}`);
  p.outro("done");
}

function commandBranch(rawArgs) {
  const sub = rawArgs[0];
  // No subcommand → interactive mode.
  if (!sub || sub === "--help" || sub === "-h") {
    if (sub === "--help" || sub === "-h") {
      process.stdout.write(`Usage:
  dev-memory-cli branch                          # interactive flow
  dev-memory-cli branch list                     # JSON snapshot of all branches
  dev-memory-cli branch inspect [--branch NAME]  # JSON snapshot of one branch
  dev-memory-cli branch rename --source A --target B [--force | --backup]
  dev-memory-cli branch fork   --source A --target B [--force | --backup]
  dev-memory-cli branch delete [--branch NAME] [--force | --backup]
  dev-memory-cli branch init   [--branch NAME] [--force | --backup]
  dev-memory-cli branch inherit-worktree-base [--source NAME] [--branch NAME] [--force | --backup]
                                             # explicit worktree-base inheritance (auto-fires on first lazy-init)
`);
      return;
    }
    commandBranchInteractive();
    return;
  }
  // Any other subcommand is forwarded to python verbatim — the python side
  // owns the flag parsing.
  runPython(branchScript(), rawArgs);
}

function printHelp() {
  process.stdout.write(`Usage:
  dev-memory-cli hook <session-start|pre-compact|stop|session-end> [--repo PATH]
  dev-memory-cli install-hooks <codex|claude|trae|trae-cn> [--repo PATH] [--global]
  dev-memory-cli install-hooks --all [--repo PATH] [--global]
  dev-memory-cli ui [--port N] [--host HOST] [--no-open] [--read-only]
  dev-memory-cli workspace <show|primary> [...]
  dev-memory-cli init [--repo PATH] [--branch NAME]
  dev-memory-cli read <show|search> [...]
  dev-memory-cli maintain <tidy|archive> [...]   # starts a dedicated interactive agent
  dev-memory-cli context <show|...> [...]
  # Low-level mutation/admin commands used by session-scan and maintenance agents:
  dev-memory-cli capture <record|show|sync-working-tree|record-head|suggest-kind|classify> [...]
  dev-memory-cli setup <init|merge-unsorted|mark-completed> [...]
  dev-memory-cli graduate <dry-run|apply|index> [...]
  dev-memory-cli tidy <prepare|apply> [...]
  dev-memory-cli summary <extract-core> [...]
  dev-memory-cli session-scan <run|install|status|stats|history|show|uninstall|config> [...]
  dev-memory-cli branch [list|inspect|rename|fork|delete|init|inherit-worktree-base] [...]   # no subcommand = interactive

Environment:
  DEV_MEMORY_ROOT defaults to ${DEFAULT_STORAGE_ROOT}
  (also accepts DEV_ASSETS_ROOT for backward-compat with dev-assets <0.13)
`);
}

function main() {
  const argv = process.argv.slice(2);
  const command = argv[0];
  if (!command || command === "-h" || command === "--help") {
    printHelp();
    return;
  }
  if (PY_SUBCOMMAND_SCRIPTS[command]) {
    commandPySubcommand(command, argv.slice(1));
    return;
  }
  if (command === "init") {
    commandPySubcommand("setup", ["init", ...argv.slice(1)]);
    return;
  }
  if (command === "maintain") {
    commandMaintain(argv.slice(1));
    return;
  }
  if (command === "branch") {
    commandBranch(argv.slice(1));
    return;
  }
  // Legacy commands keep using the lightweight Node-side parser.
  const { positional, options } = parseArgs(argv);
  positional.shift();
  if (command === "hook") {
    commandHook(positional, options);
    return;
  }
  if (command === "install-hooks") {
    commandInstallHooks(positional, options);
    return;
  }
  if (command === "ui") {
    commandUi(positional, options);
    return;
  }
  if (command === "workspace") {
    commandWorkspace(argv.slice(1));
    return;
  }
  fail(`unknown command: ${command}`);
}

main();
