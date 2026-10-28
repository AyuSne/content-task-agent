# 07 验收证据

日期：2026-10-29。实现：票 07（资源和工具反馈后的策略调整）。
基线：integration `2309b38171c236a056b29362d5ea70ae372b16fc`。

## red → green

在基线的临时源码副本上运行本票最终测试文件（`tests/test_strategy.py`），产品代码来自 `git archive HEAD`，测试来自本分支：

```text
$env:PYTHONPATH="<baseline>/src"
D:/AAA小影科技/content-task-agent/.venv/Scripts/python.exe -m pytest tests/test_strategy.py -q
FFFFFFFFFFFFFF                                                           [100%]
14 failed
```

失败为未知工具/未知 CLI 命令、缺少 `estimates`/`stops`/资源分类等真实缺口，不是语法或环境错误；完整输出见 `red.txt`。实现后：

```text
$env:PYTHONPATH="<worktree>/src"
D:/AAA小影科技/content-task-agent/.venv/Scripts/python.exe -m pytest -q
....................................................................     [100%]
$ D:/AAA小影科技/content-task-agent/.venv/Scripts/python.exe -m ruff check src tests
All checks passed!
```

完整输出见 `green.txt`。测试通过 CLI 公开输出断言；模型只在系统边界注入，跨进程/落盘状态使用真实 SQLite。

## 确定性样例（Mock 注入）

`generate_evidence.py` 可复现全部 JSON 与交付文件：

```powershell
$env:PYTHONPATH="<worktree>/src"
D:/AAA小影科技/content-task-agent/.venv/Scripts/python.exe evidence/ticket-07/generate_evidence.py
```

### 1. 超预算后有可行替代并完成（`01`-`06` + `delivery-budget-alternative/`）

预算 2.50 元（250 分）。检索后 `compare_strategies` 显示扩展检索 + 多候选择优完整路径 390 分，只剩 230 分；`choose_strategy` 被 `strategy_infeasible` 拒绝并返回基本检索 + 直接生成 210 分的可行替代。模型按替代完成简报、生成、核验与保存：

- 状态 `complete`；7 轮；已发生 230 分（20 + 120 + 80 + 10），未把估算/预留计入消耗。
- 选择记录：`user_scenario / search_basic / generate_direct`，预留 210 分；预算缺口为“完整路径需要 390 分，当前只剩 230 分”。
- 执行尝试严格使用所选模式：`search_basic`、`generate_direct`、`review`、`save`。
- 交付文件、报告、Trace 见 `delivery-budget-alternative/`；Trace 含 `estimate`、`strategy_selection` 事件。

### 2. 无可行路径时保留成果并说明缺口（`07`-`09`）

预算 0.50 元（50 分）。检索后最低成本完整路径仍不可行：

- `compare_strategies` 的 `cheapest_feasible` 为 `null`；四个模式组合均 `budget_feasible=false`、`deadline_feasible=true`。
- `choose_strategy` 被拒绝，`strategy_selections` 为空，未产生预留或额外消耗。
- 模型调用 `stop_incomplete` 说明“剩余预算不足以完成生成、核验与保存”，并列出两条具体缺口及影响；既有 20 分检索消耗保留，状态 `incomplete`。

### 3. 检索失败后补查，失败尝试与消耗保留（`10`-`13`）

`CTA_FAIL_SEARCH=1` 注入第一次检索失败：

- 第一次检索尝试 `failed`，20 分保留；第二次换检索词成功并形成简报。
- 最终两条检索消耗共 40 分；失败结果、成功结果、简报与选择记录都在同一任务中，未被清除。
- 本样例按计划停止并记录未生成方案的缺口，用于观察“补查后继续/停止”的反馈闭环。

### 4. 有明确预期收益的改进投入（`14`-`19` + `delivery-improvement/`）

基础路径 `generate_direct` 已成功形成可用脚本后，模型再比较并选择 `generate_multi`：

- 选择记录 `improvement=true`，预期收益“比较三个开场候选，降低单一候选的表达风险”，完整剩余路径 390 分（300 + 80 + 10）。
- 两个生成尝试均成功且都保留：`generate_direct` 120 分、`generate_multi` 300 分；总计 530 分（+ 80 核验 + 10 保存）。
- 这不是固定“先贵后便宜”路线，也不是盲目重做：改进有明确目标与完整路径理由，预算与 12 轮上限均未突破。

## 验收对照

| 验收点 | 证据 |
| --- | --- |
| 创作方向包含文字目标、资料需求与资源取舍，模型按实际反馈选择 | `01`-`06`、`14`-`19`；`fixtures/strategy/2026-10-29.v1.json`；`compare_strategies` 结果 |
| 比较已发生消耗 + 完整剩余路径（检索/生成/核验/保存/返修复核） | `estimate_cost` 与 `compare_strategies` 的 `total_cost_cents` / `total_duration_ms`；`re_review` 行；`test_estimate_covers_full_remaining_path_without_charging` |
| 两种检索、两种生成模式的有限内部次数、费用、耗时、失败规则、候选数留版本 | `05-save.json` 的 `strategy.mode_table`；`test_compare_strategies_returns_versioned_full_paths_without_charge`；`test_multi_candidate_generation_uses_bounded_internal_requests` |
| 执行前检查参数/依赖/预算/截止，超限反馈；失败后重新估算；拒绝不虚记 | `01`、`07`、`08`；`test_generation_failure_reestimate_and_alternative_then_complete`；`test_budget_guard_rejects_before_attempt_and_model_continues` |
| 模拟工具费、真实模型用量、活动费用分开；未知与不适用分开；不重复汇总 | `05-save.json` / `18-improvement-save.json` 的 `resources`；`test_resources_separate_incurred_estimated_reserved_and_unknown_vs_not_applicable` |
| 有替代时原要求内调整；无路径/关键缺陷/上限时保留成果并明确未完成及缺口 | `01`-`06`、`07`-`09`；`test_round_cap_records_limit_gap_and_keeps_spend` |
| 可行后仍有明确预期收益的改进，说明目标与全路径理由 | `14`-`19`；`test_improvement_after_workable_baseline_keeps_both_successful_attempts`；`test_choose_improvement_requires_expected_benefit_and_records_reservation` |
| 失败/部分成功后由模型决定补查、替代或停止；成功不重做、失败与消耗不清除 | `10`-`13`；`test_search_failure_is_preserved_and_model_rechecks`；`test_partial_generation_success_keeps_valid_candidate_and_cost`；`test_generation_failure_reestimate_and_alternative_then_complete` |
| CLI 注入超限有替代、无路径、费用/时间只满足其一、失败与改进样例，允许多种有效序列 | `01`-`19` 全部经 `python -m cta`；`test_budget_and_deadline_feasibility_are_reported_separately`；两条不同完成路线（替代/改进） |

## 执行报告与 Trace

`delivery-budget-alternative/execution-report.md` 含：

- 创作策略：方向、检索/生成模式、理由、改进与预期收益、完整路径估算、策略表/费用表版本。
- 成本口径：模拟工具费用（已发生/估算/预留分开）、真实模型请求与金额未知、活动费用未知。
- 停止/缺口、核验、保存、账本与工具结果。

`trace.jsonl` 含 `estimate`、`strategy_selection`、`stop` 事件，以及原有消息、尝试、账本、请求、核验、保存事件。

## 已知限制

- 本票确定性样例全部为 Mock 注入；未新增真实 DeepSeek 调用。07 授权真实 API 但非必需，真实反馈轨迹由 12 汇集验收。
- 策略表与费用表为合成配置，只证明规则与流程，不证明真实业务效果或价格。
- 活动费用只在方案素材涉及待获取/待制作项且没有金额时标为 `unknown`；未涉及标为 `not_applicable`；不把任务预算混作活动预算。
- 意见处置、返修复审与评估中断恢复由 06 交付；保存未知与导出修复由 08 交付。本票只按完整路径纳入预计开销，不实现返修流程。
- 真实模型金额因缺少可靠单价保持未知；Mock 运行不据此推断真实费用。
