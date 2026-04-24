#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const PACKAGE_ROOT = path.resolve(__dirname, "..");
const DEFAULT_STORAGE_ROOT = path.join(os.homedir(), ".dev-assets", "repos");

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
  const env = {
    ...process.env,
    DEV_ASSETS_ROOT: process.env.DEV_ASSETS_ROOT || DEFAULT_STORAGE_ROOT,
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

function buildSessionStartContext(repoRoot) {
  const script = packageScript("scripts", "hooks", "session_start.py");
  const env = {
    DEV_ASSETS_HOOK_REPO_ROOT: repoRoot,
    DEV_ASSETS_ROOT: process.env.DEV_ASSETS_ROOT || DEFAULT_STORAGE_ROOT,
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
    DEV_ASSETS_HOOK_REPO_ROOT: repoRoot,
    DEV_ASSETS_ROOT: process.env.DEV_ASSETS_ROOT || DEFAULT_STORAGE_ROOT,
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

function templatePathForAgent(agent) {
  if (agent === "codex") return packageScript("hooks", "codex-hooks.json");
  if (agent === "claude") return packageScript("hooks", "hooks.json");
  fail(`unsupported agent: ${agent}`);
}

function targetPathForAgent(agent, repoRoot) {
  if (agent === "codex") return path.join(repoRoot, ".codex", "hooks.json");
  if (agent === "claude") return path.join(repoRoot, ".claude", "settings.local.json");
  fail(`unsupported agent: ${agent}`);
}

function globalTargetPathForAgent(agent) {
  if (agent === "codex") return path.join(os.homedir(), ".codex", "hooks.json");
  if (agent === "claude") return path.join(os.homedir(), ".claude", "settings.json");
  fail(`unsupported agent: ${agent}`);
}

function mergeHookLists(existingItems, incomingItems) {
  const merged = existingItems.map((item) => ({ ...item }));
  const index = new Map();
  merged.forEach((item, i) => index.set(`${item.id || ""}\u0000${item.matcher || ""}`, i));
  for (const item of incomingItems) {
    const copied = { ...item };
    const key = `${copied.id || ""}\u0000${copied.matcher || ""}`;
    if (index.has(key)) {
      merged[index.get(key)] = copied;
    } else {
      index.set(key, merged.length);
      merged.push(copied);
    }
  }
  return merged;
}

function mergeConfig(existingConfig, templateConfig) {
  const result = { ...existingConfig };
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
  const report = { agent, scope, target: targetPath, events: Object.keys(template.hooks || {}) };
  if (repoRoot) report.repo_root = repoRoot;
  return report;
}

function commandInstallHooks(positional, options) {
  const isAll = Boolean(options.all);
  const agents = isAll ? ["codex", "claude"] : [positional[0] || options.agent || "codex"];
  const reports = agents.map((agent) => installHooksForAgent(agent, options));
  process.stdout.write(`${JSON.stringify(isAll ? reports : reports[0], null, 2)}\n`);
}

function commandUi(_positional, options) {
  const { start } = require(path.join(PACKAGE_ROOT, "lib", "ui-server.js"));
  const host = options.host || "127.0.0.1";
  const port = options.port != null && options.port !== true ? Number(options.port) : 0;
  const openBrowserFlag = !options["no-open"];
  start({ host, port, openBrowserFlag });
}

function printHelp() {
  process.stdout.write(`Usage:
  dev-assets hook <session-start|pre-compact|stop|session-end> [--repo PATH]
  dev-assets install-hooks <codex|claude> [--repo PATH] [--global]
  dev-assets install-hooks --all [--repo PATH] [--global]
  dev-assets ui [--port N] [--host HOST] [--no-open]

Environment:
  DEV_ASSETS_ROOT defaults to ${DEFAULT_STORAGE_ROOT}
`);
}

function main() {
  const { positional, options } = parseArgs(process.argv.slice(2));
  const command = positional.shift();
  if (!command || command === "-h" || command === "--help") {
    printHelp();
    return;
  }
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
  fail(`unknown command: ${command}`);
}

main();
