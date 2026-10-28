# 05 验收证据：主循环和生成中断后的跨进程续跑

日期：2026-10-29。实现：票 05（基于 `2309b38171c236a056b29362d5ea70ae372b16fc`）。

## red → green

测试 seam：真实 CLI 子进程（`python -m cta`）+ 真实落盘 SQLite + 导出文件；模型与故障只在系统边界注入。

先写 `tests/test_process_resume.py` 的首批续跑测试（缺回执、已完成检索复用），在实现前运行：

```text
$ $env:PYTHONPATH="<worktree>/src"; <venv>/python.exe -m pytest tests/test_process_resume.py -q
FF                                                                       [100%]
2 failed
```

失败原因是真实子进程没有在注入点中断（`assert 0 == 70`），即尚不存在可验证的“进程在工具回执前死亡后由新进程恢复”行为，见 `red-01.txt`。随后补上 save 未知分支测试并先运行：

```text
$ ... -m pytest tests/test_process_resume.py -q
..........F..
1 failed
```

失败原因是 `cta save` 崩溃注入点不存在、保存记录未被标为未知（`assert 0 == 70`），见 `red-04.txt`。实现后：

```text
$ $env:PYTHONPATH="<worktree>/src"; <venv>/python.exe -m pytest -q
...................................................................      [100%]
67 passed
$ <venv>/python.exe -m ruff check src tests
All checks passed!
```

完整输出见 `green.txt`、`ruff.txt`。

## 确定性 CLI 验收记录（真实新进程）

重新生成：

```powershell
D:/AAA小影科技/content-task-agent/.venv/Scripts/python.exe evidence/ticket-05/generate.py
```

`generate.py` 通过 `subprocess` 启动真实 `python -m cta`，`CTA_FAULT_CRASH` 仅作为测试注入让子进程在指定系统边界退出（退出码 70），随后由另一个真实进程 `resume`。

| 记录 | 场景与可核对事实 |
| --- | --- |
| `01`–`04` | 主循环缺回执：`03-inspect-before-resume.json` 中 assistant 工具调用存在、无对应 tool 回执、无尝试/费用；`04-resume-missing-receipt.json` 追加匹配的 `interrupted` 回执，恢复后 `brief_ready`、3 轮、20 分、`recovery.pending_calls=1`、`interrupted_calls=[search_materials]`。 |
| `05`–`10` | 生成中断：`07-crash-generation.txt` 记录真实子进程在生成尝试启动后退出；`08` 显示 search 成功 + generate `running`、已发生 140 分、无草稿；`09` 恢复后 `draft_ready`、5 轮、260 分；`10` 的尝试序列为 `success / interrupted / success`，旧检索/简报/第一笔生成费用保留，重试是新尝试。 |
| `11`–`16` | 保存未知：`12` 在 save 记录写入后真实退出；`13` 显示同版本 save 未核实、save 尝试 `running`、已发生 350 分；`14` 由新进程续跑后保持 `incomplete / save_unknown`，save 变 `unknown` 且 `verified=false`，未新增保存尝试；`15` 再次 `save` 退出码 4、`save_error.code=save_unknown`，不重放。 |
| `17`–`21` | 暂停/续跑：`18` 简报后 2000 ms、2 轮、20 分；`19` 暂停后时钟/轮数不变；`20` 普通 `run` 返回 `paused` 且不调用模型；`21` 显式续跑后 10000 ms、4 轮、140 分、预算剩余 860 分，输入版本/预算/截止沿用。 |

所有记录只含 Mock 测试注入与合成数据；不包含密钥或模型内部思维。

## 验收对照

| 票面验收点 | 实现与证据 |
| --- | --- |
| 显式续跑恢复原输入、任务/产物版本、实际消息与调用结果、已加载资料/知识、评审、尝试、账本、模拟进度、已用轮数与待办 | `resume` 先恢复 SQLite 权威记录：`recovery.reused` 汇总 input/brief/output/review/save/knowledge/materials/search_results/attempts/spent；状态视图新增 `todos`、`recovery`；`04`、`09`、`10`、`21`；测试 `test_resume_reuses_completed_review...`、`test_pause_freezes...`。 |
| 重启先核实进行中/缺结果动作，再追加恢复事实与快照；缺回执追加有依据结果或中断/未知，保持合法配对 | `recovery.py` 按 pending tool call 核实 attempt/简报/草稿/澄清/停止状态与生成消息；`append_recovery_fact` 追加 `[恢复事实]`，`append_snapshot` 追加包含 review/save/todos/recovery_count 的当前快照；`03`/`04`、`10` 的 assistant/tool 配对；测试 `test_resume_after_missing_receipt...`。 |
| 已完成且有效的检索、草稿、评审复用；生成无效时只重跑未完成有界步骤 | 检索尝试成功但回执丢失时复用同一 attempt 重建结果（`test_resume_reuses_completed_search_attempt...`）；`propose_campaign` 已写入时恢复成功而不重复创建（`test_resume_recovers_campaign_effect...`）；完整生成候选从 `generator` 消息校验后复用（`test_complete_generation_candidate...`）；review 完成后 `resume` 停在 `reviewed`，不新增请求（`test_resume_reuses_completed_review...`）。 |
| 已启动尝试固定费用/耗时延续、不重复收费；真实重试新记；未知用量保留；不完整输出只追溯 | `start_attempt` 在启动时固定 cost/duration/sim 起止并记账；恢复只更新状态/回执，不新建 attempt/ledger；重试由模型再次调用工具创建新 attempt（`09`/`10`：20+120+120=260 分）；坏候选响应保留在 generator 消息但不形成 output（`test_incomplete_generation_output_stays_traceable...`，6 条 generator 消息、仅 v1 草稿）。 |
| 暂停不推进时钟；恢复继续原模拟进度/预算/截止/轮数；回退重启不撤销消耗，触顶续跑无新额度 | `pause` 仅更新 paused 与快照；`run` paused 早退；`resume` 清除 paused 后继续。`21`：2000→10000 ms、2→4 轮、140 分、剩余 860；`test_resume_after_round_cap...`：12 轮后恢复仍 12 轮、0 新请求/费用、原因含“新额度”。 |
| 恢复采用已保存消息/资料/知识正文，外部文件变化或冷缓存不无声替换，不压缩历史 | `material_snapshots` 首次使用时冻结任务材料；生成与回读只读任务快照；知识从 `knowledge_loads` 正文恢复。测试 `test_resume_uses_saved_material_and_knowledge_bodies...` 在 crash 后修改注入的 catalog/knowledge 文件，恢复生成提示仍含“旧资料标记AAA/旧知识标记OLDK”，不含新标记；旧消息前缀逐条相等。 |
| 主模型不可用时保留进度并明确未完成，不隐藏自动重试或伪降级；后续动作仍由模型决定 | 模型不可用/请求失败分别记 `model_unavailable` / `model_error`，不写 llm_request 成功或降级结果；`resume` 追加恢复事实后由模型脚本继续下一步。测试 `test_model_unavailable_preserves_progress...`、`test_resume_after_model_request_error...`；恢复事实含上次原因。 |
| 保存状态无法核实时保持未知并安全停止，不盲目重放；正常保存核实复用 | `recovery._detect_unknown_save` 将同版本未核实 save 标 `unknown` + `phase=save_unknown` + `incomplete`；`save.py` 对未知记录返回 `save_unknown` 且不启动新尝试（`14`/`15`/`16`，测试 `test_resume_keeps_unknown_save...`）。正常 save 路径仍由 04 的版本/评审/预算/截止前置检查与逐文件 sha256 核实。 |
| 真实新进程 CLI 故障注入覆盖主循环中断、生成失败/中断、冷缓存、缺回执、触顶续跑；对照前后成果、消息、消耗、时间、轮数与待办 | `generate.py` 与 `tests/test_process_resume.py` 全部用真实子进程；覆盖缺回执、生成中断/重试、完整候选恢复、草稿写入后中断、暂停续跑、触顶、模型不可用/请求失败、保存未知、外部资料/知识变化；记录恢复前后字段。 |

## 已知限制

- 本票不实现评估中断的自动恢复（06）与保存回执丢失的完整核实/导出修复（08）；评估未完成时 `resume` 停在 `review_incomplete`，保存未知时停在 `save_unknown`。
- 真实 DeepSeek API 未被调用；本票验收是确定性 Mock 注入 + 真实 SQLite/子进程，只证明规则与流程，不证明真实模型决策或语义质量。
- 故障注入点（`after_tool_request`、`after_tool_effect`、`after_campaign_effect`、`search_after_start`、`generation_after_start`、`generation_after_response`、`save_after_record`）与 `CTA_MATERIALS_PATH`/`CTA_KNOWLEDGE_DIR` 是测试边界，不是面向用户的产品命令。
- 保存未知只做安全停止；已写文件、文件哈希与重放策略留 08 处理。
