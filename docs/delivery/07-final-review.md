# 最终双轴评审（交付文档 07）

固定点：`71e87f9`（BASE）；评审对象：`integration` 截至交付 HEAD。评审按 `code-review` skill 分两轴独立进行，以下保留两轴原始结论与处置，不合并、不重排。

## Standards（原文摘要）

**HARD**
1. `save.py` `_repair_files` 先写 `complete` 再核实文件；核实失败仍可 complete（违反完成不变式）。
2. `export.py` 执行报告自算 plan，不是 SQLite 投影；与 `repository.plan_view`、`service._plan_view` 三份实现。
3. `service._plan_view` 硬编码版本且漏 `save_unknown`，与 `todos` 不一致。
4. `export --repair` 丢弃 `reconcile_task` 的 `version_mismatch`，仍导出并 exit 0。
5. `calibration.py` 记录字面知识版本，不是实际加载版本。
6. 错误信封 `ok/error/code` 不一致；CLI 把 review 具体 code 压成 `review_blocked`。
7. README/证据索引链接不存在的 `07-final-review.md`；README CLI 列表过期、重复 resume/pause；architecture 命令/模块列表过期。
8. `pytest -q` 曾被报告失败（resume 测试）。

**SMELLS（判断）**
- `calibration._evaluate` 复制 review 请求循环；`_guard`/deadline 计算多处重复；`ResourceState` 未复用；phase 集合重复；quote-in-material 检查 3 份；私有跨模块 import；字符串字面量重复；CLI if/elif 与工具 switch 两处扩展；死代码/单调用 hook；CLI 从 status 重建 RawInput；`conn/task` 缺类型。

**处置**
- #1 已修：先构建并核实文件，全部通过后才写 complete；失败保持 unknown/in_progress。
- #2 已修：报告 plan 改用 `repository.plan_view`。
- #3 已修：service 删除 `_plan_view`，统一用 `repository.plan_view`（含 `save_unknown`）。
- #4 已修：`export --repair` 遇 version_mismatch/save_error 输出并返回 4。
- #5 已修：记录实际 `knowledge_doc.version`。
- #6 部分修：`ReviewError` 带 code（no_output/model_unavailable/guard code），CLI 保留；loop/save 的 `ok/error/code` 信封仍是既有风格，未全量统一（残余）。
- #7 已修：本文件补齐；README CLI 列表补全并去重；architecture 命令/阶段/版本更新。
- #8 已核：当前全量 **112 passed**，ruff 通过；原失败为合并期间旧断言，已修正。

**未解决（判断/残余）**：calibration/review 请求循环重复；`_guard`/deadline 计算三处；phase 字符串集合重复；私有 import；CLI 命令 cascade。未在本次交付内重构，不影响已测行为，但增加后续变更成本。

## Spec（原文摘要）

**最严重**
- 知识/资料采用后旧 `pass` 评审仍被缓存/用于保存，可能用未核验的新知识认证完成（违反 I-08/ticket 09）。

**其他**
- 真实运行未达 T-03 的“三类核心各三次”：0 次真实“评审通过→保存”，0 次真实失败/续跑；4/6 真实任务触 12 轮上限；缺真实工具失败样本。
- 复审 payload 不带 prior findings，无法“延续未解决问题/重新打开”。
- 人工稿导入后 `run`/`resume` 在 `draft_ready` 直接返回，主 Agent 未读取修改。
- 交付缺 AI usage 文档；`07-final-review.md` 缺失；`evidence/ticket-14` 未跟踪（合并后已入）。
- 11 首轮校准结果被覆盖，未保留全部历史。
- 12 轮硬编码/报告 `/12`；资源-only 更新不重估可行性。
- 模型工具集无核验/保存，I-03 的“模型选择核验/保存”由 operator CLI 承担（scope 偏差）。

**处置**
- 最严重已修：采用知识/资料及语义输入更新时把旧 complete 评审标记 `stale`；`save` 返回 `review_stale`；新增测试。`review_task` 缓存只认 `complete`，stale 会重新评审。
- 资源-only 更新已修：预算低于已花费或截止早于已用时间 → `incomplete/stopped`，新增测试。
- 12 轮上限：改为 `CTA_MAX_ROUNDS` 初始值（默认 12），报告用同一常量，视图记录版本；不再硬编码。
- AI usage 文档已补：`09-ai-usage.md`；最终评审文档即本文件；ticket-14 已提交。
- **未修/未达成**：真实运行未完成保存（12 已如实记录，未用 Mock 冒充）；真实失败/续跑未在真实 API 任务上执行；复审不带 prior findings；人工稿导入后主 Agent 的读取路径；11 首轮历史被覆盖；模型工具集不含核验/保存；错误信封全量统一。
- 这些未解决项是**已知缺口**，不是“通过”。交付不声称满足 T-03 的全部真实运行次数。

## 总结

- Standards：8 项 hard，其中 7 项已修/已核，1 项部分修（错误信封统一）；判断类 smell 多数保留。
- Spec：1 项最严重已修；真实运行/复审/人工稿/历史保留等仍未达成，已在 README、ticket-12/13/14 与 `08-retro.md` 明示。
- 无“通过”结论：交付范围是可运行系统 + 真实证据 + 明确限制，真实完成路径仍未证明。
