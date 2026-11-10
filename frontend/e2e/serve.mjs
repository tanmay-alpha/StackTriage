// Starts a real StackTriage backend for the e2e suite: node e2e/serve.mjs <name> <port> [apiKey]
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, "..");
const repo = path.resolve(frontend, "..");
const [name, port, apiKey] = process.argv.slice(2);
if (!name || !port) {
  console.error("usage: node e2e/serve.mjs <name> <port> [apiKey]");
  process.exit(2);
}

const dist = path.join(frontend, "dist");
if (!existsSync(path.join(dist, "index.html"))) {
  console.error(`frontend/dist/index.html is missing. Run "npm run build" in frontend/ before the e2e suite.`);
  process.exit(2);
}

const dataDir = path.join(frontend, ".e2e-data");
mkdirSync(dataDir, { recursive: true });
const db = path.join(dataDir, `${name}.db`);
for (const suffix of ["", "-wal", "-shm", "-journal"]) rmSync(db + suffix, { force: true });

function findPython() {
  if (process.env.STACKTRIAGE_PYTHON) return process.env.STACKTRIAGE_PYTHON;
  for (const p of [path.join(repo, ".venv", "Scripts", "python.exe"), path.join(repo, ".venv", "bin", "python")]) {
    if (existsSync(p)) return p;
  }
  return "python";
}

const env = { ...process.env, STACKTRIAGE_DB: db, STACKTRIAGE_STATIC_DIR: dist };
delete env.STACKTRIAGE_API_KEY;
if (apiKey) env.STACKTRIAGE_API_KEY = apiKey;

const child = spawn(findPython(), ["-m", "stacktriage", "serve", "--port", port], { cwd: repo, env, stdio: "inherit" });
child.on("exit", (code) => process.exit(code ?? 0));
child.on("error", (e) => {
  console.error("could not start the backend:", e.message);
  process.exit(1);
});
for (const sig of ["SIGINT", "SIGTERM", "SIGBREAK"]) process.on(sig, () => child.kill());
process.on("exit", () => child.kill());
