# 58号门v2审查、质量闸门与跨电脑验收

## `reviewed_candidates.csv`

每行表示一个候选越界，不表示已经计数。必须包含以下字段：

| 字段 | 允许值或说明 |
|---|---|
| `candidate_id` | 本次运行内唯一ID |
| `event_time` | 画面角标时间，`YYYY-mm-ddTHH:MM:SS` |
| `evidence_start` | 不晚于事件前20秒 |
| `evidence_end` | 不早于事件后20秒 |
| `start_side` | `inside/outside/boundary/unknown` |
| `boundary_crossed` | `yes/no/unknown` |
| `end_side` | `inside/outside/boundary/unknown` |
| `occluded` | `yes/no/unknown` |
| `evidence_paths` | 任何判定至少两条；明确计数至少起点、跨门、终点三条绝对路径，以 `|` 分隔 |
| `review_note` | 只写可见事实 |

## 唯一计数模式

- `outside + yes + inside + occluded=no` → `明确进入`。
- `inside + yes + outside + occluded=no` → `明确外出`。
- 起终点同侧、`boundary_crossed=no` → `明确不构成进出`。
- 任一端为 `unknown/boundary`、跨门为 `unknown` 或存在遮挡 → `待复核`，暂不计数，但证据路径不得留空。

起终点同侧却标为已经越界表示一行混入了完整折返。契约会拒绝该行；应拆为进入和外出两行，每行各自保留完整证据。

## 一次提问内的自动收敛

第一次契约汇总后，`pending>2` 时 `quality_gate=needs_review`。此时不得先回答人数，也不得等待用户追加提示词；必须自动用 `gate58_pending_manifest.py` 为待复核行补取前后45秒连续证据，只填写脚本生成的 `pending_decisions.csv`，再由 `gate58_apply_pending_reviews.py` 验证“全部待复核项均已处理”并生成第二轮审查表。最多两轮，仍未通过时只给确认范围。

## 一致性粒度与验收

正式结果按每次完整人员越界为一行。同机同输入复跑必须得到完全相同清单。跨电脑的业务验收允许解码帧落点造成的轻微差异：同方向事件时间容差3秒，进入、外出、合计各自最多差2人次，而且两边待复核均不得超过2条。

最终CSV和汇总由 `scripts/gate58_review_contract.py`生成。配置哈希使用规范化JSON，不受Windows/macOS换行符影响。任何人不得在脚本生成后手工改数字或在运行目录编写临时修数脚本。
