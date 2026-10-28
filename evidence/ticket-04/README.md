# 04 验收证据

日期：2026-10-29。实现：票 04（正常核验并核实保存）。

## red → green

在 03 基线 `f916593`（无核验、保存、导出能力）上运行本票 CLI 验收测试：

```text
$ py -m pytest tests/test_review_save.py
FFFFFF                                                                   [100%]
6 failed
```

失败原因均为 `cta review/save/export` 不存在、无核验记录与保存核实，不是语法/环境错误（见 `red.txt`）。实现后：

```text
$ uv run pytest -q
63 passed
$ uv run ruff check src tests
All checks passed!
```

## 确定性完整路径（Mock 注入）

`generate_scripted.py` 可复现：创建 → 简报 → 草稿 → 独立核验（通过）→ 核实保存 → 重新导出。

- `01-create.json` 到 `03-draft.json`：4 轮、140 分、`draft_ready`。
- `04-review.json`：评估 Agent 在独立上下文返回 `pass/full`；核验费用 80 分；请求 1 次、回读 0 次；载入 `content-review 2026-10-29.v1`。
- `05-save.json`：`complete`；保存费用 10 分；累计 230 分；保存记录 `verified=true`，5 个文件逐一记录 sha256 与字节数。
- `06-export.json`：从 SQLite 重新导出，文件哈希一致、`verified=true`。
- `delivery/`：`campaign-v1.md`、`campaign-v1.json`、`execution-report.md`、`brief-v1.json`、`trace.jsonl`。
- `07-inspect.json`：同一权威事实的消息、产物、核验、保存、账本与请求记录。

执行报告含：状态/版本、模拟时钟与完成时刻、真实墙钟与 API 耗时、预算与账本、计划步骤、工具结果、简要决策依据（工具调用名称或助手说明）、核验意见、保存文件与缺口；不含密钥或内部思维。保存后 `export` 只校验既有文件、不重写，哈希与保存记录一致。

## 真实模型核验（已执行，未通过）

任务 `t_708f84239aa0`（03 的完整草稿，真实 DeepSeek）：

- 第一次 `review`：评估响应一次返回两个 `read_material` 调用，被协议校验拒绝；当时实现只把拒绝写入数据库、未回送下一请求，核验记为 `incomplete`（`08-real-review-incomplete.json`，`requests_used=1`）。该问题已修复：拒绝/回读结果现在追加到评估对话并进入下一请求，由 `test_non_object_review_arguments_*`、多调用与回读上限测试覆盖。
- 第二次 `review`：模型改用单动作核验，返回 `fail/full` 与 3 条 `must_fix`（阻塞事实与制作步骤矛盾、音乐授权晚于截止、切片与主片安排不一致），并给出具体位置、要求、依据与建议（`09-real-review-fail.json`）。累计费用 600 分。
- `save`：被 `unresolved_must_fix` 拒绝，任务保持 `in_progress/reviewed`，不写成功保存记录、不谎报完成（`10-real-save-blocked.json`，退出码 4）。

该轨迹证明：核验是独立上下文、真实依据判断；多调用被拒后可以继续；未处置关键意见不能保存。它也暴露 06 需要交付的处置与返修能力。

## 验收对照

| 验收点 | 证据 |
| --- | --- |
| 独立上下文评估完整方案，提供核验知识/要求/证据/程序检查/被审版本 | `04-review.json`、`reviewer_knowledge`、`llm_requests.meta.context=isolated_review` |
| 五项质量要求、意见/依据/完成程度 | 真实 `09-real-review-fail.json` 的 3 条 must_fix；确定性 pass |
| 评估请求/回读边界与用量留痕 | `requests_used`、`readbacks_used`、`llm_requests`；多调用拒绝 |
| 保存前核对版本/评审适用/关键问题/预算/截止/保存消耗 | `_preconditions`；`save_without_review`、`must_fix`、`blocking question`、失败注入测试 |
| 保存针对明确版本；完成需记录与文件一致 | `05-save.json`、`delivery/` 哈希；`complete` |
| SQLite 同一权威导出方案/报告/JSON/Trace | `06-export.json`、`delivery/`；`export` 重建哈希一致 |
| 报告含计划、步骤、工具结果、决策依据、分类成本、真实耗时、模拟完成时间 | `delivery/execution-report.md` |
| 正常完成、未解决质量、明确保存失败、已完成重复查询 | 9 个测试 + 确定性/真实证据 |
| 密钥与内部思维不泄露 | 报告、Trace、evidence 检查；密钥仅 `set/missing` |

## 已知限制

- 意见处置（采纳/拒绝/待查证）与返修复审由 06 交付；本票在存在 must_fix 时安全停止。
- 保存未知、导出修复与版本不一致由 08 交付；本票只处理明确失败与已核实成功。
- 真实任务目前停在 `reviewed`，未完成；这是如实状态，不是本票失败。
