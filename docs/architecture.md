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
  materials.py   合成资料检索与有界原文回读
  knowledge.py   版本化工作知识加载（正文 + sha256）
  review.py      (04+) 独立上下文有界评估
  export.py      (04+) Markdown/JSON/Trace 导出
```

- 一票一个 worktree/分支；只改本票范围与自己的报告。共享运行状态、票面、进度索引由主控维护。
- 不新增第二套权威状态：SQLite 是唯一权威，Markdown/JSON/Trace 都从它导出。
- 不引入 Web、插件平台、通用多 Agent 框架；模型调用是系统边界，用 `ModelClient` 协议注入。
- 资料正文是数据不是指令；系统提示明确此边界。业务工具不能扩大用户授权。

## 已落地事实（01-02）

- CLI：`cta [--mode mock|real] [--data-dir DIR] [--format json|text] create|status|run|inspect|list`。
- 数据目录：`<repo>/.cta-runs/<mode>/tasks.db` 与 `<mode>/exports/`；`CTA_DATA_DIR` 覆盖根目录；真实/Mock 完全分离。
- 模拟起点：`CTA_SIM_START` 或默认 `2026-10-29T00:00:00+08:00`；截止必须晚于它。
- 输入：主题/预算（元，两位小数）/截止必填；受众、创作要求可选。缺失/无效仍持久化，状态 `needs_input`，逐字段错误与解释在 `validation`。
- 身份：`(mode, 规范化字段)` 的 SHA-256 指纹；无效字段按稳定错误码参与。重复 `create` 返回原任务；`--new` 独立。
- 状态：`needs_input | in_progress | incomplete | complete`；阶段 `created | brief_ready | draft_ready | stopped`。完成只能由 04 的核验+保存产生。
- 主循环：每轮 `used_rounds += 1` 再调用模型；只接受 0/1 个工具调用；多调用、未知工具、非法参数、截断、无动作都拒绝并反馈。模型错误或 12 轮触顶 → `incomplete`，保留消息、尝试、账本。
- 模型：真实模式 `DeepSeekClient`（openai SDK，DeepSeek base URL，`deepseek-flash`，`thinking.disabled`，非流式，`max_retries=0`）；Mock 模式 `ScriptedClient`（`CTA_MODEL_SCRIPT`，按 `channel` 取脚本），仅测试/离线。
- 上下文：系统规则 + 工具定义 + 知识正文 + 任务输入 + 状态快照 + 追加消息。首轮加载 `campaign-creation` 知识并把正文、版本、sha256 存入 `knowledge_loads`；无变化不重复快照。`brief_ready` 后追加一次阶段推进消息。
- 工具：`search_materials`（basic 20 分/2 s；extended 60 分/6 s）、`read_material`（0）、`estimate_cost`（0，含预计完成时刻与预算/时间缺口）、`propose_brief`、`generate_script`（direct 120 分/8 s；multi 300 分/20 s，最多 3 个内部候选）、`propose_campaign`、`request_clarification`、`stop_incomplete`。
- 脚本生成：独立上下文（创作知识 + 简报 + 策略 + 事实/素材），不继承主循环历史；请求写 `llm_requests`（purpose=`script_generation`，meta.context=`isolated_generation`），消息写 `messages.agent='generator'`；候选校验事实 quote 与证据引用。
- 产物：`outputs` 按版本保存完整方案 JSON 与 Markdown；`current_output_id`/`phase=draft_ready`；未核验不标完成。
- 计量：业务工具启动时写 `attempts`（running + 固定费用/耗时）与 `ledger`（nature=incurred），并推进 `tasks.sim_elapsed_ms`；执行前检查预算与模拟截止，生成前另查完整剩余路径；拒绝不计费。真实模型请求逐次写 `llm_requests`（用量、缓存命中/未命中、耗时、错误、mock 标记），未知用量为 NULL 并单列计数。
- 简报：`briefs` 按版本保存；事实必须来自本任务材料且 quote 可在正文找到、scope 非空；失败校验为工具拒绝，不保存。
- 测试 seam：CLI 公开输出；跨进程用 `python -m cta` + 真实 SQLite。测试不直接查库断言行为；Mock 只注入模型边界。

## 扩展约定

1. 新表用 `CREATE TABLE IF NOT EXISTS`；`tasks` 新列加进 `db._ensure_columns` 并提升 `SCHEMA_VERSION`。旧库必须能打开。
2. 所有模拟费用必须同时写 `attempts` 和 `ledger`；预估/预留不得写成 incurred；实际重试新增尝试，旧消耗不回退。
3. 每次模型请求写 `llm_requests`；purpose 区分主循环/生成/核验；usage 可得字段原样保存；不写密钥或内部思维。
4. 每轮只执行一个已校验动作；工具返回的失败/拒绝是反馈，不得改写为成功。
5. append-only：系统规则、工具定义稳定；知识、证据、反馈、快照追加。任务外知识变化不得静默替换已加载正文。
6. 任何“完成”必须同时满足：产物版本、核验适用、资源与截止、保存记录与文件核实一致。
7. 04 起在 `review.py` 增加独立上下文评估；评估只读本任务材料，限 3 次请求/2 次回读；不得改稿、保存或扩大额度。
8. 05 起恢复需先核对未完成调用与保存记录；只重跑未完成有界步骤，不重复计量。08 处理保存未知与导出修复。
