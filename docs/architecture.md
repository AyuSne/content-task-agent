# 实现架构（Agent 入口）

本文件是后续实现 Agent 的共享上下文指针：先读它，再读对应票与规格条款。详细需求以 `D:/AAA小影科技/.scratch/content-task-agent-build/spec.md` 为准；本文件只记录已经落地的组织方式与扩展点，不复制规格。

## 分层与写入边界

```text
src/cta/
  cli.py         CLI 解析、输出 json/text、退出码
  config.py      .env 加载、模式/数据目录、模拟起点、密钥脱敏
  identity.py    输入规范化、校验、请求指纹（不做语义相似）
  db.py          SQLite schema、轻量迁移、事务/连接
  repository.py  消息、尝试、账本、请求、知识、简报、快照的持久化助手
  service.py     任务用例：创建、查询、列表、inspect；后续 update/import
  loop.py        单一主循环：模型动作 -> 工具 -> 反馈 -> 下一轮
  model.py       ModelClient 协议；DeepSeek(openai SDK) / ScriptedClient
  prompts.py     稳定系统规则、工具 schema、Prompt 版本
  pricing.py     版本化费用/耗时表与完整路径估算
  strategy.py    版本化创作方向表、完整路径比较与预留选择
  materials.py   合成资料检索与有界原文回读
  knowledge.py   版本化工作知识加载（正文 + sha256）
  review.py      独立上下文有界评估（最多 3 请求/2 回读）
  save.py        核验前置检查、保存尝试与文件核实
  recovery.py    跨进程恢复：核实未完成动作、追加恢复结果/中断事实、构造恢复快照
  faults.py      仅测试用的系统边界故障注入（环境变量）
  export.py      Markdown/JSON/JSONL Trace 导出与报告
```

- 一票一个 worktree/分支；只改本票范围与自己的报告。共享运行状态、票面、进度索引由主控维护。
- 不新增第二套权威状态：SQLite 是唯一权威，Markdown/JSON/Trace 都从它导出。
- 不引入 Web、插件平台、通用多 Agent 框架；模型调用是系统边界，用 `ModelClient` 协议注入。
- 资料正文是数据不是指令；系统提示明确此边界。业务工具不能扩大用户授权。

## 已落地事实（01-11）

- CLI：`cta [--mode mock|real] [--data-dir DIR] [--format json|text] create|status|run|review|save|export|strategies|inspect|list`。
- 数据目录：`<repo>/.cta-runs/<mode>/tasks.db` 与 `<mode>/exports/`；`CTA_DATA_DIR` 覆盖根目录；真实/Mock 完全分离。
- 模拟起点：`CTA_SIM_START` 或默认 `2026-10-29T00:00:00+08:00`；截止必须晚于它。
- 输入：主题/预算（元，两位小数）/截止必填；受众、创作要求可选。缺失/无效仍持久化，状态 `needs_input`，逐字段错误与解释在 `validation`。
- 身份：`(mode, 规范化字段)` 的 SHA-256 指纹；无效字段按稳定错误码参与。重复 `create` 返回原任务；`--new` 独立。
- 状态：`needs_input | in_progress | incomplete | complete`；阶段 `created | brief_ready | draft_ready | reviewed | complete | review_incomplete | save_failed | stopped`。只有“核验通过 + 文件核实”才可 `complete`。
- 主循环：每轮 `used_rounds += 1` 再调用模型；只接受 0/1 个工具调用；多调用、未知工具、非法参数、截断、无动作都拒绝并反馈。模型错误或 12 轮触顶 → `incomplete`，保留消息、尝试、账本。
- 模型：真实模式 `DeepSeekClient`（openai SDK，DeepSeek base URL，`deepseek-flash`，`thinking.disabled`，非流式，`max_retries=0`）；Mock 模式 `ScriptedClient`（`CTA_MODEL_SCRIPT`，按 `channel` 取脚本），仅测试/离线。
- 上下文：系统规则 + 工具定义 + 知识正文 + 任务输入 + 状态快照 + 追加消息。首轮加载 `campaign-creation` 知识并把正文、版本、sha256 存入 `knowledge_loads`；无变化不重复快照。`brief_ready` 后追加一次阶段推进消息。
- 工具：`search_materials`（basic 20 分/2 s；extended 60 分/6 s）、`read_material`（0）、`estimate_cost`（0，可声明已知剩余检索次数，含预计完成时刻、预算/时间缺口与返修复核）、`compare_strategies`（0，按同一费用表比较三个创作方向的全路径与可行替代）、`choose_strategy`（0，记录方向/模式/理由/改进预期收益与预留，不可行则拒绝并返回替代）、`propose_brief`、`generate_script`（direct 120 分/8 s；multi 300 分/20 s，最多 3 个内部候选）、`propose_campaign`、`request_clarification`、`stop_incomplete`（可带具体 gaps）。
- 策略：`fixtures/strategy/2026-10-29.v1.json` 固定方向表版本；`estimate_cost`/`compare_strategies` 写 `estimates`（nature=estimated），`choose_strategy` 写 `strategy_selections`（nature=reserved）；两者都不推进模拟时钟、不写 `ledger`、不计入预算消耗。执行仍按同一 `fixtures/pricing` 表；生成/核验内部请求上限取自模式表。
- 停止：模型停止、轮数触顶、模拟截止、模型不可用/错误写 `stops`（kind + 具体 gaps），`status.gaps` 与执行报告可回查；既有成功产物、尝试与消耗保留。
- 资源口径：`resources.simulated_tool_fee` 分开 incurred / estimated / reserved；`real_model_usage.amount.status=unknown`（无可靠单价）；`activity_costs` 未涉及为 `not_applicable`、涉及时金额为 `unknown`；`unknown` 与 `not_applicable` 分列，估算/预留不汇总进 `sim_spent_cents`。
- 脚本生成：独立上下文（创作知识 + 简报 + 策略 + 事实/素材），不继承主循环历史；请求写 `llm_requests`（purpose=`script_generation`，meta.context=`isolated_generation`），消息写 `messages.agent='generator'`；候选校验事实 quote 与证据引用。
- 产物：`outputs` 按版本保存完整方案 JSON 与 Markdown；`current_output_id`/`phase=draft_ready`；未核验不标完成。
- 核验：`cta review` 在独立上下文调用评估模型（3 请求/2 回读上限），意见含版本、位置、要求/证据、severity（must_fix/optional/needs_evidence）；多调用拒绝后反馈继续；结果写 `reviews`，阶段 `reviewed`。
- 保存：`cta save` 检查目标产物版本、核验适用且 pass/full、无 must_fix、无关键阻塞、预算与模拟截止；启动 saving 尝试后写 `exports/<task>/`，逐文件 sha256 核实并写 `saves`，任务 `complete`。重复保存复用记录；失败写 `save_failed` 与失败尝试。
- 导出：方案 Markdown/JSON、执行报告、JSONL Trace、简报 JSON 均从 SQLite 同一权威生成；报告含创作策略与改进理由、成本口径、计划、步骤、工具结果、决策依据、账本、真实/模拟时间、核验、保存与停止缺口；Trace 含 estimate / strategy_selection / stop 事件。
- 计量：业务工具启动时写 `attempts`（running + 固定费用/耗时）与 `ledger`（nature=incurred），并推进 `tasks.sim_elapsed_ms`；执行前检查预算与模拟截止，生成前另查完整剩余路径；拒绝不计费。真实模型请求逐次写 `llm_requests`（用量、缓存命中/未命中、耗时、错误、mock 标记），未知用量为 NULL 并单列计数。
- 简报：`briefs` 按版本保存；事实必须来自本任务材料且 quote 可在正文找到、scope 非空；失败校验为工具拒绝，不保存。
- 续跑（05）：`run` 执行、`resume` 显式续跑、`pause` 暂停；真实新进程先核实 pending 工具调用、running 尝试与未核实 save，再追加有依据结果或 `interrupted`/`unknown`，保持 assistant/tool 合法配对。
- 恢复事实：`messages` 中追加 `meta.kind=recovery_fact` 的 `[恢复事实]` 用户消息与当前快照；`inspect` 暴露 `recoveries`/`snapshots`/`material_snapshots`/`todos`。有效检索/草稿/评审直接复用，生成只有完整有效候选才可恢复复用；不完整输出仅留追溯。
- 计量与额度：恢复不新建尝试、不重复收费；只有模型/用户实际重试才新增尝试。暂停不推进模拟时钟；触顶/过期后续跑不获得新额度。
- 资料与知识：`material_snapshots` 在首次使用时冻结全部任务材料正文；生成与回读使用任务快照而非当前外部文件；知识正文继续从 `knowledge_loads` 恢复，不重读外部文件。
- 保存未知（05 边界）：恢复发现同版本未核实 save 时标记 `unknown`、`phase=save_unknown`、状态 `incomplete` 并安全停止；`save` 对未知记录拒绝重放。完整回执核实与导出修复留 08。
- 测试 seam：CLI 公开输出；跨进程用 `python -m cta` + 真实 SQLite；测试专用 `CTA_FAULT_CRASH` 在系统边界让子进程中断，`CTA_MATERIALS_PATH`/`CTA_KNOWLEDGE_DIR` 只用于验证外部文件变化不被静默替换。

## 扩展约定

1. 新表用 `CREATE TABLE IF NOT EXISTS`；`tasks` 新列加进 `db._ensure_columns` 并提升 `SCHEMA_VERSION`。旧库必须能打开。
2. 所有模拟费用必须同时写 `attempts` 和 `ledger`；预估/预留不得写成 incurred；实际重试新增尝试，旧消耗不回退。
3. 每次模型请求写 `llm_requests`；purpose 区分主循环/生成/核验；usage 可得字段原样保存；不写密钥或内部思维。
4. 每轮只执行一个已校验动作；工具返回的失败/拒绝是反馈，不得改写为成功。
5. append-only：系统规则、工具定义稳定；知识、证据、反馈、快照追加。任务外知识变化不得静默替换已加载正文。
6. 任何“完成”必须同时满足：产物版本、核验适用、资源与截止、保存记录与文件核实一致。
7. 04 起在 `review.py` 增加独立上下文评估；评估只读本任务材料，限 3 次请求/2 次回读；不得改稿、保存或扩大额度。
8. 05 起恢复需先核对未完成调用与保存记录；只重跑未完成有界步骤，不重复计量。08 处理保存未知与导出修复。
9. 07 起策略表与费用表分别版本化（`CTA_STRATEGY_VERSION` / `CTA_PRICING_VERSION`）；估算、预留、实际消耗是不同性质，只有 `ledger.nature='incurred'` 参与预算；未知与不适用不得合并。
10. 停止必须有具体缺口（`stops.gaps`）；程序停止与模型停止都保留既有成功成果、尝试和消耗，不因停止清除历史。
