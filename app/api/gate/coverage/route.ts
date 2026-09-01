import cameras from "@/public/data/cameras.json";
import { queryCoverage } from "@/lib/gate-adapter";

const cameraIds = new Set(cameras.map((camera) => camera.id));
const MAX_WINDOW_MS = 24 * 60 * 60 * 1000;

export async function POST(request: Request) {
  let payload: { cameraId?: unknown; start?: unknown; end?: unknown };
  try {
    payload = (await request.json()) as typeof payload;
  } catch {
    return Response.json({ error: "请求内容必须是 JSON。" }, { status: 400 });
  }

  if (typeof payload.cameraId !== "string" || !cameraIds.has(payload.cameraId)) {
    return Response.json({ error: "cameraId 不在获批的脱敏摄像头目录中。" }, { status: 400 });
  }
  if (typeof payload.start !== "string" || typeof payload.end !== "string") {
    return Response.json({ error: "start 和 end 为必填 ISO 时间。" }, { status: 400 });
  }

  const start = new Date(payload.start);
  const end = new Date(payload.end);
  const duration = end.getTime() - start.getTime();
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || duration <= 0 || duration > MAX_WINDOW_MS) {
    return Response.json({ error: "查询窗口必须大于0且不超过24小时。" }, { status: 400 });
  }

  const result = await queryCoverage({ cameraId: payload.cameraId, start: start.toISOString(), end: end.toISOString() });
  return Response.json(result, {
    status: result.status === "unknown" ? 503 : 200,
    headers: { "cache-control": "no-store" },
  });
}
