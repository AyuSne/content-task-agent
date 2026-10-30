# 需求与题面覆盖

## 用户与交付目标

本项目选择第 2 题：内容任务规划 Agent。运营输入主题、预算、截止时间，以及受众和创作要求；Agent 使用合成产品资料和素材，在资源约束内准备并保存内容活动方案。

方案包括创作策略、内容安排、主视频脚本、素材用途、制作步骤和待确认项。执行报告说明任务计划、实际步骤、工具结果、分类费用和保存情况；无法完成时说明原因并保留已有成果。

运行入口为 CLI。预算按模拟工具费核对，截止按模拟业务时钟核对，真实模型开销和后续制作投放费用单列。

## 题面要求与材料对应

| 题面要求 | 实现和评审入口 |
| --- | --- |
| 主题、预算、截止；缺少必要输入时明确处理 | `create/update`；[缺预算](../examples/submission/offline/missing-budget/result.json)、[缺截止](../examples/submission/offline/missing-deadline/result.json) |
| 合成素材、费用和耗时表 | `data/materials/`、`data/pricing/` |
| 素材检索、脚本生成、成本估算、方案保存 | `search_materials`、主 Agent 创作并通过 `submit_script` 提交、`estimate_cost`、`save_campaign` |
| 任务计划、步骤状态、工具结果、费用、方案或未完成原因 | [真实正常任务报告](../examples/submission/live-normal/execution-report.md)及各场景结果 |
| 模型选择动作、观察结果、调整或结束 | [正常任务 Trace](../examples/submission/live-normal/trace.jsonl)、[预算停止 Trace](../examples/submission/live-budget-stop/trace.jsonl) |
| 正常完成 | [30秒校园推广方案](../examples/submission/live-normal/campaign-readable.md) |
| 工具失败，保留成功结果并调整 | [真实保存中断恢复](../examples/submission/live-save-recovery/summary.json)、[离线检索失败恢复](../examples/submission/offline/tool-failure-recovery/result.json) |
| 预算超限 | [真实预算不足停止](../examples/submission/live-budget-stop/summary.json)、离线正常场景的两次路径估算 |
| 重复执行 | [重复创建与运行结果](../examples/submission/offline/normal/repeat.json) |
| 有执行上限，失败步骤可续跑 | 任务内持久轮数；`run/resume` 与中断恢复测试 |
| 至少三组输入、实际输出和 Trace | [提交样例索引](../examples/submission/README.md) |
| 环境、设计、评估、AI使用、复盘和依赖配置 | README、本目录文档、`pyproject.toml`、`uv.lock`、`.env.example` |

## 成功标准

- 方案回应当前要求，主张有材料依据，素材和制作步骤清楚。
- 工具实际结果、累计费用、模拟进度和版本可以相互核对。
- 当前方案经过完整核验与意见处置，保存文件已核实。
- 输入不足、资源不足或达到执行上限时，返回明确状态和缺口。
- 续跑延续已有成果、消耗和额度，重复请求保持任务身份和费用一致。

主 Agent 直接创作，独立上下文核验内容，程序核对执行事实。调用流程和设计取舍见[方案与设计](02-solution.md)。
