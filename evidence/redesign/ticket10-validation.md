# 工单 10 确定性验证记录

本记录仅包含 Mock 和本地 HTTP 假对端的验证，不是 DeepSeek 真实调用或内容语义质量证据。原始输出按执行顺序保留如下；日志不包含任务数据或凭据。

- `ticket10-full-before-request-refinement.log`：`PYTHONPATH=src python -m pytest -p no:cacheprovider --basetemp .scratch/test-10-full-v2 -q --tb=short`，287 项通过，进程退出码 0。执行时是集成 `2252b61` 加工作区中的上下文实现、v2 价目及旧实验隔离；该状态当时尚未提交，不能将本次结果归属于某个精确提交。随后仅精确化了 Mock 与真实 SDK 的请求记录及对应测试，本次全量结果不冒充最终 HEAD 的完整回归。
- `ticket10-final-targeted.log`：在代码提交 `71fcd03` 上执行 `PYTHONPATH=src python -m pytest -p no:cacheprovider --basetemp .scratch/test-10-evidence-final tests/test_context_composition.py tests/test_legacy_experiment_entry.py tests/test_model_direct.py tests/test_protocol_freeze.py tests/test_task_pricing.py tests/test_strategy.py tests/test_comparison.py -q --tb=short`，33 项通过，进程退出码 0。包含本地假对端接收与实际 SDK 入参逐字段核对；SDK 测试不是对外真实模型请求。
- `examples/context-redesign/mock-request-roundtrip.json` 是同一新请求接口下，公开 CLI 创建、运行、inspect 得到的两轮 Mock 样例，含 13 项完整工具和真实调用/结果配对；不含密钥。

最终集成提交仍需合并后进行适当检查；真实模型、新旧对照和语义质量由工单 11 单独完成并留证。
