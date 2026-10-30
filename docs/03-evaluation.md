# 评估与题面验收

## 本轮检查

本轮核对目录调整后的程序、离线入口和提交样例，检查记录保留在 `examples/submission/validation/`。

| 检查 | 结果 | 原始输出 |
| --- | --- | --- |
| pytest | 343 项通过 | [最新回归输出](../examples/submission/validation/layout-2026-10-31/pytest.txt) |
| Ruff | 通过，范围含源码、测试、脚本和离线入口 | [ruff.txt](../examples/submission/validation/layout-2026-10-31/ruff.txt) |
| compileall | 通过 | [compileall.txt](../examples/submission/validation/layout-2026-10-31/compileall.txt) |
| 提交文件核对 | 85 份原样归档文件哈希一致，390 条 Trace 可解析 | [artifact-check.txt](../examples/submission/validation/layout-2026-10-31/artifact-check.txt) |
| 当前协议离线演示 | 六组场景达到预期，真实模型请求 0 次 | [最新复现输出](../examples/submission/validation/layout-2026-10-31/offline-check.txt) |

## 三组真实运行

| 场景 | 实际结果 | 主轮次 | 真实请求 | 模拟工具费 |
| --- | --- | ---: | ---: | ---: |
| 校园30秒推广方案 | 核验后返修，当前稿核实保存 | 17 | 20 | 4.70 元 |
| 学习打卡方案保存中断 | 保存生效后进程中断，续跑核实交付，未重复保存尝试 | 14 | 15 | 2.50 元 |
| 0.50元预算准备完整方案 | 查证后判断剩余预算不足，明确未完成并保留消耗记录 | 6 | 6 | 0.20 元 |

输入、实际输出、方案和 Trace 见[样例索引](../examples/submission/README.md)。真实记录保留目录调整前的原始文件，具体源码版本写在清单中；调整后的资料读取和同类行为由本轮回归与离线演示复核。

正常任务体现“工具反馈 → 修正提交 → 核验返修 → 再核验 → 保存”；预算场景体现“查证与估算 → 无可行路径 → 停止”。恢复场景展示跨进程保留成功副作用和已有费用。

## 题面覆盖

| 验收点 | 样例与测试 |
| --- | --- |
| 正常完成 | 真实正常任务；离线 `normal`；`test_campaign_delivery_new.py` |
| 缺预算或截止 | 离线 `missing-budget`、`missing-deadline`；`test_task_intake.py` |
| 工具失败 | 真实保存故障恢复；离线检索失败恢复；`test_new_recovery.py` |
| 预算超限 | 真实预算停止；离线两条路径估算及 `budget-stop`；`test_strategy.py` |
| 重复执行 | 离线 `repeat.json`；`test_task_intake.py`、`test_autonomous_delivery.py` |
| 失败续跑 | 保存故障后核实，检索失败后继续；恢复相关测试 |
| 截止与执行上限 | 离线 `deadline-stop`；`test_runtime_limits.py`、`test_tool_guidance.py` |

离线正常场景先得到 3.90 元的不可行路径估算，再改用 2.10 元的剩余路径，最终含已发生检索费共 2.70 元。该场景使用预设模型响应，便于稳定复现协议、计量和保存行为。

## 内容检查与证据口径

内容核验由独立上下文中的模型执行，程序核对版本、资源和交付文件。范围表达等语义问题仍需要运营审阅；方案用于合成业务场景，实际制作和投放效果尚无数据。

原始参数错误、核验意见和返修保留在所选 Trace 中。历次运行与校准保持原位，代码审查范围见[审查记录](../notes/delivery/07-final-review.md)。
