# 实现架构（Agent 入口）

本文件是后续实现 Agent 的共享上下文指针：先读它，再读对应票与规格条款。详细需求以 `D:/AAA小影科技/.scratch/content-task-agent-build/spec.md` 为准；本文件只记录已经落地的组织方式与扩展点，不复制规格。

## 分层与写入边界

```text
src/cta/
  cli.py        CLI 解析、输出 json/text、退出码
  config.py     .env 加载、模式/数据目录、模拟起点、密钥脱敏
  identity.py   输入规范化、校验、请求指纹（不做语义相似）
  db.py         SQLite schema/事务/连接
  service.py    任务用例：创建、查询、列表；后续 update/resume/import
  loop.py       (02+) 单一主循环：模型动作 -> 工具 -> 反馈 -> 下一轮
  tools.py      (02+) 业务工具：检索、生成、估算、核验入口、保存
  llm.py        (02+) openai SDK / DeepSeek 适配与用量记录
  review.py     (04+) 独立上下文有界评估
  knowledge.py  (02+) 版本化工作知识加载与留存
  materials.py  (02+) 合成资料检索与原文回读
  export.py     (04+) Markdown/JSON/Trace 导出
```

- 一票一个 worktree/分支；只改本票范围与自己的报告。共享运行状态、票面、进度索引由主控维护。
- 不新增第二套权威状态：SQLite 是唯一权威，Markdown/JSON/Trace 都从它导出。
- 不引入 Web、插件平台、通用多 Agent 框架；模型调用是系统边界，可用 `ModelClient` 协议注入。

## 已落地事实（01）

- CLI：`cta [--mode mock|real] [--data-dir DIR] [--format json|text] create|status|list`。
- 数据目录：`<repo>/.cta-runs/<mode>/tasks.db`；`CTA_DATA_DIR` 覆盖根目录；真实/Mock 完全分离。
- 模拟起点：`CTA_SIM_START` 或默认 `2026-10-29T00:00:00+08:00`；截止时间必须晚于它。
- 输入：主题/预算（元，两位小数）/截止时间必填；受众、创作要求可选。缺失/无效仍持久化任务，状态 `needs_input`，逐字段错误在 `validation.errors`。
- 身份：`(mode, 规范化字段)` 的 SHA-256 指纹；`dedupe_key` 唯一。重复 `create` 返回原任务且 `duplicate=true`；`--new` 的 `dedupe_key` 为 NULL，获得独立 ID。
- 状态：`needs_input | in_progress | incomplete | complete`；`progress.used_rounds`、`sim_elapsed_ms`、`paused`；`resources.sim_spent_cents` 从账本累计。
- 密钥：只从项目 `.env` 或进程环境读取；公开视图只有 `api_key: set|missing`；`Config.redact` 用于错误文本。
- 测试 seam：CLI 公开输出；跨进程用 `python -m cta` + 真实 SQLite。测试不直接查库断言行为。

## 扩展约定

1. 新表用 `CREATE TABLE IF NOT EXISTS` 并提升 `db.SCHEMA_VERSION`；写迁移/初始化时保持旧库可打开。票 02 起新增消息、尝试、账本、产物、评审、保存表。
2. 所有模拟费用必须同时写 `attempts`（启动与固定计量）和 `ledger`（nature=`incurred`）；预估/预留写 `nature in ('estimate','reserve')`，不计入已发生。
3. 每次模型请求写 `llm_requests`：purpose、model、status、usage（可得字段原样）、cache hit/miss、真实起止、错误；不写密钥。
4. 主循环每轮只接受一个已校验动作；多调用/未知工具/非法参数/截断 → 拒绝、计数、反馈，不执行多个业务动作。
5. append-only：系统规则、工具定义稳定；知识正文、证据、反馈、快照追加。知识加载记录版本+sha256+实际正文。
6. 任何“完成”必须同时满足：产物版本、核验适用、资源与截止、保存记录与文件核实一致。
7. 失败/中断保留调用与消耗；恢复只重跑未完成有界步骤。
