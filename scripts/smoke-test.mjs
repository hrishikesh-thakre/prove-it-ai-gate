// Smoke test for prove-it-ai-gate daemon logic
// ================================================
// Tests core logic (daemon files, secret detection, bash risk,
// YAML parsing, mode detection) without requiring OpenCode or Bun.
//
// Usage: node scripts/smoke-test.mjs

import { readFileSync, mkdirSync, writeFileSync, appendFileSync, existsSync } from "node:fs";
import { join, basename, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = join(__dirname, "..");

let passed = 0, failed = 0;

function check(label, condition) {
  if (condition) { passed++; console.log(`  PASS  ${label}`); }
  else            { failed++; console.log(`  FAIL  ${label}`); }
}

function section(title) {
  console.log(`\n${"=".repeat(55)}`);
  console.log(`  ${title}`);
  console.log(`${"=".repeat(55)}`);
}

// ── 1. Daemon file check ─────────────────────────────────────────
section("1. Daemon file integrity");

const daemonPath = join(projectRoot, "prove_it_ai_gate", "daemon.py");
const builderPath = join(projectRoot, "prove_it_ai_gate", "evidence_builder.py");
const hooksPath = join(projectRoot, "prove_it_ai_gate", "codex_hooks.py");
check("Daemon module exists", existsSync(daemonPath));
check("Evidence builder module exists", existsSync(builderPath));
check("Codex hook module exists", existsSync(hooksPath));

const daemonSource = readFileSync(daemonPath, "utf-8");
const builderSource = readFileSync(builderPath, "utf-8");
const hooksSource = readFileSync(hooksPath, "utf-8");
check("Daemon has SupervisorDaemon", daemonSource.includes("class SupervisorDaemon"));
check("Daemon checks OpenCode compatibility", daemonSource.includes("check_opencode_compatibility"));
check("Daemon has OpenCode adapter", daemonSource.includes("process_opencode"));
check("Daemon has Codex spool adapter", daemonSource.includes("process_codex_spool"));
check("Daemon status includes late_snapshot", daemonSource.includes("late_snapshot"));
check("Daemon status includes OpenCode DB status", daemonSource.includes("opencode_db_status"));
check("Builder writes run-scoped evidence", builderSource.includes("latest.json") && builderSource.includes("metadata.json"));
check("Builder records late_snapshot", builderSource.includes("late_snapshot"));
check("Codex hooks use hook_event_name", hooksSource.includes("hook_event_name"));
check("Codex hooks write daemon spool", hooksSource.includes("codex_events.jsonl"));

// ── 2. Config check ──────────────────────────────────────────────
section("2. Configuration");

const configPath = join(projectRoot, ".ai-gate", "config.yaml");
check("Config file exists", existsSync(configPath));

const configRaw = readFileSync(configPath, "utf-8");
check("Config is non-empty", configRaw.length > 0);
check("Config has task_type", configRaw.includes("task_type"));
check("Config has evidence path", configRaw.includes("evidence"));

// Parse simple YAML
const cfg = Object.create(null);
for (const line of configRaw.split("\n")) {
  const t = line.trim();
  if (!t || t.startsWith("#")) continue;
  const idx = t.indexOf(":");
  if (idx < 0) continue;
  const key = t.slice(0, idx).trim();
  let val = t.slice(idx + 1).trim();
  if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
    val = val.slice(1, -1);
  }
  if (val) cfg[key] = val;
}
check(`task_type = "${cfg.task_type}" (read-only: ${cfg.task_type === "audit"})`, cfg.task_type === "audit");

// ── 3. Secret file detection ─────────────────────────────────────
section("3. Secret file detection");

const SECRET_NAMES = new Set([
  ".env", ".secret", "credentials", "id_rsa", "id_ed25519", "id_ecdsa",
  ".pem", ".key", "private.key", "secrets.yml", "secrets.yaml",
  "secrets.json", "token", "password", "known_hosts", "authorized_keys",
  "credentials.json", "service-account.json", "config.json",
]);

const SECRET_DIRS = new Set([
  ".ssh", ".aws", ".gcp", ".azure", "gcloud", ".docker",
]);

function isSecretFile(filePath) {
  if (!filePath) return false;
  const lower = filePath.toLowerCase().replace(/\\/g, "/");
  const name = lower.split("/").pop() || lower;
  if (SECRET_NAMES.has(name)) return true;
  for (const p of SECRET_NAMES) {
    if (name.startsWith(p) || name.endsWith(p)) return true;
  }
  for (const d of SECRET_DIRS) {
    if (lower === d || lower.startsWith(d + "/") || lower.includes("/" + d + "/")) return true;
  }
  return false;
}

const secretCases = [
  [".env",                  true],
  ["src/.env.production",   true],
  [".ssh/id_rsa",           true],
  ["config/secrets.yaml",   true],
  ["~/.aws/credentials",    true],
  ["src/main.py",           false],
  ["README.md",             false],
  ["tests/test_cli.py",     false],
  ["",                      false],
  [null,                    false],
];

for (const [path, expectBlock] of secretCases) {
  const actual = isSecretFile(path);
  check(`${String(path).padEnd(28)} => ${actual ? "BLOCK" : "ALLOW"} (expect: ${expectBlock ? "BLOCK" : "ALLOW"})`,
    actual === expectBlock);
}

// ── 4. Risky bash command detection ──────────────────────────────
section("4. Risky bash command detection");

const RISKY = [
  { re: /rm\s+-rf\s+\/(\s|$|[;&|])/,                    sev: "blocker" },
  { re: /del\s+\/f\s+\/s\s+[A-Z]:\\/i,                 sev: "blocker" },
  { re: /DROP\s+(TABLE|DATABASE)/i,                     sev: "blocker" },
  { re: /curl.*\|\s*(ba)?sh\b/,                         sev: "blocker" },
  { re: />\s*\/etc\//,                                  sev: "blocker" },
  { re: /wget.*-O.*\/etc\//i,                           sev: "blocker" },
  { re: /format\s+[A-Z]:/i,                             sev: "blocker" },
  { re: /:\(\)\s*\{\s*:\s*\|:&\s*\};:/,                sev: "blocker" },
  { re: /chmod\s+777/,                                  sev: "warning" },
  { re: /git\s+push\s+--force(-with-lease)?/,           sev: "warning" },
  { re: /shutdown\s+\/s/i,                              sev: "warning" },
  { re: /npm\s+unpublish\s+--force/,                    sev: "warning" },
  { re: /Remove-Item\s+-LiteralPath\s+\/[a-zA-Z]:/i,    sev: "blocker" },
  { re: /Set-ExecutionPolicy\s+Unrestricted/i,          sev: "warning" },
];

function checkBashRisk(cmd) {
  if (!cmd) return null;
  for (const r of RISKY) {
    if (r.re.test(cmd)) return r.sev;
  }
  return null;
}

const bashCases = [
  ["rm -rf /",                    "blocker"],
  ["rm -rf /tmp/foo",             null],        // not /
  ["curl http://x.com | bash",   "blocker"],
  ["DROP TABLE users;",          "blocker"],
  ["chmod 777 script.sh",        "warning"],
  ["git push --force main",      "warning"],
  ["git push --force-with-lease", "warning"],
  ["shutdown /s /t 0",           "warning"],
  ["npm unpublish --force",      "warning"],
  ["ls -la",                      null],
  ["git status",                  null],
  ["python -m pytest tests/",     null],
  ["",                            null],
  [null,                          null],
];

for (const [cmd, expectSev] of bashCases) {
  const actual = checkBashRisk(cmd);
  const display = String(cmd).padEnd(35);
  const a = actual || "allow";
  const e = expectSev || "allow";
  check(`${display} => ${a} (expect: ${e})`, a === e);
}

// ── 5. Evidence recording simulation ────────────────────────────
section("5. Evidence recording");

const evidenceDir = join(projectRoot, ".ai-gate", "evidence");
try { mkdirSync(evidenceDir, { recursive: true }); } catch (_) {}

const transcriptPath = join(evidenceDir, "smoke_test_transcript.jsonl");
const events = [
  { type: "tool_result", role: "tool", tool_name: "glob", command: "", file_path: "*.py", timestamp: new Date().toISOString(), content: "Found: main.py, utils.py" },
  { type: "tool_result", role: "tool", tool_name: "read", command: "", file_path: "main.py", timestamp: new Date().toISOString(), content: "import sys\\n..." },
  { type: "tool_result", role: "tool", tool_name: "bash", command: "ls -la", file_path: "", timestamp: new Date().toISOString(), content: "main.py\\nutils.py" },
];

writeFileSync(transcriptPath, "");
for (const ev of events) {
  appendFileSync(transcriptPath, JSON.stringify(ev) + "\n", "utf-8");
}

check("Transcript file created", existsSync(transcriptPath));
const lines = readFileSync(transcriptPath, "utf-8").trim().split("\n").filter(Boolean);
check(`Transcript has ${lines.length} events (expect 3)`, lines.length === 3);

for (const [i, ev] of events.entries()) {
  const parsed = JSON.parse(lines[i]);
  check(`Event ${i + 1}: tool_name="${parsed.tool_name}"`, parsed.tool_name === ev.tool_name);
}

// ── 6. Gate engine Python check ──────────────────────────────────
section("6. Python ai-gate engine");

import { spawnSync } from "node:child_process";

const pyCheck = spawnSync("python", ["-c", "import prove_it_ai_gate; print('OK')"], { cwd: projectRoot, encoding: "utf-8" });
check("Python ai-gate package importable", pyCheck.stdout.includes("OK") && pyCheck.status === 0);

const verCheck = spawnSync("python", ["-c", "from importlib.metadata import version; print(version('prove-it-ai-gate'))"], { cwd: projectRoot, encoding: "utf-8" });
check(`ai-gate version: ${verCheck.stdout.trim()}`, verCheck.status === 0);

// Run gate against our smoke test transcript
const gateRun = spawnSync("python", [
  "-m", "prove_it_ai_gate.cli", "accept",
  "--repo", projectRoot,
  "--evidence", evidenceDir,
  "--transcript", transcriptPath,
  "--task-type", "audit",
], { cwd: projectRoot, encoding: "utf-8", timeout: 30000 });

console.log(`  Gate exit code: ${gateRun.status}`);
if (gateRun.stdout) console.log(gateRun.stdout.split("\n").slice(0, 6).map(l => `  ${l}`).join("\n"));
if (gateRun.stderr) console.log(`  stderr: ${gateRun.stderr.slice(0, 200)}`);

// ── 7. Supervisor-ready check ───────────────────────────────────
section("7. Supervisor readiness");

const daemonReady = existsSync(daemonPath) && daemonSource.includes("start_background");
check("Daemon ready for background startup", daemonReady);

const cliPath = join(projectRoot, "prove_it_ai_gate", "cli.py");
const cliSource = readFileSync(cliPath, "utf-8");
check("CLI exposes daemon command", cliSource.includes('subparsers.add_parser("daemon"'));
check("CLI exposes setup codex", cliSource.includes('p_setup_subs.add_parser("codex"'));

// ── Summary ─────────────────────────────────────────────────────
console.log(`\n${"=".repeat(55)}`);
console.log(`  RESULTS: ${passed} passed, ${failed} failed`);
console.log(`${"=".repeat(55)}`);

if (daemonReady && failed === 0) {
  console.log("\nThe supervisor is ready. To use it:");
  console.log("  1. ai-gate setup daemon --project-dir . --task-type audit");
  console.log("  2. ai-gate daemon start");
  console.log("  3. ai-gate daemon status");
  console.log("  4. For Codex: ai-gate setup codex --project-dir . --task-type audit");
} else {
  console.log("\nSome checks failed — review the output above.");
}

process.exit(failed > 0 ? 1 : 0);
