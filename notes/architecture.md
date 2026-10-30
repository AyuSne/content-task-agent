# 实现架构

本项目以 Python 原生主循环、结构化工具和 SQLite 实现内容任务规划。当前入口是[README](../README.md)，交付证据集中在[提交样例](../examples/submission/README.md)。

## 从输入到交付

`create → run/resume → 查证与创作 → 提交完整方案 → 内容核验及必要返修 → 核实保存`

下一步由模型根据工具结果选择。用户缺口、资源不足和执行上限都有明确的停止状态。

## Module 与 interface

- CLI 是公开任务 interface，`run/resume` 承接完整推进过程，`TaskService` 提供创建、查询及显式修改。
- `loop.py` 组织主 Agent 的单动作回合。`model.py` 在真实模型与脚本 Mock 这个实际变化位置提供 adapter。
- `artifacts.py` 接收完整脚本和简报，`campaign.py` 引用脚本组装方案，`task_plan.py` 记录工作安排。
- `evaluation.evaluate` 集中内容核验的请求协议、只读权限和执行上限；`review.py` 连接当前方案、材料与持久状态。
- `save_preconditions` 集中内容与版本准入；`save.py` 处理资源、保存和核实，`export.py` 生成阅读视图，`recovery.py` 核对中断结果。
- `materials.py` 与 `knowledge.py` 管理资料和 Skill 采用，`db.py` 与 `repository.py` 保存任务权威记录。

工具是面向模型的 interface；实现内部集中计量、版本和状态规则。测试从公开 CLI、产物、账本和报告进入，在模型、文件与进程交互处注入变化。

## 输入与状态

主请求包含稳定系统规则、13 项原生工具定义、创作 Skill、费用概览、当前输入与实际历史。条件性资料通过工具读取，变化追加到后续消息。

输入、简报、脚本、方案、核验及保存有明确关联。新任务默认 24 轮，每轮一个动作；恢复延续创建时的上限。核验使用独立上下文和有限请求/回读额度。

## 费用与副作用

估算、执行和保存按任务采用的费用表计量。业务尝试和模型请求在执行前登记，结果回写原记录。中断保留消耗与未知项，恢复先核实副作用。

模拟工具费按人民币分记录，实际模型请求与 token 单列。全部交付文件核实后记录完成；人工稿通过显式导入形成待核验版本，导出修复保留明确操作记录。

## 维护入口

- 工具与模型输入：`prompts.py`、`loop.py`、`protocol.py`。
- 核验与处置：`evaluation.py`、`review.py`、`dispositions.py`。
- 恢复与文件一致性：`recovery.py`、`save.py`、`export.py`。
- 行为验证：[测试说明](../docs/03-evaluation.md)及 `tests/`。

[构建思路](agent-design-rationale.md)说明设计原因，历史讨论与阶段报告保留原位。
