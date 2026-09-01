import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const templateRoot = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the investigation workspace", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>寻证｜主管监控调查工作台<\/title>/i);
  assert.match(html, /先缩小范围/);
  assert.match(html, /建立事件单/);
  assert.match(html, /脱敏目录模式/);
  assert.match(html, /证据边界/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/);
});

test("ships the complete sanitized camera capability snapshot", async () => {
  const raw = await readFile(new URL("../public/data/cameras.json", import.meta.url), "utf8");
  const cameras = JSON.parse(raw);
  const inventory = JSON.parse(
    await readFile(new URL("../camera-data/current/data/摄像头脱敏库存.json", import.meta.url), "utf8"),
  );
  assert.equal(cameras.length, inventory.expected_channel_count);
  assert.equal(new Set(cameras.map((camera) => camera.id)).size, inventory.expected_channel_count);
  assert.ok(cameras.some((camera) => camera.evidenceLevel === "E2_direct_observation"));
  assert.ok(cameras.some((camera) => camera.evidenceLevel === "E1_source_only"));
  assert.doesNotMatch(raw, /rtsp:\/\/|https?:\/\/|password|username|webhook|cookie/i);

  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  assert.match(page, /camera-investigation-cases-v1/);
  assert.match(page, /exportCaseMarkdown/);
  assert.match(page, /查询录像覆盖/);
  assert.match(page, /\/api\/gate\/coverage/);
  assert.match(layout, /主管监控调查工作台/);
  assert.doesNotMatch(page + layout, /_sites-preview|codex-preview/);
  assert.ok(templateRoot);
});

test("exposes a fail-closed replacement connector without requiring the original source", async () => {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("gate-test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const env = { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } };
  const context = { waitUntil() {}, passThroughOnException() {} };

  const statusResponse = await worker.fetch(new Request("http://localhost/api/gate/status"), env, context);
  assert.equal(statusResponse.status, 200);
  const status = await statusResponse.json();
  assert.equal(status.mode, "catalog-only");
  assert.equal(status.capabilities.cameraCatalog, true);
  assert.equal(status.capabilities.coverageQuery, false);
  assert.doesNotMatch(JSON.stringify(status), /token|password|rtsp:\/\//i);

  const coverageResponse = await worker.fetch(
    new Request("http://localhost/api/gate/coverage", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        cameraId: "cam-nvr-caiduo-001",
        start: "2026-08-01T00:00:00+08:00",
        end: "2026-08-01T01:00:00+08:00",
      }),
    }),
    env,
    context,
  );
  assert.equal(coverageResponse.status, 503);
  const coverage = await coverageResponse.json();
  assert.equal(coverage.status, "unknown");
  assert.match(coverage.note, /未知不等于没有录像/);
});
