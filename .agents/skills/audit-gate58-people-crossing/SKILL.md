---
name: audit-gate58-people-crossing
description: Audit 大井街58号大门 personnel entry and exit counts through one versioned runner with a fixed physical threshold, automatic pending-evidence retry, and a measurable cross-computer tolerance. Use when the user asks 58号大门、今天进出多少人、进入/外出人数、门口进出明细、两台电脑结果是否接近, or requests Gate-58 crossing evidence.
---

# 大井街58号大门人员进出统计

## 目标

同一时段、同一句用户提示词在不同获批电脑上必须自动走同一流程；不允许靠用户追加提示词反复修数。候选粗筛可以过量，但最终数字只能由版本化程序和固定证据契约生成。

验收目标：同机同输入复跑完全一致；跨电脑进入、外出、合计各自绝对差值不超过2人次。超过即判定未达标，不把任一电脑的数字强行改成另一台。

## 开始前

1. 读取仓库根目录的 `camera-data/current/SOURCE.json`，运行 `python3 scripts/sync_camera_catalog.py --check`，报告仓库内置资料包版本。
2. 读取 [references/camera-map.md](references/camera-map.md) 与 [references/decision-contract.md](references/decision-contract.md)。
3. 将相对日期转换为 `Asia/Shanghai` 绝对起止时间，不得扩大用户时段。
4. 每次查询必须创建全新空目录，并从仓库根目录运行唯一准备脚本；跨电脑正式验收必须使用 `--strict-reproducible`：

```bash
python3 .agents/skills/audit-gate58-people-crossing/scripts/prepare_gate58_review.py \
  --start 2026-08-27T08:00:00 \
  --end 2026-08-27T12:00:00 \
  --output-dir /absolute/local/audit-output/gate58-YYYYMMDD-HHMM-HHMM-run-YYYYMMDDTHHMMSS \
  --strict-reproducible
```

禁止复用以前的输出目录、`reviewed_candidates.csv`、最终数字或对话记忆。禁止在 `audit-output/` 临时编写分析脚本。严格模式若发现仓库有未提交改动、结束时点尚未形成稳定回放、资料包或规则不一致，应停止正式统计。

## 固定判定口径

- `进入`：连续画面明确显示 `outside → boundary → inside`。
- `外出`：连续画面明确显示 `inside → boundary → outside`。
- 门口停留、未越界折返、始终位于门外的路过人员一律不计。
- 已经完成越界后再折返，每一次完整越界分别计数。
- 遮挡、角标不可读、缺少起点侧、缺少终点侧或连续证据不足，统一标为 `待复核`，不计数。
- 初审每个候选至少审查事件前20秒和后20秒、抽帧间隔不大于0.5秒。
- 每一条判定（包括待复核）都必须保留证据路径；明确计数必须保留起点、跨门、终点三个节点。
- 以监控画面角标为准；检测器时间、文件名和播放器进度只用于定位。

## 执行流程

1. 执行 `review_plan.json` 中的只读命令，得到移动事件、合并候选和固定0.5秒连续审查帧。不得换用聊天中临时生成的脚本。
2. 候选阶段保留所有可能接近门界的人员；不要在粗筛阶段根据单帧方向删除候选。
3. 按真实物理门界逐人连续追踪。画面左侧深色车库地面为内侧；右侧亮色门外地面、外侧通道和道路均为外侧。室外人员即使靠近门口，只要始终在外侧就不计。
4. 将逐条观察写入 `reviewed_candidates.csv`，字段和允许值按 [references/decision-contract.md](references/decision-contract.md)。
5. 只能通过契约脚本生成最终清单和汇总；必须传入准备计划中的同一 `query_id`：

```bash
python3 scripts/gate58_review_contract.py \
  /absolute/local/reviewed_candidates.csv \
  /absolute/local/final_events.csv \
  --summary /absolute/local/summary.json \
  --query-start YYYY-mm-ddTHH:MM:SS \
  --query-end YYYY-mm-ddTHH:MM:SS \
  --query-id <review_plan.json中的query_id>
```

6. 读取 `summary.json`：
   - `quality_gate=pass` 才可结束并回答用户。
   - `quality_gate=needs_review` 时，不向用户输出中间人数；在同一轮自动执行 `pending_review_commands`。只在固定的 `pending_decisions.csv` 填写二次观察，随后由版本化合并脚本完整覆盖全部待复核项并再次运行契约。
   - 最多完成配置规定的两轮。仍超过2条待复核时，只报告确认区间和录像缺陷，不把确认下限冒充总人数。

契约脚本报错时不得手改数字；应修正证据行。整个流程从用户角度只有一次提问和一次最终回答，不要求用户追加“再复核”。

## 跨电脑验收

两台电脑必须满足以下条件才可称为“结果一致”：

1. `policy_version`、规范化JSON `config_sha256`、Git提交与 `query_id` 相同。配置哈希不受Windows CRLF与macOS LF换行影响。
2. 资料包文件名、发布时间和ZIP SHA-256相同。
3. 查询的绝对起止时间、录像机、通道、回放轨道相同。
4. 先检查质量闸门，再按用户目标验收：进入、外出、合计各自差值均不超过2；事件时间允许因解码落点相差3秒。同一台电脑同输入复跑仍必须完全一致。

使用：

```bash
python3 scripts/compare_gate58_results.py \
  /path/computer-a-final.csv \
  /path/computer-b-final.csv \
  --output /absolute/local/compare.json
```

比较脚本退出码为2表示未达到用户目标。不得让任一电脑自行放宽口径，也不得通过反复提示词把数字修成一样；应报告版本、查询指纹、待复核数或录像源差异。

## 输出

只发送一次最终结果。先给绝对检查时段、规则版本、查询指纹和资料包版本，再给进入、外出、合计、待复核数及 `quality_gate`。`needs_review` 时不得称为正式总数。提供最终CSV与 `summary.json` 路径；没有确认事件时不得写成绝对没有。

## 资源

- [references/camera-map.md](references/camera-map.md)：固定录像机、通道、门界和内外侧。
- [references/decision-contract.md](references/decision-contract.md)：v2审查CSV、质量闸门和跨电脑容差。
- `scripts/prepare_gate58_review.py`：生成版本指纹及只读审查计划。
- 仓库根目录 `scripts/gate58_review_contract.py`：唯一正式汇总入口。
- 仓库根目录 `scripts/gate58_pending_manifest.py`：只为待复核条目生成固定二次证据清单。
- 仓库根目录 `scripts/gate58_apply_pending_reviews.py`：验证并合并完整的二次复核表，取代运行目录内临时脚本。
- 仓库根目录 `scripts/compare_gate58_results.py`：两台电脑逐事件一致性检查。
