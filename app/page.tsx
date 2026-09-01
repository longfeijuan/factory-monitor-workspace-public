"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type CameraRoute = {
  id: string;
  title: string;
  sequence: number;
  stage: string;
};

type Camera = {
  id: string;
  name: string;
  sourceName: string;
  recorder: string;
  channel: string;
  onlineClaim: boolean | null;
  evidenceLevel: "E1_source_only" | "E2_direct_observation";
  evidenceScope: "current_frame" | "historical_only" | "source_name_only";
  frameClockStatus: string;
  timeMappingStatus: string;
  periods: string[];
  zones: string[];
  capabilities: string[];
  routes: CameraRoute[];
  unknowns: string[];
};

type ReviewOutcome = "pending" | "relevant" | "irrelevant" | "unclear";

type Review = {
  outcome: ReviewOutcome;
  note: string;
  updatedAt?: string;
};

type CoverageCheck = {
  status: "available" | "partial" | "missing" | "unknown";
  clockStatus: "validated" | "unverified" | "unknown";
  decodeStatus: "complete" | "incomplete" | "unknown";
  evidenceIds: string[];
  gaps: Array<{ start: string; end: string; reason: string }>;
  note: string;
  checkedAt: string;
};

type PlanCamera = {
  cameraId: string;
  name: string;
  stage: string;
  sequence: number;
  reason: string;
  evidenceLevel: Camera["evidenceLevel"];
  evidenceScope: Camera["evidenceScope"];
  onlineClaim: boolean | null;
  review: Review;
  coverage?: CoverageCheck;
};

type Investigation = {
  id: string;
  item: string;
  description: string;
  location: string;
  lastSeen: string;
  discovered: string;
  owner: string;
  routeId: string;
  routeTitle: string;
  windowStart: string;
  windowEnd: string;
  status: "待回看" | "复核中" | "已关闭";
  createdAt: string;
  plan: PlanCamera[];
};

type CaseForm = {
  item: string;
  description: string;
  location: string;
  lastSeen: string;
  discovered: string;
  owner: string;
  routeId: string;
};

type GateStatus = {
  mode: "catalog-only" | "connected" | "unavailable";
  label: string;
  detail: string;
};

const STORAGE_KEY = "camera-investigation-cases-v1";

const ROUTES = [
  {
    id: "auto",
    title: "自动推荐最小路线",
    summary: "根据物品、地点和时间从现有业务路线中选择。",
  },
  {
    id: "warehouse-quality-delivery-flow",
    title: "仓储—品检—打包—快递交付",
    summary: "收货、称数、入库、品检、打包、扫单与快递暂存。",
  },
  {
    id: "surface-outsourcing-handoff-flow",
    title: "表面处理—外发架—交接通道",
    summary: "表面处理、外发架位、送货暂存与交接通道。",
  },
  {
    id: "machining-postprocess-flow",
    title: "机加工工序状态",
    summary: "刷单、车床、电脑锣、铣床、钻攻、激光与后加工。",
  },
  {
    id: "caiduo-cutting-shipping-flow",
    title: "材多切料—通道—出货",
    summary: "原料、切料、机加工、刷单、通道与出货门。",
  },
  {
    id: "order-material-flow-v1",
    title: "订单物料全链路候选",
    summary: "厂区入口、收货、加工、交接、品检、打包与出货。",
  },
] as const;

const EVIDENCE_LABEL: Record<Camera["evidenceScope"], string> = {
  current_frame: "当前画面证据",
  historical_only: "历史时段证据",
  source_name_only: "仅名称线索",
};

const REVIEW_LABEL: Record<ReviewOutcome, string> = {
  pending: "待复核",
  relevant: "有相关线索",
  irrelevant: "与本事件无关",
  unclear: "无法判断",
};

const COVERAGE_LABEL: Record<CoverageCheck["status"], string> = {
  available: "录像覆盖完整",
  partial: "录像存在缺口",
  missing: "未返回录像段",
  unknown: "覆盖状态未知",
};

const KEYWORDS = [
  "仓",
  "收货",
  "入库",
  "品检",
  "打包",
  "快递",
  "出货",
  "外发",
  "表面处理",
  "氧化",
  "机加工",
  "车床",
  "电脑锣",
  "铣床",
  "钻攻",
  "激光",
  "后加工",
  "材多",
  "切料",
  "铝板",
  "磨床",
  "通道",
  "门",
];

const emptyForm: CaseForm = {
  item: "",
  description: "",
  location: "",
  lastSeen: "",
  discovered: "",
  owner: "",
  routeId: "auto",
};

function dateTimeLocal(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function addMinutes(value: string, minutes: number) {
  const date = new Date(value);
  date.setMinutes(date.getMinutes() + minutes);
  return dateTimeLocal(date);
}

function displayTime(value: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function recommendRoute(text: string) {
  if (/材多|切料|铝板|铝排|磨床/.test(text)) return "caiduo-cutting-shipping-flow";
  if (/车床|电脑锣|铣床|钻攻|激光|机加工|后加工|去披锋/.test(text)) return "machining-postprocess-flow";
  if (/表面处理|氧化|外发|喷砂|电镀/.test(text)) return "surface-outsourcing-handoff-flow";
  if (/仓|品检|质检|打包|快递|扫单|入库|收货|出货/.test(text)) return "warehouse-quality-delivery-flow";
  return "order-material-flow-v1";
}

function routeName(routeId: string, cameras: Camera[]) {
  const fromData = cameras.flatMap((camera) => camera.routes).find((route) => route.id === routeId)?.title;
  return fromData?.replace(/检索路由候选$/, "") ?? ROUTES.find((route) => route.id === routeId)?.title ?? "业务检索路线";
}

function evidenceReason(camera: Camera) {
  if (camera.evidenceScope === "source_name_only") return "仅按设备名称召回，需优先确认位置与录像覆盖";
  if (camera.evidenceScope === "historical_only") return "已有历史时段视觉证据，可用于缩小回看范围";
  return "已有直接视觉证据，可先检查目标时段录像";
}

function exportCaseMarkdown(investigation: Investigation) {
  const reviewed = investigation.plan.filter((item) => item.review.outcome !== "pending");
  const lines = [
    `# 丢失物品调查事件单 ${investigation.id}`,
    "",
    `- 状态：${investigation.status}`,
    `- 物品：${investigation.item}`,
    `- 外观/数量：${investigation.description || "未填写"}`,
    `- 最后确认地点：${investigation.location}`,
    `- 最后确认时间：${investigation.lastSeen.replace("T", " ")}`,
    `- 首次发现时间：${investigation.discovered.replace("T", " ")}`,
    `- 最小回看窗口：${investigation.windowStart.replace("T", " ")} 至 ${investigation.windowEnd.replace("T", " ")}`,
    `- 调查负责人：${investigation.owner || "待指定"}`,
    `- 候选路线：${investigation.routeTitle}`,
    "",
    "## 候选摄像头与人工复核",
    "",
    "| 顺序 | 阶段 | 摄像头 | 证据范围 | 录像覆盖 | 人工结论 | 备注 |",
    "|---:|---|---|---|---|---|---|",
    ...investigation.plan.map(
      (item) =>
        `| ${item.sequence} | ${item.stage} | ${item.cameraId} ${item.name} | ${EVIDENCE_LABEL[item.evidenceScope]} | ${item.coverage ? COVERAGE_LABEL[item.coverage.status] : "未查询"} | ${REVIEW_LABEL[item.review.outcome]} | ${item.review.note || ""} |`,
    ),
    "",
    "## 证据边界",
    "",
    "本事件单只记录候选路线与人工复核结论。未检测到、没有画面或设备名称均不能证明事件没有发生；路线顺序也不证明物理相邻、必经关系、同一物体或因果关系。",
    "",
    `人工已复核 ${reviewed.length}/${investigation.plan.length} 路。`,
  ];

  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${investigation.id}_事件单.md`;
  link.click();
  URL.revokeObjectURL(url);
}

export default function Home() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [cases, setCases] = useState<Investigation[]>([]);
  const [form, setForm] = useState<CaseForm>(emptyForm);
  const [activeView, setActiveView] = useState<"new" | "cases" | "map">("new");
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [cameraQuery, setCameraQuery] = useState("");
  const [cameraRouteFilter, setCameraRouteFilter] = useState("all");
  const [formError, setFormError] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const [gateStatus, setGateStatus] = useState<GateStatus>({
    mode: "catalog-only",
    label: "脱敏目录模式",
    detail: "原版源码不是必需项；正在使用独立只读录像接口契约。",
  });
  const [coverageLoading, setCoverageLoading] = useState<Record<string, boolean>>({});

  useEffect(() => {
    fetch("/data/cameras.json")
      .then(async (response) => (await response.json()) as Camera[])
      .then((data) => setCameras(data))
      .catch(() => setCameras([]));

    fetch("/api/gate/status", { cache: "no-store" })
      .then(async (response) => (await response.json()) as GateStatus)
      .then((status) => setGateStatus(status))
      .catch(() => setGateStatus({ mode: "unavailable", label: "录像接口不可用", detail: "状态检查失败，按证据不足处理。" }));

    const stored = window.localStorage.getItem(STORAGE_KEY);
    let restoredCases: Investigation[] = [];
    if (stored) {
      try {
        restoredCases = JSON.parse(stored) as Investigation[];
      } catch {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    }
    const now = new Date();
    const twoHoursAgo = new Date(now.getTime() - 2 * 60 * 60 * 1000);
    queueMicrotask(() => {
      setCases(restoredCases);
      setForm((current) => ({
        ...current,
        lastSeen: dateTimeLocal(twoHoursAgo),
        discovered: dateTimeLocal(now),
      }));
      setHydrated(true);
    });
  }, []);

  useEffect(() => {
    if (hydrated) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cases));
  }, [cases, hydrated]);

  const metrics = useMemo(() => {
    const direct = cameras.filter((camera) => camera.evidenceLevel === "E2_direct_observation").length;
    const sourceOnly = cameras.filter((camera) => camera.evidenceLevel === "E1_source_only").length;
    const online = cameras.filter((camera) => camera.onlineClaim === true).length;
    return { direct, sourceOnly, online };
  }, [cameras]);

  const selectedCase = cases.find((item) => item.id === selectedCaseId) ?? null;

  const filteredCameras = useMemo(() => {
    const query = cameraQuery.trim().toLowerCase();
    return cameras
      .filter((camera) => cameraRouteFilter === "all" || camera.routes.some((route) => route.id === cameraRouteFilter))
      .filter((camera) => {
        if (!query) return true;
        const search = [camera.id, camera.name, camera.sourceName, ...camera.zones, ...camera.routes.map((route) => route.stage)]
          .join(" ")
          .toLowerCase();
        return search.includes(query);
      })
      .slice(0, 80);
  }, [cameras, cameraQuery, cameraRouteFilter]);

  function setField<Key extends keyof CaseForm>(key: Key, value: CaseForm[Key]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function loadExample() {
    const now = new Date();
    const before = new Date(now.getTime() - 70 * 60 * 1000);
    setForm({
      item: "蓝色周转箱",
      description: "约 40×30 cm，内有待品检铝件 12 件",
      location: "仓库品检到打包区",
      lastSeen: dateTimeLocal(before),
      discovered: dateTimeLocal(now),
      owner: "现场主管",
      routeId: "auto",
    });
    setFormError("");
  }

  function buildPlan(event: FormEvent) {
    event.preventDefault();
    setFormError("");
    if (!form.item.trim() || !form.location.trim() || !form.lastSeen || !form.discovered) {
      setFormError("请先填写物品、最后确认地点和两个时间点。");
      return;
    }
    if (new Date(form.discovered) <= new Date(form.lastSeen)) {
      setFormError("首次发现时间必须晚于最后确认时间。");
      return;
    }
    if (!cameras.length) {
      setFormError("摄像头能力数据仍在加载，请稍后再试。");
      return;
    }

    const intentText = `${form.item} ${form.description} ${form.location}`;
    const resolvedRouteId = form.routeId === "auto" ? recommendRoute(intentText) : form.routeId;
    const routeCameras = cameras
      .map((camera) => ({
        camera,
        route: camera.routes.find((route) => route.id === resolvedRouteId),
      }))
      .filter((item): item is { camera: Camera; route: CameraRoute } => Boolean(item.route))
      .sort((a, b) => a.route.sequence - b.route.sequence);

    const matchedKeywords = KEYWORDS.filter((keyword) => intentText.includes(keyword));
    const directMatches = cameras
      .filter((camera) => {
        const haystack = `${camera.name} ${camera.zones.join(" ")}`;
        return matchedKeywords.some((keyword) => haystack.includes(keyword));
      })
      .sort((a, b) => Number(b.evidenceLevel === "E2_direct_observation") - Number(a.evidenceLevel === "E2_direct_observation"));

    const ordered = [...routeCameras.map((item) => item.camera), ...directMatches];
    const unique = ordered.filter((camera, index) => ordered.findIndex((candidate) => candidate.id === camera.id) === index).slice(0, 12);
    const routeByCamera = new Map(routeCameras.map((item) => [item.camera.id, item.route]));
    const plan: PlanCamera[] = unique.map((camera, index) => {
      const route = routeByCamera.get(camera.id);
      const directReason = matchedKeywords.find((keyword) => `${camera.name} ${camera.zones.join(" ")}`.includes(keyword));
      return {
        cameraId: camera.id,
        name: camera.name,
        stage: route?.stage ?? (directReason ? `${directReason}直接匹配` : "补充候选"),
        sequence: index + 1,
        reason: directReason ? `地点关键词“${directReason}”匹配；${evidenceReason(camera)}` : evidenceReason(camera),
        evidenceLevel: camera.evidenceLevel,
        evidenceScope: camera.evidenceScope,
        onlineClaim: camera.onlineClaim,
        review: { outcome: "pending", note: "" },
      };
    });

    const stamp = new Date();
    const id = `EVT-${stamp.getFullYear()}${String(stamp.getMonth() + 1).padStart(2, "0")}${String(stamp.getDate()).padStart(2, "0")}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`;
    const investigation: Investigation = {
      id,
      item: form.item.trim(),
      description: form.description.trim(),
      location: form.location.trim(),
      lastSeen: form.lastSeen,
      discovered: form.discovered,
      owner: form.owner.trim(),
      routeId: resolvedRouteId,
      routeTitle: routeName(resolvedRouteId, cameras),
      windowStart: addMinutes(form.lastSeen, -10),
      windowEnd: addMinutes(form.discovered, 10),
      status: "待回看",
      createdAt: new Date().toISOString(),
      plan,
    };
    setCases((current) => [investigation, ...current]);
    setSelectedCaseId(id);
    setActiveView("cases");
  }

  function updateReview(cameraId: string, outcome: ReviewOutcome, note?: string) {
    if (!selectedCaseId) return;
    setCases((current) =>
      current.map((item) => {
        if (item.id !== selectedCaseId) return item;
        const plan = item.plan.map((camera) =>
          camera.cameraId === cameraId
            ? {
                ...camera,
                review: {
                  outcome,
                  note: note ?? camera.review.note,
                  updatedAt: new Date().toISOString(),
                },
              }
            : camera,
        );
        const reviewedCount = plan.filter((camera) => camera.review.outcome !== "pending").length;
        return { ...item, plan, status: reviewedCount ? "复核中" : item.status };
      }),
    );
  }

  function updateReviewNote(cameraId: string, note: string) {
    if (!selectedCaseId) return;
    setCases((current) =>
      current.map((item) =>
        item.id === selectedCaseId
          ? {
              ...item,
              plan: item.plan.map((camera) =>
                camera.cameraId === cameraId ? { ...camera, review: { ...camera.review, note } } : camera,
              ),
            }
          : item,
      ),
    );
  }

  function closeCase() {
    if (!selectedCaseId) return;
    setCases((current) => current.map((item) => (item.id === selectedCaseId ? { ...item, status: "已关闭" } : item)));
  }

  async function checkCoverage(cameraId: string) {
    if (!selectedCase) return;
    setCoverageLoading((current) => ({ ...current, [cameraId]: true }));
    let coverage: CoverageCheck;
    try {
      const response = await fetch("/api/gate/coverage", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ cameraId, start: selectedCase.windowStart, end: selectedCase.windowEnd }),
      });
      const payload = (await response.json()) as Omit<CoverageCheck, "checkedAt">;
      coverage = {
        status: payload.status ?? "unknown",
        clockStatus: payload.clockStatus ?? "unknown",
        decodeStatus: payload.decodeStatus ?? "unknown",
        evidenceIds: Array.isArray(payload.evidenceIds) ? payload.evidenceIds : [],
        gaps: Array.isArray(payload.gaps) ? payload.gaps : [],
        note: payload.note || "覆盖查询未返回可用说明。",
        checkedAt: new Date().toISOString(),
      };
    } catch {
      coverage = {
        status: "unknown",
        clockStatus: "unknown",
        decodeStatus: "unknown",
        evidenceIds: [],
        gaps: [],
        note: "覆盖查询失败；未知不等于没有录像。",
        checkedAt: new Date().toISOString(),
      };
    }
    setCases((current) =>
      current.map((item) =>
        item.id === selectedCase.id
          ? { ...item, plan: item.plan.map((camera) => (camera.cameraId === cameraId ? { ...camera, coverage } : camera)) }
          : item,
      ),
    );
    setCoverageLoading((current) => ({ ...current, [cameraId]: false }));
  }

  function openCase(id: string) {
    setSelectedCaseId(id);
    setActiveView("cases");
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="寻证工作台首页">
          <span className="brand-mark" aria-hidden="true"><span /></span>
          <span>
            <strong>寻证</strong>
            <small>主管监控调查工作台</small>
          </span>
        </a>
        <nav aria-label="主要功能">
          <button className={activeView === "new" ? "nav-active" : ""} onClick={() => setActiveView("new")}>新建调查</button>
          <button className={activeView === "cases" ? "nav-active" : ""} onClick={() => setActiveView("cases")}>事件台账 <span>{cases.length}</span></button>
          <button className={activeView === "map" ? "nav-active" : ""} onClick={() => setActiveView("map")}>能力地图</button>
        </nav>
        <div className={`local-badge ${gateStatus.mode === "connected" ? "connected" : ""}`}><i /> {gateStatus.label}</div>
      </header>

      <section className="status-ribbon" aria-label="系统状态">
        <div><span>摄像头目录</span><strong>{cameras.length || "—"}</strong><small>脱敏逻辑通道</small></div>
        <div><span>直接视觉证据</span><strong>{metrics.direct || "—"}</strong><small>不是识别准确率</small></div>
        <div><span>仅名称线索</span><strong>{metrics.sourceOnly || "—"}</strong><small>需现场确认</small></div>
        <div><span>最近盘点在线</span><strong>{metrics.online || "—"}</strong><small>非实时状态</small></div>
        <div className="system-note"><span className="pulse" /> {gateStatus.detail}</div>
      </section>

      {activeView === "new" && (
        <div className="page-grid" id="top">
          <section className="intro-panel">
            <p className="eyebrow">Lost item investigation · v0.1 local</p>
            <h1>先缩小范围，<br />再回看证据。</h1>
            <p className="lede">把“东西在哪丢的”变成一条可复核的最小摄像头路线。系统只推荐候选，不替代人的事实判断。</p>
            <ol className="flow-list">
              <li><span>01</span><div><strong>定时间</strong><small>最后确认到首次发现</small></div></li>
              <li><span>02</span><div><strong>选路线</strong><small>从 172 路能力卡召回</small></div></li>
              <li><span>03</span><div><strong>人复核</strong><small>确认、排除或标记未知</small></div></li>
            </ol>
            <div className="boundary-card">
              <strong>证据边界</strong>
              <p>“未检测到”或“没有画面”不能证明事件没有发生。禁止身份识别、考勤判断和自动责任归因。</p>
            </div>
          </section>

          <section className="form-card">
            <div className="section-heading">
              <div><span className="step-chip">01</span><h2>建立事件单</h2></div>
              <button type="button" className="text-button" onClick={loadExample}>载入示例</button>
            </div>
            <form onSubmit={buildPlan}>
              <div className="form-row two">
                <label>丢失物品<span>必填</span><input value={form.item} onChange={(event) => setField("item", event.target.value)} placeholder="例如：蓝色周转箱" /></label>
                <label>调查负责人<input value={form.owner} onChange={(event) => setField("owner", event.target.value)} placeholder="具名主管或复核人" /></label>
              </div>
              <label>外观、数量和可辨识特征<textarea value={form.description} onChange={(event) => setField("description", event.target.value)} placeholder="只写物品特征，不猜测人员身份" rows={3} /></label>
              <label>最后一次人工确认地点<span>必填</span><input value={form.location} onChange={(event) => setField("location", event.target.value)} placeholder="例如：仓库品检到打包区" /></label>
              <div className="form-row two">
                <label>最后确认时间<span>必填</span><input type="datetime-local" value={form.lastSeen} onChange={(event) => setField("lastSeen", event.target.value)} /></label>
                <label>首次发现丢失<span>必填</span><input type="datetime-local" value={form.discovered} onChange={(event) => setField("discovered", event.target.value)} /></label>
              </div>
              <fieldset>
                <legend>业务检索路线</legend>
                <div className="route-options">
                  {ROUTES.map((route) => (
                    <label className={form.routeId === route.id ? "route-option selected" : "route-option"} key={route.id}>
                      <input type="radio" name="route" value={route.id} checked={form.routeId === route.id} onChange={() => setField("routeId", route.id)} />
                      <span className="radio-dot" />
                      <span><strong>{route.title}</strong><small>{route.summary}</small></span>
                    </label>
                  ))}
                </div>
              </fieldset>
              {formError && <p className="form-error" role="alert">{formError}</p>}
              <div className="form-action">
                <div><strong>输出</strong><span>候选路线 · 最小窗口 · 人工复核单</span></div>
                <button className="primary-button" type="submit">生成回看计划 <b aria-hidden="true">→</b></button>
              </div>
            </form>
          </section>
        </div>
      )}

      {activeView === "cases" && (
        <section className="workspace-page">
          <aside className="case-sidebar">
            <div className="section-heading compact"><div><span className="step-chip">台账</span><h2>调查事件</h2></div><button className="square-button" onClick={() => setActiveView("new")} aria-label="新建调查">＋</button></div>
            {cases.length === 0 ? (
              <div className="empty-state"><span>◎</span><strong>还没有事件单</strong><p>从明确的物品、地点和时间开始。</p><button onClick={() => setActiveView("new")}>建立第一张事件单</button></div>
            ) : (
              <div className="case-list">
                {cases.map((item) => {
                  const reviewed = item.plan.filter((camera) => camera.review.outcome !== "pending").length;
                  return (
                    <button className={selectedCaseId === item.id ? "case-item active" : "case-item"} onClick={() => openCase(item.id)} key={item.id}>
                      <span className={`case-status ${item.status === "已关闭" ? "closed" : ""}`}>{item.status}</span>
                      <strong>{item.item}</strong>
                      <small>{item.id} · {item.location}</small>
                      <span className="progress"><i style={{ width: `${item.plan.length ? (reviewed / item.plan.length) * 100 : 0}%` }} /></span>
                      <em>{reviewed}/{item.plan.length} 已复核</em>
                    </button>
                  );
                })}
              </div>
            )}
          </aside>

          <div className="case-detail">
            {!selectedCase ? (
              <div className="detail-placeholder"><span className="radar" /><h2>选择一张事件单</h2><p>查看候选摄像头路线并记录人工复核结论。</p></div>
            ) : (
              <>
                <div className="case-hero">
                  <div>
                    <p className="eyebrow">{selectedCase.id} · {selectedCase.status}</p>
                    <h1>{selectedCase.item}</h1>
                    <p>{selectedCase.description || "未填写物品特征"}</p>
                  </div>
                  <div className="case-actions"><button onClick={() => exportCaseMarkdown(selectedCase)}>导出事件单</button><button className="close-button" disabled={selectedCase.status === "已关闭"} onClick={closeCase}>{selectedCase.status === "已关闭" ? "事件已关闭" : "关闭事件"}</button></div>
                </div>

                <div className="case-facts">
                  <div><span>最后确认</span><strong>{displayTime(selectedCase.lastSeen)}</strong><small>{selectedCase.location}</small></div>
                  <div><span>首次发现</span><strong>{displayTime(selectedCase.discovered)}</strong><small>由人工提供</small></div>
                  <div className="window-fact"><span>建议最小回看窗口</span><strong>{displayTime(selectedCase.windowStart)} — {displayTime(selectedCase.windowEnd)}</strong><small>前后各保留 10 分钟缓冲</small></div>
                  <div><span>负责人</span><strong>{selectedCase.owner || "待指定"}</strong><small>关键结论建议双人复核</small></div>
                </div>

                <div className="route-heading">
                  <div><span className="step-chip">02</span><div><h2>{selectedCase.routeTitle}</h2><p>{selectedCase.plan.length} 路候选；顺序只用于检索，不证明物理相邻或必经。</p></div></div>
                  <div className="legend"><span><i className="dot evidence" />有视觉证据</span><span><i className="dot source" />仅名称线索</span></div>
                </div>

                <div className="review-list">
                  {selectedCase.plan.map((camera, index) => (
                    <article className={`review-card ${camera.review.outcome}`} key={camera.cameraId}>
                      <div className="route-line" aria-hidden="true"><span>{String(index + 1).padStart(2, "0")}</span><i /></div>
                      <div className="camera-main">
                        <div className="camera-title">
                          <div><span className="stage-label">{camera.stage}</span><h3>{camera.name}</h3><code>{camera.cameraId}</code></div>
                          <div className="camera-badges"><span className={camera.evidenceLevel === "E2_direct_observation" ? "evidence-badge" : "source-badge"}>{EVIDENCE_LABEL[camera.evidenceScope]}</span><span className={camera.onlineClaim ? "inventory-online" : "inventory-unknown"}>{camera.onlineClaim ? "盘点在线" : "状态待查"}</span></div>
                        </div>
                        <p className="camera-reason">{camera.reason}</p>
                        <div className={`coverage-check ${camera.coverage?.status ?? "not-checked"}`}>
                          <div>
                            <strong>{camera.coverage ? COVERAGE_LABEL[camera.coverage.status] : "尚未查询目标时段录像"}</strong>
                            <span>{camera.coverage?.note ?? "只查询覆盖与缺口，不自动判断事件是否发生。"}</span>
                          </div>
                          <button type="button" disabled={coverageLoading[camera.cameraId]} onClick={() => checkCoverage(camera.cameraId)}>
                            {coverageLoading[camera.cameraId] ? "查询中…" : camera.coverage ? "重新查询" : "查询录像覆盖"}
                          </button>
                        </div>
                        <div className="review-controls">
                          <label>人工复核结论<select value={camera.review.outcome} onChange={(event) => updateReview(camera.cameraId, event.target.value as ReviewOutcome)}><option value="pending">待复核</option><option value="relevant">有相关线索</option><option value="irrelevant">与本事件无关</option><option value="unclear">无法判断</option></select></label>
                          <label className="note-field">客观备注<input value={camera.review.note} onChange={(event) => updateReviewNote(camera.cameraId, event.target.value)} placeholder="例如：目标时段无录像，记录为证据缺口" /></label>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>

                <div className="integration-gate">
                  <div><span className="gate-icon">⌁</span><div><strong>{gateStatus.mode === "connected" ? "生产录像覆盖查询已接入" : "下一接入门：生产录像能力"}</strong><p>{gateStatus.mode === "connected" ? "四台录像机已通过本地只读连接器接入，可查询目标时段的录像覆盖与缺口；代表帧提取和对象候选筛选仍保持关闭。" : "当前已完成脱敏路线规划、人工复核闭环和独立只读接口契约；无需原版 GatePersonAudit 源码，接入符合契约的录像服务后即可启用覆盖查询。"}</p></div></div>
                  <span>FAIL-CLOSED</span>
                </div>
              </>
            )}
          </div>
        </section>
      )}

      {activeView === "map" && (
        <section className="map-page">
          <div className="map-header">
            <div><p className="eyebrow">Camera capability map</p><h1>172 路摄像头，先看证据再用。</h1><p>这里展示的是 2026-07-28 协作包中的脱敏能力快照，不是实时设备状态。</p></div>
            <div className="map-summary"><div><strong>{metrics.direct}</strong><span>有直接视觉证据</span></div><div><strong>{metrics.sourceOnly}</strong><span>仅名称线索</span></div></div>
          </div>
          <div className="map-filters">
            <label>搜索摄像头、区域或阶段<input value={cameraQuery} onChange={(event) => setCameraQuery(event.target.value)} placeholder="例如：打包、外发、车床、出货门" /></label>
            <label>业务路线<select value={cameraRouteFilter} onChange={(event) => setCameraRouteFilter(event.target.value)}><option value="all">全部摄像头</option>{ROUTES.filter((route) => route.id !== "auto").map((route) => <option value={route.id} key={route.id}>{route.title}</option>)}</select></label>
            <span className="result-count">显示 {filteredCameras.length}{filteredCameras.length === 80 ? "+" : ""} 路</span>
          </div>
          <div className="camera-table" role="table" aria-label="摄像头能力列表">
            <div className="camera-table-head" role="row"><span>逻辑摄像头</span><span>证据范围</span><span>候选区域</span><span>时间映射</span><span>盘点状态</span></div>
            {filteredCameras.map((camera) => (
              <div className="camera-table-row" role="row" key={camera.id}>
                <div><strong>{camera.name}</strong><code>{camera.id}</code></div>
                <span className={camera.evidenceLevel === "E2_direct_observation" ? "evidence-badge" : "source-badge"}>{EVIDENCE_LABEL[camera.evidenceScope]}</span>
                <span>{camera.zones.length ? camera.zones.join(" · ") : "待现场确认"}</span>
                <span>{camera.timeMappingStatus.includes("validated") ? "已有历史校准" : "目标时段需校验"}</span>
                <span className={camera.onlineClaim ? "online-text" : "muted-text"}>{camera.onlineClaim ? "盘点在线" : "待查"}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <footer><span>寻证工作台 · 本机脱敏原型</span><span>数据基线：GatePersonAudit 0.21.0 协作包 · 2026-08-02</span><span>不含账号、地址、录像或人员身份</span></footer>
    </main>
  );
}
