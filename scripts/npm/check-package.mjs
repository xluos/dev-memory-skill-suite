#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..");

function fail(message) {
  process.stderr.write(`ERROR: ${message}\n`);
  process.exit(1);
}

function run(cmd, args, options = {}) {
  const result = spawnSync(cmd, args, {
    cwd: repoRoot,
    encoding: "utf8",
    stdio: ["inherit", "pipe", "pipe"],
    ...options,
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) {
    fail(`${cmd} ${args.join(" ")} failed`);
  }
}

for (const relPath of [
  "package.json",
  "bin/dev-memory.js",
  "lib/ui-server.js",
  "lib/ui-app.html",
  "lib/assets/tidy_review.html",
  "lib/dev_memory_common.py",
  "lib/dev_memory_context.py",
  "lib/dev_memory_capture.py",
  "lib/dev_memory_setup.py",
  "lib/dev_memory_graduate.py",
  "lib/dev_memory_tidy.py",
  "lib/dev_memory_branch.py",
  "lib/dev_memory_summary.py",
  "hooks/hooks.json",
  "hooks/codex-hooks.json",
  "suite-manifest.json",
  "scripts/hooks/session_start.py",
  "scripts/hooks/session_summary_worker.py",
  "scripts/hooks/pre_compact.py",
  "scripts/hooks/stop.py",
  "scripts/hooks/session_end.py",
]) {
  if (!fs.existsSync(path.join(repoRoot, relPath))) {
    fail(`missing required file: ${relPath}`);
  }
}

run("node", ["--check", "bin/dev-memory.js"]);
run("node", ["--check", "lib/ui-server.js"]);
run("python3", [
  "-m",
  "py_compile",
  "lib/dev_memory_common.py",
  "lib/dev_memory_context.py",
  "lib/dev_memory_capture.py",
  "lib/dev_memory_setup.py",
  "lib/dev_memory_graduate.py",
  "lib/dev_memory_tidy.py",
  "lib/dev_memory_branch.py",
  "lib/dev_memory_summary.py",
  "scripts/hooks/_common.py",
  "scripts/hooks/session_summary_worker.py",
  "scripts/hooks/session_start.py",
  "scripts/hooks/pre_compact.py",
  "scripts/hooks/stop.py",
  "scripts/hooks/session_end.py",
]);

JSON.parse(fs.readFileSync(path.join(repoRoot, "hooks/hooks.json"), "utf8"));
JSON.parse(fs.readFileSync(path.join(repoRoot, "hooks/codex-hooks.json"), "utf8"));
JSON.parse(fs.readFileSync(path.join(repoRoot, "suite-manifest.json"), "utf8"));

const pkg = JSON.parse(fs.readFileSync(path.join(repoRoot, "package.json"), "utf8"));
if (pkg.name !== "dev-memory-cli") {
  fail(`package name must be dev-memory-cli, got ${pkg.name}`);
}
if (!pkg.bin || pkg.bin["dev-memory-cli"] !== "bin/dev-memory.js") {
  fail("package bin must expose dev-memory-cli -> bin/dev-memory.js");
}
if (Object.prototype.hasOwnProperty.call(pkg.bin, "dev-memory")) {
  fail("package bin must not expose dev-memory; that npm command name is occupied");
}

process.stdout.write("Package checks passed.\n");
