import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const mode = process.argv[2];
if (!new Set(["dev", "build", "start"]).has(mode)) {
  console.error("Usage: node scripts/run-vinext.mjs <dev|build|start>");
  process.exit(2);
}

const projectRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const executable = process.execPath;
const vinextCli = path.join(
  projectRoot,
  "node_modules",
  "vinext",
  "dist",
  "cli.js",
);
const env = {
  ...process.env,
  WRANGLER_LOG_PATH: ".wrangler/wrangler.log",
};
if (mode !== "build") {
  env.GATE_PERSON_AUDIT_BASE_URL =
    process.env.GATE_PERSON_AUDIT_BASE_URL || "http://127.0.0.1:8766";
}

const result = spawnSync(executable, [vinextCli, mode], {
  cwd: projectRoot,
  env,
  stdio: "inherit",
  windowsHide: true,
});
if (result.error) {
  throw result.error;
}
process.exit(result.status ?? 1);
