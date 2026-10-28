# 02 验收证据

日期：2026-10-29。实现：票 02（资料检索、业务简报与关键缺口）。

## red → green

在 01 基线提交 `e028a47`（尚无主循环、知识、材料与计量）上运行本票同一组 CLI 验收测试：

```text
$ py -m pytest tests/test_brief_loop.py
FFFFFFFFFFFFFFFFF                                                        [100%]
17 failed
```

失败原因均为 `cta run` 不存在、无 `inspect`、无知识/材料/计量行为，不是语法或环境错误（见 `red.txt`）。实现后：

```text
$ uv run pytest -q
..................................                                       [100%]
34 passed
$ uv run ruff check src tests
All checks passed!
```

## 确定性运行（Mock 注入）

`generate_scripted.py` 可复现：创建任务 → 注入“基础检索 + 回读 M-001 + 提交简报” → `cta run` → `cta inspect`。

- `01-create.json`：合法输入，`in_progress`。
- `02-run.json`：3 轮，简报版本 1，阶段 `brief_ready`，模拟费用 20 分，模拟进度 +2 s。
- `03-inspect.json`：实际加载知识 `campaign-creation 2026-10-29.v1`（sha256 64 位）、连续消息、工具结果、1 次工具尝试（成功）、1 条账本、3 次 Mock 模型请求；`real_requests=0`、`mock_requests=3`。
- `script.json`：注入内容，只在测试/离线演示使用，不是产品对外能力。

## 真实模型最小接入核对（已执行）

用同一产品代码、真实 DeepSeek（`deepseek-flash`、非思考、非流式、SDK 重试 0），任务 `t_fff593487db6`：

- 输入：主题“校园社团招新季推广”、预算 10 元、截止 2026-10-30 12:00 +08、受众 18-24 岁大学生、要求突出自动卡点。
- 实际动作（7 轮）：5 次资料检索（3 次扩展、2 次基础；其中 2 次无结果后改词重查）→ 1 次完整路径估算 → 提交简报。
- 结果：`brief_ready`；简报含 6 条带原文片段/适用范围/用途的事实、建议/假设/未知分开、2 个关键阻塞未知；模拟费用 220 分；累计真实用量 30,776 输入 / 2,865 输出 tokens，缓存命中 25,088、未命中 5,688，采得 API 耗时 19,805 ms。
- 证据：`04-real-create.json`、`05-real-run.json`、`06-real-trace.json`（消息、工具结果、尝试、账本、逐请求用量，无密钥）。

该轨迹已显示依据真实工具反馈调整检索词的行为；它只证明本次接入与流程，不证明统计稳定性或生产质量。

## 验收对照

| 验收点 | 证据 |
| --- | --- |
| openai SDK + DeepSeek 地址/密钥、deepseek-flash、非思考非流式、重试关闭 | `model_config` 输出；`04/05-real-*`；真实请求成功 |
| 主循环最多 12 轮、每轮一个动作、失败计数、触顶未完成 | `test_round_cap_stops_incomplete`、`test_model_error_*`、`test_multiple_tool_calls_*` |
| 参数/协议校验、多调用/未知工具/非法参数/截断不执行多动作 | 对应 4 个测试与 `03-inspect.json` 中的拒绝结果 |
| 版本化资料检索、片段/来源/范围/缺口、按需回读 | `test_material_with_only_an_id_*`、`test_material_instructions_*`、真实轨迹 |
| 简报四类信息、来源关联、可查先查、集中澄清 | `test_scripted_main_loop_*`、`test_clarification_*`、真实简报 |
| 知识首轮加载、实际正文留存、稳定前缀、追加 | `03-inspect.json` 的 knowledge/messages；`test_brief_ready_is_idempotent_*` |
| 计量：启动即计费、失败保留、拒绝不计、预估不记账、API 用量另记 | attempts/ledger 测试；`test_estimate_*`；真实轨迹用量 |
| 完整路径估算含生成/核验/保存（可含返修） | `test_estimate_covers_full_remaining_path_without_charging` |
| CLI 可查简报/计划/知识/消息/工具反馈/消耗/缺口 | `cta status`、`cta inspect`、`03-inspect.json` |
| Mock 只验证流程、真实失败不伪降级 | `mode_notice`、`mock_requests`、真实模式独立数据库 |
| 信息足够/无结果/依据不足/授权边界/响应异常/上限 | 17 个测试覆盖；真实轨迹含 2 次无结果与改词 |

## 已知限制

- 本票只到简报阶段；完整草稿、核验与保存分别在 03、04 实现。
- 真实运行目前 1 次最小核对；三类核心各三次由 12 收集，不在本票宣称稳定性。
- `test_round_cap` 使用 12 次无动作响应证明轮数上限，不代表真实模型行为。
