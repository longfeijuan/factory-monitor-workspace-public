# 第二台电脑安装说明

## 能复制什么

GitHub 仓库负责复制：监控问答规则、摄像头脱敏目录、只读查询代码、区域技能、网页工作台和测试。

普通使用者不需要 GitHub 账号，也不需要加入“黄伟工作群”；公开分发仓库已经包含审核过的脱敏摄像头资料。

只有需要查询真实录像的电脑，才必须通过公司批准的安全渠道单独取得4台 NVR 只读连接项，并接入公司内网或获批 VPN。普通使用者不需要钉钉 `dws` 或黄伟工作群权限；`dws` 只供指定资料同步人读取内部原始发布渠道。因为安全和隐私要求，账号密码、原始录像、截图、人员参考图和本机历史运行结果不会跟随 GitHub 同步。

## Windows 一键安装（推荐）

同事直接打开下面这个固定公开链接，不需要注册或登录 GitHub：

[下载 Windows 一键安装器](https://github.com/longfeijuan/factory-monitor-workspace-public/releases/latest/download/INSTALL-WINDOWS.cmd)

运行 `INSTALL-WINDOWS.cmd` 后，安装器会：

1. 安装或检查 Git、Node.js 22+、Python 3.11、pnpm 和 Codex；
2. 匿名克隆或安全快进更新 `%USERPROFILE%\Codex\factory-monitor-workspace-public`；
3. 创建项目专用 `.venv`，安装 Python 和 Node.js 依赖；
4. 运行仓库自检、摄像头目录校验、连接器/监控契约测试、网页构建与渲染测试；
5. 本机缺少 NVR 连接项时，提示隐藏录入4台只读连接信息，只保存到当前 Windows 用户的凭据管理器；
6. 仅在全部质量门通过后执行 `codex app <项目目录>`，直接在 Windows Codex 中打开项目。

现有目录有未提交改动、GitHub 无法访问或任何质量门失败时，安装器会停止。验证证书保存在本机 `runtime/onboarding/windows-ready.json`，该目录不会提交到 GitHub。

## 手动安装（故障排查备用）

```bash
git clone https://github.com/longfeijuan/factory-monitor-workspace-public.git
cd factory-monitor-workspace-public
node --version  # 必须能直接找到Node.js 22+
python3 scripts/monitor_self_check.py
pnpm install --frozen-lockfile
python3 -m pip install -r requirements-monitor.txt
pnpm run test:connector
pnpm test
```

`pnpm run test:connector` 包含58号门统一口径的合成回归样本：明确进入、明确外出、室外路过、门口停留、未越界折返、完整越界后折返和遮挡待复核。该测试未通过时，不得在第二台电脑对CEO报告58号门人数。

需要 YOLO 候选检测时再安装 `python3 -m pip install -r requirements-ml.txt`。模型权重按任务在本机下载，不进入 Git。

手动安装时，在 Codex 中新增本地项目，把克隆后的仓库根目录设为主目录。不要只把它作为第二附加目录：Codex 从主目录自动发现 `AGENTS.md`、`.agents/skills/` 和 `.codex/environments/environment.toml`。

## 首次实时查询

安装器会自动检查本机凭据状态。若安装时暂未取得公司批准的4台只读连接项，之后在项目目录双击 `SETUP-NVR-CREDENTIALS.cmd`；输入密码时屏幕不会显示字符。也可以手动执行：

```powershell
.\.venv\Scripts\python.exe connector\gate_nvr_service.py --setup-credentials
.\.venv\Scripts\python.exe connector\gate_nvr_service.py --credential-status
.\.venv\Scripts\python.exe scripts\monitor_self_check.py --live
.\.venv\Scripts\python.exe connector\gate_nvr_service.py --check
```

连接项只保存在当前 Windows 用户的凭据管理器中，不进入项目目录、GitHub、报告或聊天记录。公开链接不能安全携带共用密码；连接项必须由公司授权人员通过批准的密码管理器、当面录入或其他安全渠道交付。

普通使用者不要运行黄伟工作群同步命令。只有指定资料同步人在确认群权限后执行：

```bash
python3 scripts/fetch_latest_camera_package.py --apply
python3 scripts/sync_camera_catalog.py
python3 scripts/monitor_self_check.py
```

同步人审核变更后提交并推送 GitHub；其他电脑通过 `git pull` 获取更新。

在 macOS 上，连接器从系统钥匙串读取凭据。换机时不要导出旧电脑钥匙串、Windows 凭据、配置 JSON 或聊天里的密码；应在新电脑重新从获批来源安全录入。

### Windows 查询材多监控

先确保电脑在公司内网或获批 VPN 中，并已通过 `SETUP-NVR-CREDENTIALS.cmd` 把4台只读连接项保存到 Windows 凭据管理器。材多查询从仓库根目录运行，不需要黄伟工作群或 `--import-from-dingtalk`：

```powershell
.\.venv\Scripts\python.exe .agents/skills/caiduo-high-speed-saw-runtime/scripts/analyze_runtime.py `
  --camera-id 022 `
  --start '2026-08-31T08:00:00+08:00' `
  --end '2026-08-31T12:00:00+08:00' `
  --output-dir "$PWD/outputs/caiduo-runtime-022"
```

查询时从 Windows 凭据管理器读取连接项，凭据不会写入 GitHub、配置文件或报告。以后在 Windows Codex 项目里直接提问材多开机率时，项目技能会自动读取，使用者不需要在提示词里写命令。如果出现 `nvr_credentials_unavailable`，依次检查 `.\.venv\Scripts\python.exe connector\gate_nvr_service.py --credential-status`、公司内网/VPN和 `.\.venv\Scripts\python.exe connector\gate_nvr_service.py --check`。

## 开始问问题

检查通过后，直接从这个 Codex 项目提问即可。Codex 会先报告仓库内置资料包的文件名和发布时间，再按问题加载对应技能。没有黄伟工作群权限不会影响读取监控目录、规则和技能；只有“核对群内是否发布了更新”必须由资料同步人完成。

若电脑不在公司网络、本机没有完整 NVR 只读连接项或没有录像权限，仍可回答摄像头目录、统计口径和操作方法，但不能声称已经核查了真实录像。

## 58号门跨电脑一致性验收

两台电脑开始同一查询前，都必须位于同一个干净Git提交，并确认 `camera-data/current/SOURCE.json` 与 `config/gate58-people-crossing-v2.json` 一致。配置指纹按规范化 JSON 内容计算，因此 Windows 的 CRLF 与 macOS 的 LF 不会再造成假差异。正式查询必须从仓库根目录触发 `audit-gate58-people-crossing` 技能，不能让 Codex 在聊天或 `audit-output/` 临时另写脚本修数。

用户仍然只说一次原来的查询提示词。任务内部会自动完成第一轮和待复核事件的扩大证据复核；中间结果不作为正式答案。每次查询必须使用全新的输出目录，最终待复核不超过2条才允许给出单一数值，否则只能报告范围和阻塞原因。

两台电脑分别生成 `final_events.csv` 后运行：

```bash
python3 scripts/compare_gate58_results.py \
  /path/computer-a/final_events.csv \
  /path/computer-b/final_events.csv \
  --output /absolute/local/compare.json
```

`accepted_for_user_goal: true` 表示达到当前目标：两边规则版本、查询指纹和资料包一致，每边待复核不超过2条，且进入、外出、合计三个数各自相差不超过2人次。同机复跑仍要求 `identical: true`。未通过时保留证据和差异清单，不能临时修改规则迁就其中一台电脑。

## 其他监控数值的统一验收

除58号门专用契约外，项目里的开机率、运行率、次数和持续时间使用 `run-context.json` 与 `result-envelope.json`。同一句提示词在两台电脑运行后比较：

```bash
python3 scripts/compare_monitor_results.py \
  /path/computer-a/result-envelope.json \
  /path/computer-b/result-envelope.json \
  --output /absolute/local/compare.json
```

只有 `query_id` 相同、两边质量闸门均通过并且所有指标均在约定偏差内，`accepted_for_user_goal` 才会是 `true`。项目默认偏差为计数2、比率1.0个百分点、时长5分钟；区域技能可以规定更严格的值。`query_id` 不同表示两台电脑实际没有执行同一输入，不能直接拿最后数字互相比较。

## 私密的本机校准资料

以下资料必须在每台获批电脑单独建立，不能提交 GitHub：

- 侧门关闭基准图与真实通知收件人；
- 带人员或现场画面的身份/位置参考图；
- 新增或移动机台的状态灯 ROI 校准图；
- 任务产生的录像帧、联系表、证据图和报告草稿。

缺少私密校准图时，可以做普通目录查询和画面人工复核；涉及具名身份、精确灯位或自动通知时必须标为“需本机校准/待核实”，不得猜测。
