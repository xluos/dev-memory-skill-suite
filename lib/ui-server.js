"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn } = require("node:child_process");

const DEFAULT_STORAGE_ROOT = path.join(os.homedir(), ".dev-memory", "repos");
const APP_HTML_PATH = path.join(__dirname, "ui-app.html");

function getStorageRoot() {
  return process.env.DEV_ASSETS_ROOT || DEFAULT_STORAGE_ROOT;
}

function getScanRoot() {
  return process.env.DEV_MEMORY_SCAN_ROOT
    || path.join(path.dirname(getStorageRoot()), "jobs", "session-scan");
}

function safeReadDir(dir) {
  try {
    return fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
}

function safeReadJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

function safeStat(p) {
  try {
    return fs.statSync(p);
  } catch {
    return null;
  }
}

function listTextFiles(dir) {
  const entries = safeReadDir(dir);
  const files = [];
  for (const ent of entries) {
    if (!ent.isFile()) continue;
    if (!ent.name.endsWith(".md") && ent.name !== "manifest.json") continue;
    const full = path.join(dir, ent.name);
    const st = safeStat(full);
    files.push({
      name: ent.name,
      size: st?.size ?? 0,
      mtime: st?.mtime ? st.mtime.toISOString() : null,
    });
  }
  files.sort((a, b) => {
    if (a.name === "manifest.json") return 1;
    if (b.name === "manifest.json") return -1;
    return a.name.localeCompare(b.name);
  });
  return files;
}

function manifestSummary(manifest) {
  if (!manifest) return null;
  return {
    updated_at: manifest.updated_at ?? null,
    last_update_title: manifest.last_update_title ?? null,
    setup_completed: manifest.setup_completed ?? null,
    schema_version: manifest.schema_version ?? null,
    last_seen_head: manifest.last_seen_head ?? null,
    last_recorded_commit: manifest.last_recorded_commit ?? null,
  };
}

function buildBranchInfo(branchDir, branchName, archived) {
  const manifest = safeReadJson(path.join(branchDir, "manifest.json"));
  return {
    name: branchName,
    archived,
    files: listTextFiles(branchDir),
    manifest: manifestSummary(manifest),
  };
}

function buildTree() {
  const root = getStorageRoot();
  const rootStat = safeStat(root);
  if (!rootStat) {
    return { storageRoot: root, exists: false, repos: [] };
  }
  const repos = [];
  for (const ent of safeReadDir(root)) {
    if (!ent.isDirectory()) continue;
    const key = ent.name;
    const repoDir = path.join(root, key);
    const repoManifest = safeReadJson(path.join(repoDir, "repo", "manifest.json"));
    const repoFiles = listTextFiles(path.join(repoDir, "repo"));

    const branchesDir = path.join(repoDir, "branches");
    const activeBranches = [];
    const archivedBranches = [];
    for (const b of safeReadDir(branchesDir)) {
      if (!b.isDirectory()) continue;
      if (b.name === "_archived") {
        const archDir = path.join(branchesDir, "_archived");
        for (const a of safeReadDir(archDir)) {
          if (!a.isDirectory()) continue;
          archivedBranches.push(buildBranchInfo(path.join(archDir, a.name), a.name, true));
        }
      } else {
        activeBranches.push(buildBranchInfo(path.join(branchesDir, b.name), b.name, false));
      }
    }
    activeBranches.sort((a, b) => a.name.localeCompare(b.name));
    archivedBranches.sort((a, b) => a.name.localeCompare(b.name));

    repos.push({
      key,
      repoRoot: repoManifest?.repo_root ?? null,
      identity: repoManifest?.repo_identity ?? null,
      updatedAt: repoManifest?.updated_at ?? null,
      lastSeenBranch: repoManifest?.last_seen_branch ?? null,
      repoLevel: {
        files: repoFiles,
        hasManifest: !!repoManifest,
      },
      branches: activeBranches,
      archived: archivedBranches,
    });
  }
  repos.sort((a, b) => {
    const ta = a.updatedAt ?? "";
    const tb = b.updatedAt ?? "";
    return tb.localeCompare(ta);
  });
  return { storageRoot: root, exists: true, repos };
}

function addCount(counts, key) {
  const label = key || "unknown";
  counts[label] = (counts[label] || 0) + 1;
}

function sessionIsTask(session) {
  return ["candidate", "done", "failed", "skipped_summary"].includes(session?.status)
    || session?.reason === "no_semantic_messages";
}

function classifyRun(run) {
  if (run.status === "skipped_running") return "skipped_running";
  if (Number(run.failed_count || 0) > 0) return "attention";
  if (run.dry_run && Number(run.candidate_count || 0) > 0) return "dry_run";
  if (Number(run.done_count || 0) + Number(run.summary_skipped_count || 0) > 0) return "processed";
  if (Number(run.candidate_count || 0) > 0) return "pending";
  return "idle";
}

function buildRunSummary(run) {
  const reasonCounts = {};
  for (const session of run.sessions || []) {
    if (session.reason) addCount(reasonCounts, session.reason);
  }
  return {
    run_id: run.run_id,
    run_kind: run.run_kind || "scan",
    started_at: run.started_at || null,
    finished_at: run.finished_at || null,
    duration_ms: Number(run.duration_ms || 0),
    scheduled: !!run.scheduled,
    dry_run: !!run.dry_run,
    outcome: classifyRun(run),
    status: run.status || null,
    skip_reason: run.skip_reason || null,
    executor: run.executor || null,
    model: run.model || null,
    session_count: Number(run.session_count || 0),
    candidate_count: Number(run.candidate_count || 0),
    task_count: (run.sessions || []).filter(sessionIsTask).length,
    done_count: Number(run.done_count || 0),
    summary_skipped_count: Number(run.summary_skipped_count || 0),
    failed_count: Number(run.failed_count || 0),
    discovery_skipped_count: Number(run.discovery_skipped_count || 0),
    observed_new_bytes: Number(run.observed_new_bytes || run.new_bytes || 0),
    eligible_new_bytes: Number(run.eligible_new_bytes || 0),
    summary_tokens: Number((run.summary_usage || {}).total_tokens || 0),
    reason_counts: reasonCounts,
  };
}

function buildTaskSummary(session) {
  const touchedTargets = ((session.apply_result || {}).touched_targets || []).map((target) => (
    typeof target === "string" ? target : target.file || target.path || JSON.stringify(target)
  ));
  return {
    session_id: session.session_id || null,
    repo_key: session.repo_key || null,
    branch: session.branch || null,
    cwd: session.cwd || null,
    path: session.path || null,
    status: session.status || null,
    reason: session.reason || null,
    error: session.error || null,
    raw_size: Number(session.raw_size || 0),
    new_bytes: Number(session.new_bytes || 0),
    semantic_messages: Number(session.semantic_messages || 0),
    semantic_chars: Number(session.semantic_chars || 0),
    cursor_before: Number(session.cursor_before || 0),
    cursor_after: session.cursor_after == null ? null : Number(session.cursor_after),
    stop_snapshot_changed: !!session.stop_snapshot_changed,
    summary_tokens: Number((session.summary_usage || {}).total_tokens || 0),
    touched_targets: touchedTargets,
    semantic_action_count: Number(session.semantic_action_count || 0),
  };
}

function buildSessionScanRunDetail(runId) {
  if (typeof runId !== "string" || !/^[0-9A-Za-z_.-]+$/.test(runId)) return null;
  const run = safeReadJson(path.join(getScanRoot(), "runs", `${runId}.json`));
  if (!run || typeof run !== "object") return null;
  const reasonCounts = {};
  const tasks = [];
  for (const session of run.sessions || []) {
    if (session.reason) addCount(reasonCounts, session.reason);
    if (sessionIsTask(session)) tasks.push(buildTaskSummary(session));
  }
  return {
    run: buildRunSummary(run),
    tasks,
    discovery: {
      skipped_count: Number(run.discovery_skipped_count || 0),
      reason_counts: reasonCounts,
    },
    invocations: (run.invocations || []).map((item) => ({
      invocation: item.invocation || null,
      attempt: item.attempt || null,
      returncode: item.returncode ?? null,
      duration_ms: Number(item.duration_ms || 0),
      timed_out: !!item.timed_out,
      error: item.error || null,
      usage: item.usage || null,
    })),
  };
}

function buildSessionScanData({ limit = 100, offset = 0 } = {}) {
  limit = Math.min(500, Math.max(1, Number(limit) || 100));
  offset = Math.max(0, Number(offset) || 0);
  const root = getScanRoot();
  const runsDir = path.join(root, "runs");
  const runs = [];
  for (const ent of safeReadDir(runsDir)) {
    if (!ent.isFile() || !ent.name.endsWith(".json")) continue;
    const value = safeReadJson(path.join(runsDir, ent.name));
    if (value && typeof value === "object") runs.push(value);
  }
  runs.sort((a, b) => String(b.started_at || "").localeCompare(String(a.started_at || "")));
  const repos = new Map();
  const usage = {};
  const totals = {
    candidates: 0,
    done: 0,
    summary_skipped: 0,
    failed: 0,
    discovery_skipped: 0,
    productive_runs: 0,
    attention_runs: 0,
    idle_runs: 0,
    skipped_running: 0,
  };
  let unavailableInvocations = 0;
  for (const run of runs) {
    const summary = buildRunSummary(run);
    totals.candidates += summary.candidate_count;
    totals.done += summary.done_count;
    totals.summary_skipped += summary.summary_skipped_count;
    totals.failed += summary.failed_count;
    totals.discovery_skipped += summary.discovery_skipped_count;
    if (summary.outcome === "processed") totals.productive_runs += 1;
    if (summary.outcome === "attention") totals.attention_runs += 1;
    if (summary.outcome === "idle") totals.idle_runs += 1;
    if (summary.outcome === "skipped_running") totals.skipped_running += 1;
    unavailableInvocations += Number(run.usage_unavailable_invocations || 0);
    for (const [key, value] of Object.entries(run.summary_usage || {})) {
      if (Number.isFinite(value)) usage[key] = (usage[key] || 0) + value;
    }
    for (const session of run.sessions || []) {
      if (!session.repo_key) continue;
      const row = repos.get(session.repo_key) || {
        repo_key: session.repo_key,
        branches: new Set(), sessions: new Set(), scan_count: 0,
        raw_bytes: 0, new_bytes: 0, summary_tokens: 0, done: 0, failed: 0, last_scanned_at: null,
      };
      row.scan_count += 1;
      row.sessions.add(session.session_id);
      if (session.branch) row.branches.add(session.branch);
      row.raw_bytes += Number(session.raw_size || 0);
      row.new_bytes += Number(session.new_bytes || 0);
      row.summary_tokens += Number((session.summary_usage || {}).total_tokens || 0);
      if (session.status === "done") row.done += 1;
      if (session.status === "failed") row.failed += 1;
      if (!row.last_scanned_at || session.last_scanned_at > row.last_scanned_at) {
        row.last_scanned_at = session.last_scanned_at;
      }
      repos.set(session.repo_key, row);
    }
  }
  const repoRows = Array.from(repos.values()).map((row) => ({
    ...row,
    branches: Array.from(row.branches).sort(),
    session_count: row.sessions.size,
    sessions: undefined,
  })).sort((a, b) => String(b.last_scanned_at || "").localeCompare(String(a.last_scanned_at || "")));
  return {
    scanRoot: root,
    exists: !!safeStat(root),
    run_count: runs.length,
    usage: Object.keys(usage).length ? usage : null,
    unavailable_invocations: unavailableInvocations,
    totals,
    repos: repoRows,
    runs: runs.slice(offset, offset + limit).map(buildRunSummary),
    offset,
    has_more: offset + limit < runs.length,
  };
}

function resolveSafePath(relPath) {
  const root = getStorageRoot();
  if (!relPath || typeof relPath !== "string") return null;
  const normalized = path.normalize(relPath).replace(/^[/\\]+/, "");
  if (normalized.split(path.sep).includes("..")) return null;
  const full = path.resolve(root, normalized);
  const rootWithSep = root.endsWith(path.sep) ? root : root + path.sep;
  if (!full.startsWith(rootWithSep)) return null;
  if (!full.endsWith(".md") && !full.endsWith(".json")) return null;
  return full;
}

const MAX_WRITE_BYTES = 5 * 1024 * 1024;

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    let aborted = false;
    req.on("data", (chunk) => {
      if (aborted) return;
      total += chunk.length;
      if (total > MAX_WRITE_BYTES) {
        aborted = true;
        reject(new Error("payload too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      if (!aborted) resolve(Buffer.concat(chunks).toString("utf8"));
    });
    req.on("error", (err) => {
      if (!aborted) reject(err);
    });
  });
}

function writeFileSafely(fullPath, content) {
  if (!fs.existsSync(fullPath)) {
    return { ok: false, status: 404, error: "file not found" };
  }
  if (fullPath.endsWith(".json")) {
    try {
      JSON.parse(content);
    } catch (err) {
      return { ok: false, status: 400, error: `invalid JSON: ${err.message}` };
    }
  }
  const tmp = `${fullPath}.tmp.${process.pid}.${Date.now()}`;
  try {
    fs.writeFileSync(tmp, content, "utf8");
    fs.renameSync(tmp, fullPath);
  } catch (err) {
    try { fs.unlinkSync(tmp); } catch { /* ignore */ }
    return { ok: false, status: 500, error: `write failed: ${err.message}` };
  }
  const st = fs.statSync(fullPath);
  return {
    ok: true,
    size: st.size,
    mtime: st.mtime ? st.mtime.toISOString() : null,
  };
}

const CONTEXT_SCRIPT = path.join(__dirname, "dev_memory_context.py");

function runInjectionPreview(repoKey, branch) {
  return new Promise((resolve, reject) => {
    const args = [
      CONTEXT_SCRIPT,
      "injection-preview",
      "--repo-key", repoKey,
      "--branch", branch,
      "--context-dir", getStorageRoot(),
    ];
    const child = spawn(process.env.DEV_MEMORY_PYTHON || "python3", args, {
      cwd: __dirname,
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 15000,
    });
    const chunks = [];
    child.stdout.on("data", (d) => chunks.push(d));
    let stderr = "";
    child.stderr.on("data", (d) => { stderr += d.toString(); });
    child.on("close", (code) => {
      const stdout = Buffer.concat(chunks).toString("utf8");
      if (code !== 0) {
        reject(new Error(stderr || `exit ${code}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (e) {
        reject(new Error(`invalid JSON: ${e.message}`));
      }
    });
    child.on("error", reject);
  });
}

function readAppHtml() {
  return fs.readFileSync(APP_HTML_PATH, "utf8");
}

function openBrowser(url) {
  const platform = process.platform;
  let cmd;
  let args;
  if (platform === "darwin") {
    cmd = "open";
    args = [url];
  } else if (platform === "win32") {
    cmd = "cmd";
    args = ["/c", "start", "", url];
  } else {
    cmd = "xdg-open";
    args = [url];
  }
  try {
    const child = spawn(cmd, args, { detached: true, stdio: "ignore" });
    child.unref();
  } catch {
    // ignore — user can open the URL manually from stdout
  }
}

function start({ host = "127.0.0.1", port = 0, openBrowserFlag = true, readOnly = false } = {}) {
  const server = http.createServer((req, res) => {
    let url;
    try {
      url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
    } catch {
      res.writeHead(400);
      res.end("bad request");
      return;
    }
    const method = req.method;
    const isRead = method === "GET" || method === "HEAD";
    const isFileWrite = method === "PUT" && url.pathname === "/api/file";
    if (!isRead && !isFileWrite) {
      res.writeHead(405);
      res.end("method not allowed");
      return;
    }
    if (url.pathname === "/") {
      try {
        const html = readAppHtml();
        res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        res.end(html);
      } catch (err) {
        res.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
        res.end(`ui-app.html missing: ${err.message}`);
      }
      return;
    }
    if (url.pathname === "/api/tree") {
      const tree = buildTree();
      tree.readOnly = !!readOnly;
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify(tree));
      return;
    }
    if (url.pathname === "/api/session-scan" && isRead) {
      const limit = url.searchParams.get("limit") || 100;
      const offset = url.searchParams.get("offset") || 0;
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify(buildSessionScanData({ limit, offset })));
      return;
    }
    if (url.pathname === "/api/session-scan/run" && isRead) {
      const detail = buildSessionScanRunDetail(url.searchParams.get("id") || "");
      if (!detail) {
        res.writeHead(404, { "content-type": "application/json; charset=utf-8" });
        res.end(JSON.stringify({ error: "run not found" }));
        return;
      }
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify(detail));
      return;
    }
    if (url.pathname === "/api/injection-preview" && isRead) {
      const repoKey = url.searchParams.get("repo") || "";
      const branch = url.searchParams.get("branch") || "";
      if (!repoKey || !branch) {
        res.writeHead(400, { "content-type": "application/json; charset=utf-8" });
        res.end(JSON.stringify({ error: "repo and branch required" }));
        return;
      }
      runInjectionPreview(repoKey, branch).then((data) => {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        res.end(JSON.stringify(data));
      }).catch((err) => {
        res.writeHead(500, { "content-type": "application/json; charset=utf-8" });
        res.end(JSON.stringify({ error: err.message }));
      });
      return;
    }
    if (url.pathname === "/api/file" && isRead) {
      const rel = url.searchParams.get("path") || "";
      const full = resolveSafePath(rel);
      if (!full) {
        res.writeHead(400, { "content-type": "text/plain" });
        res.end("invalid path");
        return;
      }
      try {
        const body = fs.readFileSync(full, "utf8");
        res.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
        res.end(body);
      } catch {
        res.writeHead(404);
        res.end("not found");
      }
      return;
    }
    if (isFileWrite) {
      if (readOnly) {
        res.writeHead(403, { "content-type": "text/plain" });
        res.end("read-only mode");
        return;
      }
      const rel = url.searchParams.get("path") || "";
      const full = resolveSafePath(rel);
      if (!full) {
        res.writeHead(400, { "content-type": "text/plain" });
        res.end("invalid path");
        return;
      }
      readRequestBody(req).then((content) => {
        const result = writeFileSafely(full, content);
        if (!result.ok) {
          res.writeHead(result.status || 500, { "content-type": "text/plain; charset=utf-8" });
          res.end(result.error || "write failed");
          return;
        }
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        res.end(JSON.stringify({ ok: true, size: result.size, mtime: result.mtime }));
      }).catch((err) => {
        const status = err && err.message === "payload too large" ? 413 : 400;
        res.writeHead(status, { "content-type": "text/plain; charset=utf-8" });
        res.end(err && err.message ? err.message : "bad request");
      });
      return;
    }
    res.writeHead(404);
    res.end("not found");
  });

  server.listen(port, host, () => {
    const addr = server.address();
    const actualPort = typeof addr === "object" && addr ? addr.port : port;
    const url = `http://${host}:${actualPort}`;
    process.stdout.write(`dev-memory-cli ui: ${url}${readOnly ? " (read-only)" : ""}\n`);
    process.stdout.write(`storage root:  ${getStorageRoot()}\n`);
    process.stdout.write(`press Ctrl+C to stop.\n`);
    if (openBrowserFlag) openBrowser(url);
  });

  return server;
}

module.exports = {
  start,
  buildTree,
  buildSessionScanData,
  buildSessionScanRunDetail,
  getStorageRoot,
  getScanRoot,
};
