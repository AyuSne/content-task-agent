# 工单 10 确定性验证记录

本记录仅包含 Mock 和本地 HTTP 假对端的验证，不是 DeepSeek 真实调用或内容语义质量证据。原始输出按执行顺序保留如下；日志不包含任务数据或凭据。

- `ticket10-full-before-request-refinement.log`：`PYTHONPATH=src python -m pytest -p no:cacheprovider --basetemp .scratch/test-10-full-v2 -q --tb=short`，287 项通过，进程退出码 0。执行时是集成 `2252b61` 加工作区中的上下文实现、v2 价目及旧实验隔离；该状态当时尚未提交，不能将本次结果归属于某个精确提交。随后仅精确化了 Mock 与真实 SDK 的请求记录及对应测试，本次全量结果不冒充最终 HEAD 的完整回归。
- `ticket10-final-targeted.log`：在代码提交 `71fcd03` 上执行 `PYTHONPATH=src python -m pytest -p no:cacheprovider --basetemp .scratch/test-10-evidence-final tests/test_context_composition.py tests/test_legacy_experiment_entry.py tests/test_model_direct.py tests/test_protocol_freeze.py tests/test_task_pricing.py tests/test_strategy.py tests/test_comparison.py -q --tb=short`，33 项通过，进程退出码 0。包含本地假对端接收与实际 SDK 入参逐字段核对；SDK 测试不是对外真实模型请求。
- `examples/context-redesign/mock-request-roundtrip.json` 是同一新请求接口下，公开 CLI 创建、运行、inspect 得到的两轮 Mock 样例，含 13 项完整工具和真实调用/结果配对；不含密钥。

最终集成提交仍需合并后进行适当检查；真实模型、新旧对照和语义质量由工单 11 单独完成并留证。

## 2026-10-30 独立组合核查

- 主控报告：集成代码 `8c9fe06` 的完整确定性回归 `317 passed in 390.29s`、退出码 0，定向 53 项及 Ruff 通过；`6292739` 只追加进度文档。该结果由主控提供，不归称为此工作树自行执行的全量结果。
- 基于 `6292739` 的独立工作树：`PYTHONPATH=src python -m pytest tests/test_ticket10_combinations.py tests/scope_risk_probe.py tests/test_context_composition.py tests/test_review_dispositions_new.py tests/test_task_pricing.py -q -p no:cacheprovider --basetemp D:/AAA小影科技/.scratch/t10a --tb=short`，35 项通过；Ruff `check --no-cache src tests scripts` 及 `git diff --check` 通过。第一次使用较深的工作树临时路径时，两个旧版本归档测试因 Windows 路径过长报错；更换项目内短路径后完整重跑通过，未跳过断言。
- 新增三项公开 CLI 组合：已保存方案经人工修改导入、多次历史要求更新、当前语义要求变化、计划动作提交后中断及跨进程恢复，检查历史不丢失、当前方案不适用、旧评审和保存不适用、恢复不重收费、不重复占用轮次，并由脚本化模型明确停止；另在同一交付中核对计划、评审处置、实际尝试、版本、210 分费用、运营方案和报告及所有当前保存文件的 SHA-256。此组合不声称穷举各事件排列，也不证明模型自行选择正确方向。
- 保存工具回执中的文件哈希是该动作返回时的快照。主循环随后把回执与最终状态写入历史并刷新报告和追踪，因此历史回执中这两份文件哈希可能与最后文件不同；`inspect` 中同一保存记录的最新哈希才用于最终对账。测试同时检查当时回执已核实和当前持久记录全部文件哈希，未把旧回执当作最终文件快照。
- 实际两轮 Mock 请求核对 13 项工具名称及完整描述/schema、系统正文、任务预装 Skill 全文、目录、费用和状态，条件引用的正文从工具结果逐字核对；系统和 Skill 均允许依据充分时直接创作，脚本工具不另调用生成模型，方案接收不等于核验交付。历史完整录制为 `examples/context-redesign/mock-request-roundtrip.json`；真实 SDK 入参与本地假对端逐字段比对见 `tests/test_context_composition.py`。这些证据不证明真实主模型遵循规则，也不证明所有语义场景通过。

### 适用范围的确定性契约与剩余风险

- 创作简报、脚本及方案的 `scope` 是非空的自由概括，不要求是材料范围的逐字片段；程序核对材料、适用版本和正文引文等结构和定位，但不判断自由概括是否语义等价或被支持。不能将处置依据的原文片段规则移植到此字段，否则会误拒合法概括，也不能声称非空即正确。
- 驳回处置的材料依据是另一契约：`scope` 必须是所采用材料适用范围中的原文片段，材料版本和引文也须定位；程序验证的是位置而非是否足以支持驳回。主 Agent 对处置依据作语义判断，独立核验 Agent 获得当前方案和适用材料正文、范围，负责核验事实与范围是否被支持；程序限制核验完整性、版本、处置及核实保存，但不独立保证模型判断正确。
- `tests/scope_risk_probe.py` 明确复现：材料限定 iOS/Android 4.2 及以上，错误概括覆盖更旧版本，脚本化核验预设 pass 后仍可核实保存。这说明存在错误范围通过保存的风险；Mock pass 既不证明真实核验会漏检，也不证明其能捕获。未增加提示词修饰或虚假的字面匹配保证；真实核验和主模型的语义表现、旧新对照留待工单 11，当前范围风险未解除。

## 2026-10-30 合并者逐项核对

`10a7ad2` 是 `6292739` 与 `5d407dd` 的非快进合并；合并前目标干净且后者直接以目标为父提交。以 `PYTHONPATH=src` 执行 `.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp D:/AAA小影科技/.scratch/t10merge-20261030 -q --tb=short tests tests/scope_risk_probe.py`，退出码 0；该次 pytest 的双重安静模式不显示摘要，随后收集核对总数并扣除事后新增用例，确定当次为 320 项。单独运行新增 `tests/test_ticket10_clarification.py`：1 项通过。`ruff check --no-cache src tests scripts` 和 `git diff --check` 通过。均为脚本化模型或本地假对端，不是外部真实模型。

1. **完整请求**：`tests/test_ticket10_combinations.py::test_actual_request_has_consistent_system_tools_and_loaded_skill`、`tests/test_context_composition.py::test_full_actual_request_after_updates_starts_with_stable_prefix_and_retains_tool_history` 和 `examples/context-redesign/mock-request-roundtrip.json` 核对实际两轮请求、13 项工具、正文、Skill、目录、费用、状态及调用配对；SDK 与本地接收端的入参核对另见 `test_real_sdk_cli_request_record_matches_local_wire_payload`。
2. **共同纪律**：上述完整请求及 `test_script_tool_contract_and_system_prompt_agree_on_material_evidence` 核对直接创作、接收不等于交付、素材字段一致性；`tests/test_strategy.py::test_new_toolset_excludes_fixed_comparison_selection_and_generation`、`tests/test_direct_script.py::test_direct_script_is_saved_and_charged_without_generator` 核对旧固定工具和独立生成不再出现。
3. **当前事实**：`test_human_draft_semantic_change_and_interruption_preserve_current_facts` 和 `test_semantic_change_keeps_history_but_rejects_old_campaign_and_marks_conclusions_inapplicable` 核对当前输入、产物适用性及旧评审/保存失效，计划明确不构成成功证据；脚本化模型停止不证明真实模型自行选择正确方向。
4. **相邻恢复**：前述人工稿组合跨进程故障恢复，核对历史、轮次及费用；`tests/test_context_composition.py` 的缺回执外部编辑导入/修复用例验证继续或停止。请求的 13 项工具集合无独立产物回读，历史消息保留。
5. **查证、澄清、创作与停止**：`tests/test_brief_loop.py` 覆盖检索读取、缺口和待澄清状态，`tests/test_strategy.py` 覆盖补查与资源不足停止，`tests/test_direct_script.py` 覆盖直接创作及澄清保留成果；新增 `tests/test_ticket10_clarification.py` 验证两个阻塞问题同次返回、持久化并进入待补充。建议作为简报内容及依据由相关提交用例核对，真实模型在具体场景中的自主判断留 11。
6. **双视图对账**：`test_delivery_views_reconcile_plan_disposition_cost_and_verified_version` 检查运营方案及执行报告与计划、处置、尝试、版本、210 分模拟费用和当前保存文件哈希；保存前未完成，核验及核实保存后完成。
7. **新协议回归**：本次完整确定性测试包括直连本地对端、Mock 来源、脱敏、合法消息及轮次/恢复上限；风险探针通过意味着成功复现已知风险，而非范围语义合格。测试通过不代表真实模型质量或同场景对照通过。
