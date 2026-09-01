export const EXPECTED_GATE_VERSION = "0.21.0";
export const EXPECTED_GATE_RELEASE = "release-5affc732731c3dcc";

export type GateMode = "catalog-only" | "connected" | "unavailable";

export type GateStatus = {
  mode: GateMode;
  label: string;
  checkedAt: string;
  expectedVersion: string;
  expectedRelease: string;
  capabilities: {
    cameraCatalog: boolean;
    coverageQuery: boolean;
    representativeFrames: boolean;
    objectCandidates: boolean;
  };
  detail: string;
};

export type CoverageRequest = {
  cameraId: string;
  start: string;
  end: string;
};

export type CoverageResult = {
  cameraId: string;
  start: string;
  end: string;
  status: "available" | "partial" | "missing" | "unknown";
  clockStatus: "validated" | "unverified" | "unknown";
  decodeStatus: "complete" | "incomplete" | "unknown";
  evidenceIds: string[];
  gaps: Array<{ start: string; end: string; reason: string }>;
  note: string;
};

function connectorConfig() {
  const baseUrl = process.env.GATE_PERSON_AUDIT_BASE_URL?.trim().replace(/\/$/, "") ?? "";
  const token = process.env.GATE_PERSON_AUDIT_TOKEN?.trim() ?? "";
  return { baseUrl, token };
}

function safeStatus(mode: GateMode, detail: string): GateStatus {
  const connected = mode === "connected";
  return {
    mode,
    label: connected ? "只读录像接口已连接" : mode === "catalog-only" ? "脱敏目录模式" : "录像接口不可用",
    checkedAt: new Date().toISOString(),
    expectedVersion: EXPECTED_GATE_VERSION,
    expectedRelease: EXPECTED_GATE_RELEASE,
    capabilities: {
      cameraCatalog: true,
      coverageQuery: connected,
      representativeFrames: false,
      objectCandidates: false,
    },
    detail,
  };
}

export async function getGateStatus(): Promise<GateStatus> {
  const { baseUrl, token } = connectorConfig();
  if (!baseUrl) {
    return safeStatus("catalog-only", "原版源码不是必需项；配置符合契约的只读录像服务后即可启用生产查询。");
  }

  try {
    const response = await fetch(`${baseUrl}/health`, {
      headers: token ? { authorization: `Bearer ${token}` } : undefined,
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) return safeStatus("unavailable", `只读录像服务健康检查失败（HTTP ${response.status}）。`);
    return safeStatus("connected", "连接仅暴露逻辑摄像头、覆盖状态和受控证据编号，不向浏览器返回账号、地址或录像路径。");
  } catch {
    return safeStatus("unavailable", "只读录像服务暂时无法访问；系统按证据不足处理，不返回错误的正常结论。");
  }
}

export async function queryCoverage(input: CoverageRequest): Promise<CoverageResult> {
  const { baseUrl, token } = connectorConfig();
  const unknown: CoverageResult = {
    ...input,
    status: "unknown",
    clockStatus: "unknown",
    decodeStatus: "unknown",
    evidenceIds: [],
    gaps: [],
    note: "只读录像服务未连接或返回无效结果；未知不等于没有录像。",
  };
  if (!baseUrl) return unknown;

  try {
    const response = await fetch(`${baseUrl}/v1/coverage`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(input),
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) return unknown;
    const payload = (await response.json()) as Partial<CoverageResult>;
    if (payload.cameraId !== input.cameraId || !["available", "partial", "missing", "unknown"].includes(payload.status ?? "")) {
      return unknown;
    }
    return {
      ...unknown,
      status: payload.status ?? "unknown",
      clockStatus: ["validated", "unverified", "unknown"].includes(payload.clockStatus ?? "") ? payload.clockStatus! : "unknown",
      decodeStatus: ["complete", "incomplete", "unknown"].includes(payload.decodeStatus ?? "") ? payload.decodeStatus! : "unknown",
      evidenceIds: Array.isArray(payload.evidenceIds) ? payload.evidenceIds.filter((value): value is string => typeof value === "string").slice(0, 100) : [],
      gaps: Array.isArray(payload.gaps)
        ? payload.gaps
            .filter((gap) => gap && typeof gap.start === "string" && typeof gap.end === "string" && typeof gap.reason === "string")
            .slice(0, 100)
        : [],
      note: typeof payload.note === "string" ? payload.note.slice(0, 500) : "",
    };
  } catch {
    return unknown;
  }
}
