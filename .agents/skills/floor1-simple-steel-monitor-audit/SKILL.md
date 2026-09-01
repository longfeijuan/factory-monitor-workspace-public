---
name: floor1-simple-steel-monitor-audit
description: Audit the six first-floor simple-steel CNC machines from DingTalk-accessible Hikvision NVR playback and report green-light runtime rates for day or night shifts with local-first, bounded-image review. Use when the user asks about 一楼简易钢件、六台机、白班开机率、夜班开机率、今天/昨天开机率、绿灯运行、59号或31号监控，or requests evidence/recheck of these machine rates.
---

# 一楼简易钢件监控核查

## 固定口径

- 只核查一楼简易钢件6台机。
- `nvr-main-02` 通道59负责1—4号机；通道31负责5—6号机。
- 白班：所指日期 `08:00—20:00`。
- 夜班：所指日期 `20:00—次日08:00`。例如“8月13日夜班”是8月13日20:00至8月14日08:00。
- 耐萨斯所有车间夜班固定休息时间为 `00:00—02:00`。夜班默认有效生产时段为 `20:00—00:00`、`02:00—08:00`，共10小时；休息时段不得列为生产停机异常。
- 每5分钟抽查一次。只有明确绿色指示灯算运行；黄灯、橙灯、红灯、熄灭都算未运行。
- 回放失败、无录像、无有效解码帧、画面异常或灯位被完全遮挡一律记为未知，不得算停机。
- 默认开机率按有效生产时段计算；同时保留并明确标注完整12小时原始率。开机率 = 绿灯有效点 / 有效抽查点。综合开机率按六台有效点加权，不取六个百分比的简单平均。

机台位置、ROI和判灯细节见 [camera-layout.md](references/camera-layout.md)。

## 解析用户时间

1. 先把“今天、昨天、白班、夜班”解析成完整绝对起止时间，并在结果中写出日期。
2. 当前班次未结束时，只查到最近一个已稳定回放的5分钟点，明确标为“阶段开机率”；不要补未来点。
3. 用户只说“白班开机率”或“夜班开机率”时，沿用对话中最近明确的日期；没有日期上下文时，白班默认今天、夜班默认最近已经开始的夜班。

## 执行流程

### 1. 采集回放

优先运行随技能附带的采集脚本：

```bash
python3 scripts/collect_shift.py --date YYYY-MM-DD --shift day --strict-reproducible
python3 scripts/collect_shift.py --date YYYY-MM-DD --shift night --strict-reproducible
```

脚本会定位 `lost-item-investigator`，依次采集59号、31号通道，避免两路同时高速回放导致NVR返回453。可用 `SIMPLE_STEEL_PROJECT` 指向其他项目目录。

每次默认创建带运行时间戳的全新目录，先生成项目统一的 `run-context.json`，把资料包、Git提交、绝对时段、班次、通道、5分钟采样口径和人工覆盖表的规范化哈希锁成同一个 `query_id`。禁止复用昨天的日期目录或以前的分析结果；跨电脑比较时两边 `query_id` 必须相同。

若采集后单台覆盖率低于95%：

1. 对失败点以单并发、`--resume` 重试一次。
2. 仍失败时，用项目内 `scripts/nvr_archive_sample_frames.py` 对连续缺口走HTTP回放补帧。
3. 仍无法补齐就保留未知点，并在结论中列明覆盖率；不得凭相邻时点补判。

### 2. 自动分析

`collect_shift.py` 会调用 [analyze_shift.py](scripts/analyze_shift.py)，生成：

- `green-metrics.csv`：每台、每时点的灯色指标和自动判定；
- `effective-rates.csv`：剔除固定休息时间后的默认有效生产时段统计；
- `preliminary-rates.csv`：完整班次12小时原始统计；
- `m1-light-5min.jpg` 至 `m6-light-5min.jpg`：六台逐点缩略图；
- `review-required.csv`：边界点和异常点。
- `qc-summary.json`：本地覆盖率、解码坏帧和低流量复核闸门；
- `qc-mosaic.jpg`：每台一绿一非绿、共最多12个灯位裁剪组成的单张小质检图。
- `result-envelope.json`：项目统一的查询指纹、质量闸门和可跨电脑比较的各机开机率。

本地脚本对全部有效五分钟点判灯，并自动把纯绿/块状绿色解码坏帧和尺寸异常帧记为未知。白班4号机因远景和日光反射使用较低阈值；夜班统一使用较高阈值。

### 3. 本地质检与低流量复核

严格执行以下顺序：

1. 先读取 `preliminary-rates.csv`、`review-required.csv` 和 `qc-summary.json`；这些都是本地文本，不上传图片。
2. 确认 `quality_gate` 为 `pass`。若不是 `pass`，先按采集步骤补帧或重新校准；不得靠批量上传图片绕过质量闸门。
3. 检查 `qc-mosaic.jpg` 文件大小不超过750KB后，只允许调用一次 `view_image`，使用 `detail:"high"`，不得使用 `detail:"original"`。
4. 禁止查看或上传 `m1-light-5min.jpg` 至 `m6-light-5min.jpg`，禁止查看或上传任何2560×1440原始帧，禁止用Computer Use或连续截图复核监控。
5. 若单张质检图发现明确误判，建立 `manual-overrides.csv`，字段为 `machine,start_local,state,reason`；`state` 只能是 `green`、`amber` 或 `unknown`，随后带 `--overrides` 重新运行分析脚本。
6. 若一张质检图仍无法确认，将相关点记为 `unknown` 或报告需要现场重新校准；不得继续追加图片。白班、夜班分别校准，不沿用另一班的视觉结论。

默认每次班次核查的云端图片预算是1张、总文件不超过750KB。这里的“人工复核”是对12个最接近阈值的灯位裁剪做抽查；全部五分钟点、覆盖率和解码坏帧仍由本地脚本完整处理。

### 4. 输出

先给有效生产时段结论，再给完整12小时原始率、口径和覆盖率。白班与夜班完整班次均为144点/台、864点；完整班次的有效生产时段均为120点/台、720点。固定格式：

```text
YYYY年M月D日一楼简易钢件白班/夜班有效生产时段开机率（列明有效时段）：
1号 xx.x%
2号 xx.x%
3号 xx.x%
4号 xx.x%
5号 xx.x%
6号 xx.x%
六台综合 xx.x%

有效覆盖：xxx/720 个机台抽查点（xx.x%）。
完整12小时原始率：六台综合 xx.x%（xxx/864 个机台抽查点）。
统计口径：每5分钟抽查，仅绿灯计运行；固定休息时间已从默认开机率和主要停机时段中剔除；回放失败点已剔除，未当作停机。
```

若是未结束班次，把“开机率”写成“阶段开机率”，并把截止时点写清楚。覆盖不足95%时明确提示结果为阶段性或低覆盖，不给“确定无误”之类表述。

用户要证据图时，优先给 `qc-mosaic.jpg`。只有用户明确要求某个时点的原图证据时，才可另给最多3张灯位ROI裁剪图，合计不超过1.5MB；不得给整张监控帧。时间以监控画面角标为准。

两台电脑复核同一班次时运行：

```bash
python3 scripts/compare_monitor_results.py \
  /path/computer-a/result-envelope.json \
  /path/computer-b/result-envelope.json
```

只有 `accepted_for_user_goal=true` 才表示达到目标：查询指纹一致、两边质量闸门通过，并且1—6号、六台综合及完整班次综合开机率的差值均不超过1.0个百分点。同机复跑仍要求 `identical=true`。不得通过修改阈值、手工改数或追加提示词把两边凑成一样。

## 边界

- 本技能只回答机台绿灯开机率，不顺带评价人员在岗、看手机或产量。人员行为另走相应人员核查流程。
- 不根据机器声音、现场人员或门是否开启推断运行；只按可见绿色指示灯。
- 不覆盖或删除用户已有审计结果；每次创建独立日期/班次输出目录。
