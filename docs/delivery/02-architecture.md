# 架构（交付文档 02）

详细事实见 [`docs/architecture.md`](../architecture.md)。本页给出交付级视图。

## 分层

```text
CLI (src/cta/cli.py)
  └─ use cases: service / loop / review / save / export / importer / calibration / recovery
       ├─ model boundary: model.py (DeepSeekClient | ScriptedClient)
       ├─ knowledge & materials: versioned fixtures, frozen snapshots
       ├─ pricing & strategy: versioned tables, estimates/reservations
       └─ SQLite: db.py + repository.py (authoritative state)
```

## 关键模块

| 模块 | 职责 |
| --- | --- |
| `service.py` | 创建/查询/更新输入、知识/资料采用、视图、计划与缺口 |
| `loop.py` | 12 轮主循环、工具校验、简报/草稿、策略、生成、停止 |
| `review.py` | 独立评估器、有界请求/回读、报告校验、反馈入消息 |
| `save.py` | 前置条件、未知保存、文件核实、`reconcile` 修复 |
| `export.py` | Markdown/JSON/报告/Trace、版本元数据、哈希核验 |
| `importer.py` | 人工 Markdown 解析、基稿/语义冲突、待核验新稿 |
| `recovery.py` | 跨进程恢复、未完成调用、未知保存、暂停/额度 |
| `strategy.py` | 方向表、完整剩余路径、预留与模式表 |
| `calibration.py` | 六对校准、指标、标签隐藏 |
| `db.py` / `repository.py` | schema v6、迁移、事务、查询投影 |

## 数据与状态

- 表：`tasks`、`task_inputs`、`messages`、`attempts`、`ledger`、`briefs`、`outputs`、`reviews`、`dispositions`、`saves`、`imports`、`knowledge_loads`、`material_snapshots`、`snapshots`、`estimates`、`strategy_selections`、`stops`、`llm_requests`。
- 任务状态：`needs_input | in_progress | incomplete | complete`；阶段：`created | brief_ready | draft_ready | reviewed | complete | review_incomplete | save_failed | save_unknown | stopped | input_updated`。
- 完成条件：核验 `complete` 且 `pass`、无 must_fix/证据缺口/关键阻塞、输入版本一致、保存文件逐哈希核实。
- 预算/截止在每次工具动作前 guard；失败/拒绝不写账本，启动的尝试保留成本。

## 可靠性

- 单轮单工具；非法/多调用/截断/非对象参数拒绝并继续。
- 保存：先写保存记录（`unknown`），文件核实后置 `verified`，最后完成任务；崩溃后 `reconcile` 从权威内容修复。
- 恢复：核实未完成调用和运行中尝试；补证据或标 `interrupted`/`unknown`，不伪造结果。
- 输入更新：新 `task_inputs` 版本；语义更新使旧产物/评审不适用；资源更新复用。

## 扩展点

- 新模型：实现 `ModelClient` Protocol；真实/Mock 权限与数据目录隔离。
- 新价格/策略/知识/资料版本：版本化文件 + 环境变量指向。
- 新输入/工具：在 `identity`/`prompts`/`loop` 加校验与工具定义，保持单轮单工具。

## 已知架构限制

- 人工稿解析依赖固定章节；不做自动合并。
- 真实金额无可靠单价，保持 `unknown`；模拟费用与真实用量分开。
- 12 轮上限对真实模型是硬瓶颈，本交付未放宽。
