# 工厂监控问答工作区

这个仓库把“换一台电脑后，Codex 仍能按同一口径回答监控问题”所需的代码、脱敏摄像头资料、区域操作手册和自检流程放在一起。

仓库当前包含黄伟工作群 2026-08-25 发布并已校验的 `公司摄像头主管协作包_20260825_r01`：160 路摄像头、4 台逻辑录像机。仓库不保存账号、密码、网络地址、原始画面、录像、人员参考图、事件截图或本机运行结果。

## Windows 一键安装

同事不需要 GitHub 账号，只需：

1. 点击 [下载 Windows 一键安装器](https://github.com/longfeijuan/factory-monitor-workspace-public/releases/latest/download/INSTALL-WINDOWS.cmd)。
2. 运行下载的 `INSTALL-WINDOWS.cmd`；首次打开 Codex 时，使用本人的 ChatGPT/Codex 账号登录。

安装器会自动检查或安装 Git、Node.js 22+、Python 3.11、pnpm 和 Codex，匿名克隆公开脱敏项目到 `%USERPROFILE%\Codex\factory-monitor-workspace-public`，创建独立 `.venv`，安装依赖，执行仓库自检、摄像头目录校验、连接器测试和网页测试。若本机尚无 NVR 连接项，安装器会提示普通使用者在本机隐藏录入4台只读连接信息，并只保存到当前 Windows 用户的凭据管理器；不需要进入“黄伟工作群”。全部质量门通过后才用 `codex app` 直接打开项目。

GitHub 链接只分发代码、脱敏目录和监控规则，绝不携带密码。真实录像要求同事位于公司内网或获批 VPN，并通过公司批准的安全渠道取得 NVR 只读连接项，在自己的电脑上完成本机录入；凭据不进入 GitHub、项目文件、报告或聊天记录。`dws` 只供指定资料同步人读取内部原始发布渠道，普通查询不依赖 `dws`。完整说明见 [Windows/第二台电脑安装说明](docs/SECOND_COMPUTER_SETUP.md)。

## 手动安装或在 Mac 上使用

1. 安装 Codex、Git、Python 3、Node.js 22+ 和 pnpm。只有指定资料同步人需要安装并登录公司授权的 `dws` 钉钉 CLI；普通实时查询使用本机安全保存的 NVR 只读连接项。
2. 匿名克隆公开脱敏仓库 [longfeijuan/factory-monitor-workspace-public](https://github.com/longfeijuan/factory-monitor-workspace-public)，再在 Codex 中把仓库根目录设为项目主目录。这样 Codex 会自动读取 `AGENTS.md` 和 `.agents/skills/` 下的监控流程。
3. 运行离线自检：

```bash
python3 scripts/monitor_self_check.py
pnpm install --frozen-lockfile
python3 -m pip install -r requirements-monitor.txt
pnpm run test:connector
pnpm test
```

只有运行 YOLO 人体/物品候选脚本时才需要另装 `requirements-ml.txt`；普通 NVR 抽帧、开机率和人员人工复核不需要下载整套机器学习依赖。

4. 普通使用者不需要加入“黄伟工作群”；仓库已经包含审核过的脱敏摄像头资料。Windows 首次需要查询真实录像时，双击 `SETUP-NVR-CREDENTIALS.cmd`，把通过公司批准安全渠道取得的4台只读连接项隐藏录入 Windows 凭据管理器。只有资料同步人才登录 `dws` 并运行第一条黄伟群同步命令：

```bash
# 仅资料同步人执行
python3 scripts/fetch_latest_camera_package.py --apply

# 所有人在资料更新后执行
python3 scripts/sync_camera_catalog.py
python3 scripts/monitor_self_check.py --live
pnpm run connector
```

Windows 从凭据管理器读取本机只读 NVR 连接项；macOS 从系统钥匙串读取。指定资料同步人仍可从获批钉钉会话导入，但普通使用者不需要群权限或 `dws`。凭据不会写入仓库。真实回放还要求电脑位于获批的公司网络或 VPN，并拥有对应录像权限。

更完整的换机步骤见 [docs/SECOND_COMPUTER_SETUP.md](docs/SECOND_COMPUTER_SETUP.md)。

## 直接问 Codex

从仓库根目录开启任务后，可直接问：

- “查昨天一楼简易钢件白班六台机开机率。”
- “查三楼快走丝夜班三个人是否都在，电脑桌有没有连续超过15分钟。”
- “查54号大门有没有电动车停室内，是否有人携物由内向外。”
- “查大井街58号大门今天08:00–12:00进入、外出各多少人。”
- “查尾2两个人在岗情况和六台机绿灯运行率。”
- “查材多22号高速锯板机今天的运行率和停机时段。”
- “查一楼电脑锣操作员离岗、坐岗和禁坐位置异常。”
- “查今天08:00–17:20一楼电脑锣六台机开机率。”

Codex 会先读取仓库内 `camera-data/current/SOURCE.json` 的资料版本，再选择项目内对应技能与只读脚本。只有资料同步人负责核对黄伟工作群并把审核后的更新推送到 GitHub；其他人无需访问该群。无法访问录像机或关键画面时，结论必须标为“未知/待核实”，不能把连接失败或未检测到解释成“没有发生”。

58号大门人员计数使用仓库内 `audit-gate58-people-crossing` 专用技能和 `gate58-people-crossing-v2` 结果契约。同一句提示词只允许走版本化脚本，不允许在聊天或 `audit-output/` 临时写脚本修数。每次使用全新输出目录；第一次仍有超过2条待复核时，任务会在同一轮自动扩大证据窗口再处理，不要求用户追加提示词。验收目标为同机复跑完全一致、跨电脑进入/外出/合计各自偏差不超过2人次。

整个监控项目共用 `monitor-project-reproducibility-v1`：先用资料包、Git提交、绝对时段、区域规则和参数生成 `query_id`，再封装质量闸门通过的数值结果。跨电脑默认验收为人数/次数偏差不超过2、开机率/运行率偏差不超过1.0个百分点、持续时长偏差不超过5分钟；区域专用契约更严格时从严。一楼简易钢件已接入该统一结果封装，每次默认创建全新运行目录，杜绝误读昨天的旧结果。

一楼电脑锣六台机开机率使用独立的 `cnc-floor1-runtime-audit` 技能：固定通道49、六个版本化灯位、每五分钟连续读取10秒并按绿灯闪烁判定；只有1个临界绿帧时自动延长到20秒复核。通道30只用于人员在岗辅助，不能计算开机率；画面移动导致灯位失效时必须返回“待复核”，不能把未检出写成0%。

监控资料的分发权限和更新职责见 [docs/CAMERA_PACKAGE_DISTRIBUTION.md](docs/CAMERA_PACKAGE_DISTRIBUTION.md)。

## 调查工作台

仓库仍包含丢失物品调查网页，可按脱敏目录推荐最小回看路线、检查录像覆盖并导出人工复核事件单：

```bash
pnpm run connector
pnpm run dev
```

通过 `pnpm run connector` 启动时，连接器仅监听 `127.0.0.1:8766`。未启动连接器时，网页会显示脱敏目录模式，不会把连接失败当成没有录像。

## 本地数据边界

以下内容只能留在每台获批电脑本地，并已被 `.gitignore` 排除：

- `audit-output/`、`output/`、`tmp/`、`runtime/`；
- `camera-data/private/`、`secure-state/`；
- 侧门基准图、实际通知人配置、launchd 配置；
- 模型权重 `*.pt`；
- 所有原始录像、截图、人员身份参考图和凭据。

侧门提醒配置从 `config/side-door-alert.example.json` 复制到本地 `config/side-door-alert.json` 后再填写；先保持 `dry_run: true`，完成基准图和收件人授权核验后才启用通知。
