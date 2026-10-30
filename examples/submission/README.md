# 提交样例

建议先读[30秒校园推广方案](live-normal/campaign-readable.md)，再看[执行报告](live-normal/execution-report.md)。下面三组真实记录分别展示完成交付、保存故障恢复和预算不足停止。

## 真实运行

| 场景 | 结果 | 主轮次 | 真实模型请求 | 模拟工具费 |
| --- | --- | ---: | ---: | ---: |
| [校园30秒推广](live-normal/input.json) | 核验返修后保存 v2 | 17 | 20 | 4.70 元 |
| [学习打卡方案保存恢复](live-save-recovery/input.json) | 保存已生效后中断，恢复核实，无重复保存尝试 | 14 | 15 | 2.50 元 |
| [低预算任务](live-budget-stop/input.json) | 查证后停止，说明预算缺口 | 6 | 6 | 0.20 元 |

### 1. 正常交付与返修

- [输入](live-normal/input.json) · [结果及模型用量](live-normal/result.json)
- [方案阅读版](live-normal/campaign-readable.md) · [原始 Markdown](live-normal/campaign-v2.md) · [结构化方案](live-normal/campaign-v2.json)
- [执行报告](live-normal/execution-report.md) · [完整 Trace](live-normal/trace.jsonl)

这组任务由用户在当前版本实际运行。主 Agent 根据工具反馈调整提交，接受核验意见后提交新稿，v2 核验完成后直接保存。

### 2. 工具故障与恢复

- [输入](live-save-recovery/input.json) · [场景检查](live-save-recovery/summary.json) · [故障注入清单](live-save-recovery/manifest.json)
- [恢复响应](live-save-recovery/04-resume-0.json) · [完整查询](live-save-recovery/05-inspect.json)
- [保存方案](live-save-recovery/campaign-readable.md) · [执行报告](live-save-recovery/execution-report.md) · [Trace](live-save-recovery/trace.jsonl)

在保存文件已产生后注入进程中断，原进程退出码为 70。新进程恢复时核实既有结果，保留产物与消耗，保存业务尝试未重复。

### 3. 预算约束下正确停止

- [输入](live-budget-stop/input.json) · [实际运行响应](live-budget-stop/02-run.json) · [场景检查](live-budget-stop/summary.json)
- [执行报告](live-budget-stop/execution-report.md) · [Trace](live-budget-stop/trace.jsonl)

任务预算 0.50 元，检索发生 0.20 元工具费。模型判断剩余资源无法覆盖完整交付，返回未完成和具体原因。

## 当前协议离线复现

单行运行：

```powershell
uv run python examples/submission/run_offline.py
```

模型响应和核验结论由脚本预设，工具、数据库、费用、进程续跑及文件核实均使用当前代码。实际归档：

| 场景 | 预期与实际结果 | 记录 |
| --- | --- | --- |
| 正常任务与预算路径调整 | complete，模拟费 2.70 元 | [输入](offline/normal/input.json) · [结果](offline/normal/result.json) · [Trace](offline/normal/trace.jsonl) |
| 检索失败后续跑 | complete，保留失败尝试，模拟费 3.30 元 | [输入](offline/tool-failure-recovery/input.json) · [结果](offline/tool-failure-recovery/result.json) · [Trace](offline/tool-failure-recovery/trace.jsonl) |
| 缺预算 | needs_input，0 消耗 | [输入](offline/missing-budget/input.json) · [结果](offline/missing-budget/result.json) · [Trace](offline/missing-budget/trace.jsonl) |
| 缺截止 | needs_input，0 消耗 | [输入](offline/missing-deadline/input.json) · [结果](offline/missing-deadline/result.json) · [Trace](offline/missing-deadline/trace.jsonl) |
| 预算不足 | incomplete，说明资源缺口 | [输入](offline/budget-stop/input.json) · [结果](offline/budget-stop/result.json) · [Trace](offline/budget-stop/trace.jsonl) |
| 截止不足 | incomplete，说明资源缺口 | [输入](offline/deadline-stop/input.json) · [结果](offline/deadline-stop/result.json) · [Trace](offline/deadline-stop/trace.jsonl) |

正常和恢复场景另外核对[重复创建及运行](offline/normal/repeat.json)：任务 ID、模型请求数和累计费用保持不变。正常场景中，额外检索和返修路径估算 3.90 元，简化后的剩余路径为 2.10 元，最终含检索费共 2.70 元。

## 题面覆盖

正常完成看真实样例 1；缺预算/截止看离线缺输入；工具失败看真实样例 2 和离线恢复；预算超限看真实样例 3 及离线估算；重复执行看 `repeat.json`。两条以上真实路径的动作、参数、反馈和后续选择均保留在 Trace 中。

## 来源与核对

- 正常任务：源码 `1c266da`，任务 `t_fe6c563a4bfe`。
- 保存恢复：源码 `5a8920f`，任务 `t_ee3d81e66577`，归档保留该版本的原始输出。
- 预算停止：本轮较早的新协议源码 `52b79e8`，任务 `t_b6ad05466043`；当前代码的资源规则另由离线场景和回归覆盖。

原始命令响应、方案和 Trace 按字节归档，来源与 SHA-256 见[manifest.json](manifest.json)。`input.json` 和正常任务 `result.json` 为原始记录的索引投影，`campaign-readable.md` 只解码导出正文中的字符实体。

核对全部归档文件：

```powershell
uv run python examples/submission/verify.py
```

[本轮检查](validation/README.md) · [完整历史索引](../../notes/delivery/06-evidence-index.md)
