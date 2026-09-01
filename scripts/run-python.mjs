import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const projectRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const requested = process.env.FACTORY_MONITOR_PYTHON?.trim();
const candidates = [];

if (requested) {
  candidates.push({ command: requested, prefix: [] });
}

const venvPython = path.join(
  projectRoot,
  ".venv",
  process.platform === "win32" ? "Scripts" : "bin",
  process.platform === "win32" ? "python.exe" : "python",
);
if (existsSync(venvPython)) {
  candidates.push({ command: venvPython, prefix: [] });
}

if (process.platform === "win32") {
  candidates.push(
    { command: "py", prefix: ["-3"] },
    { command: "python", prefix: [] },
    { command: "python3", prefix: [] },
  );
} else {
  candidates.push(
    { command: "python3", prefix: [] },
    { command: "python", prefix: [] },
  );
}

let selected = null;
for (const candidate of candidates) {
  const probe = spawnSync(candidate.command, [...candidate.prefix, "--version"], {
    cwd: projectRoot,
    stdio: "ignore",
    windowsHide: true,
  });
  if (!probe.error && probe.status === 0) {
    selected = candidate;
    break;
  }
}

if (!selected) {
  console.error("Python 3 was not found. Run INSTALL-WINDOWS.cmd first on Windows.");
  process.exit(127);
}

const result = spawnSync(
  selected.command,
  [...selected.prefix, ...process.argv.slice(2)],
  {
    cwd: projectRoot,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8",
    },
    stdio: "inherit",
    windowsHide: true,
  },
);
if (result.error) {
  throw result.error;
}
process.exit(result.status ?? 1);
