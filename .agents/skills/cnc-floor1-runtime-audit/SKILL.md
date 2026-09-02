---
name: cnc-floor1-runtime-audit
description: Audit the six first-floor CNC milling machines from the versioned channel-49 plus fisheye-1 tower-light ROIs and report reproducible green-light runtime rates, stopped periods, unknown coverage, and result fingerprints. Use when the user asks about 一楼电脑锣、六台电脑锣、电脑锣开机率、运行率、绿灯、停机时段、白班开机率、夜班开机率, or wants the same CNC runtime query compared across computers. Do not use this skill for personnel attendance, off-post, phone use, or seated-position behavior.
---

# 一楼电脑锣六台机开机率

## 唯一正式口径

正式结果只能使用仓库中的 `config/cnc-floor1-runtime-v3.json` 和本技能脚本。固定映射为：

- 1、2、3、5、6号机：`nvr-main-02` 通道 `49` / 轨道 `4901`（一楼电脑锣过道，`1280×720`）
- 4号机：`nvr-main-02` 通道 `2` / 轨道 `201`（一楼鱼眼1，`2560×1440`）
- 规则版本：`cnc-floor1-green-blink-v3.0-dual-view`
- 六台物理编号、每台唯一来源和固定灯位坐标随配置进入查询指纹

通道 `30` 是人员侧道辅助视角，不得用于六台绿灯开机率。鱼眼1只补通道49看不到的物理4号机；不得把鱼眼1的其他灯位重复并入分母。不要从旧对话、旧结果目录或聊天临时代码读取机台数字。

当前标定从 `2026-08-28 00:00:00` 起适用。查询更早录像时，必须明确写“当前v3标定不适用于该历史画面”，使用经审核的历史标定另建规则版本；不得把当前坐标静默套到其他画面。

## 班次与分母

- 白班整班：`08:00–20:00`；有效生产时段为 `08:00–12:00`、`13:30–17:30`、`18:00–20:00`。
- 夜班整班：`20:00–次日08:00`；有效生产时段为 `20:00–00:00`、`02:00–08:00`。
- 用户指定部分时段时，仅统计指定范围与有效生产时段的交集，并同时保留指定范围的完整原始率。
- 未知点单列，不并入停机；正式开机率分母只使用 `running + stopped`。

## 判定方法

每五分钟建立一个采样点，每个点读取连续10秒画面。绿灯正常运行时可能闪烁，因此不得凭单张暗帧判停机：

- 连续窗口覆盖至少9秒且满足最少帧数，才可判定。
- 窗口内至少2帧达到固定绿色阈值，判为 `running`。
- 完整窗口内无绿帧，判为 `stopped`。
- 首个10秒窗口只有1个孤立绿帧或覆盖不足时，程序在同一次查询内自动延长到20秒复核；延长后至少2个普通绿帧，或1个达到固定强信号门槛的绿帧，判为 `running`，证据仍不足才记 `unknown`。
- 回放失败、延长后帧数不足、覆盖不足或坐标无法命中，统一判为 `unknown`。

阈值、窗口长度、采样间隔、事件偏移及六个灯位都从版本化JSON读取，运行时不得临时修改。

## 执行流程

1. 把“今天、昨天、白班、夜班”换算成 `Asia/Shanghai` 的绝对起止时间，并确认结束时间不晚于录像可稳定回放的时间。
2. 在仓库根目录运行自检和目录校验：

```bash
python3 scripts/monitor_self_check.py
python3 scripts/sync_camera_catalog.py --check
```

3. 每次创建全新的、被Git忽略的输出目录。正式跨电脑查询必须加 `--strict-reproducible`：

```bash
python3 .agents/skills/cnc-floor1-runtime-audit/scripts/collect_runtime.py \
  --start 2026-08-28T08:00:00 \
  --end 2026-08-28T17:20:00 \
  --output-dir audit-output/cnc-floor1-20260828-0800-1720-r01 \
  --strict-reproducible
```

4. 脚本固定完成查询指纹、全新采样清单、并行连续窗口分析、失败点单线程重试、质量闸门和 `result-envelope.json`。不得在运行目录写临时脚本修数。
5. 若质量闸门未通过，结果只能标为“待复核”，不能报告0%或其他生产结论。若通过，才可报告有效生产时段和完整查询时段的六台分别/合计开机率。

## 质量闸门

以下条件全部满足才允许 `official_result=true`：

- 六台机都有计划采样点；
- 有效生产时段每台机和六台合计的已知覆盖率均不低于95%；
- 没有最终仍失败的回放窗口；
- 当前查询时间不早于v3标定生效时间；
- 每台机只来自配置指定的唯一画面，4号必须来自鱼眼1、5号必须来自通道49；
- 仓库、资料包、规则、绝对时间和参数已生成同一 `query_id`。

跨电脑比较时必须先确认两个 `query_id` 完全一致，再运行：

```bash
python3 scripts/compare_monitor_results.py \
  第一台/result-envelope.json 第二台/result-envelope.json
```

任一比率偏差超过 `1.0` 个百分点即不达标；不得用“模型判断略有不同”解释为正常。

## 输出格式

只发一次最终结果，至少包含：

1. 绝对查询时段、有效生产时段，以及“通道49五台 + 鱼眼1补4号”的固定来源；
2. 1–6号及六台合计的开机率、运行点/已知点；
3. 未知点数量和已知覆盖率；
4. 质量闸门是否通过；
5. `policy_version`、`query_id`、`git_commit`、`normalized_result_sha256`；
6. 本次全新输出目录和关键证据文件路径。

更换灯位或画面布局时，按 [标定与变更规则](references/calibration.md) 建立新版本，禁止覆盖旧版本的含义。
