import { getGateStatus } from "@/lib/gate-adapter";

export async function GET() {
  const status = await getGateStatus();
  return Response.json(status, {
    status: status.mode === "unavailable" ? 503 : 200,
    headers: { "cache-control": "no-store" },
  });
}
