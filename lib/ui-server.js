"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn } = require("node:child_process");

const DEFAULT_STORAGE_ROOT = path.join(os.homedir(), ".dev-assets", "repos");
const APP_HTML_PATH = path.join(__dirname, "ui-app.html");

function getStorageRoot() {
  return process.env.DEV_ASSETS_ROOT || DEFAULT_STORAGE_ROOT;
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

function start({ host = "127.0.0.1", port = 0, openBrowserFlag = true } = {}) {
  const server = http.createServer((req, res) => {
    let url;
    try {
      url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
    } catch {
      res.writeHead(400);
      res.end("bad request");
      return;
    }
    if (req.method !== "GET" && req.method !== "HEAD") {
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
      res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
      res.end(JSON.stringify(buildTree()));
      return;
    }
    if (url.pathname === "/api/file") {
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
    res.writeHead(404);
    res.end("not found");
  });

  server.listen(port, host, () => {
    const addr = server.address();
    const actualPort = typeof addr === "object" && addr ? addr.port : port;
    const url = `http://${host}:${actualPort}`;
    process.stdout.write(`dev-assets ui: ${url}\n`);
    process.stdout.write(`storage root:  ${getStorageRoot()}\n`);
    process.stdout.write(`press Ctrl+C to stop.\n`);
    if (openBrowserFlag) openBrowser(url);
  });

  return server;
}

module.exports = { start, buildTree, getStorageRoot };
