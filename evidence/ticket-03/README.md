# 03 验收证据

日期：2026-10-29。实现：票 03（生成完整活动方案草稿）。

## red → green

在 02 基线 `f5eabe1`（无草稿工具、无独立脚本生成、无产物版本）上运行本票 CLI 验收测试：

```text
$ py -m pytest tests/test_campaign_draft.py
FFFFFFF                                                                  [100%]
7 failed
```

失败原因是 `generate_script`/`propose_campaign` 不存在、没有 outputs、没有独立生成上下文，不是语法/环境错误（见 `red.txt`）。实现后：

```text
$ uv run pytest -q
47 passed
$ uv run ruff check src tests
All checks passed!
```

## 确定性运行（Mock 注入）

`generate_scripted.py` 可复现：创建 → 第一次 `run` 形成简报 → 第二次 `run` 生成并提交完整草稿 → `inspect`。

- `02-brief-run.json`：2 轮，`brief_ready`，费用 20 分。
- `03-draft-run.json`：2 轮（合计 4 轮），`draft_ready`，草稿 v1，费用 20+120=140 分，模拟进度 +10 s。
- `04-inspect.json`：独立生成调用 1 次（`purpose=script_generation`，`meta.context=isolated_generation`），主循环消息不含生成提示；生成器消息 3 条（系统/请求/响应）；产物含 3 段脚本、3 类素材状态、证据关联、制作步骤。
- `script.json`：注入内容，仅测试/离线演示。

## 真实模型运行（已执行）

任务 `t_708f84239aa0`，真实 DeepSeek、`deepseek-flash`、非思考、非流式、SDK 重试 0：

- 第一次 `run`：8 轮形成简报，`brief_ready`，模拟费用 80 分。
- 第二次 `run`：继续到 12 轮上限的最后一轮，生成脚本候选（`generate_multi`，3 个内部候选）并提交完整草稿；`draft_ready`，草稿 v1，模拟费用累计 440 分，模拟进度 +220 s。
- 草稿：`functional_demo` 策略，5 段 30 秒脚本，3 项素材（已有/待获取/待制作），5 条证据（M-001/M-002/M-004/M-005），4 个待确认项（2 个制作阻塞）。
- 真实用量：15 次请求，133,451 输入 / 15,835 输出 tokens，缓存命中 116,864、未命中 16,587，采得 API 耗时 118,221 ms；未知用量请求 0。
- 证据：`05-real-create.json`、`06-real-brief-run.json`、`07-real-draft-run.json`、`08-real-trace.json`。

真实运行恰好在 12 轮上限的最后一轮保存草稿；若再多一个必要动作会被明确记为未完成。该单次运行不证明统计稳定性。

## 验收对照

| 验收点 | 证据 |
| --- | --- |
| 主 Agent 组织目标/受众/主题/策略/内容安排/主脚本/素材/制作步骤/待确认 | `08-real-trace.output`、`04-inspect.outputs[0].content` |
| 脚本生成独立获得创作知识、简报、策略、事实、素材 | `generator_messages_count=12`（真实）/3（脚本）；`llm_requests.meta.context=isolated_generation`；主消息不含生成提示 |
| 关键事实保留主张—片段—来源—范围—用途 | 真实草稿 5 条 evidence；校验拒绝不可回溯 quote |
| 情境画面不冒充产品能力、建议/假设不冒充事实 | 知识规则、提示与真实草稿材料状态；`test_script_claim_without_supported_quote_*` |
| 生成前检查完整剩余路径 | `test_full_path_guard_rejects_generation_that_cannot_be_reviewed`；真实任务 440/1000 分 |
| 草稿作为明确产物版本保存 | `outputs` v1、`current_output`、`artifacts.campaign_versions` |
| CLI/候选视图可核对稿件、依据、缺口、工具结果、资源 | `cta status`、`cta inspect`、`04-inspect.json` |
| 完整草稿、素材缺失/无依据、生成失败、受控停止 | 7 个确定性测试 + 真实草稿 + 生成失败/无依据测试 |
| 失败不形成成功稿、消耗保留 | `test_generation_failure_*`、`test_script_claim_*`；尝试 failed、outputs 空 |

## 已知限制

- 草稿尚未核验、保存；`complete` 由 04 产生。草稿中的 2 个阻塞制作项必须在核验/保存前处置。
- 真实运行 1 次，且用满 12 轮；三类核心重复运行由 12 汇总。
- 多候选模式固定最多 3 次内部请求，按最长脚本选择；不按业务质量自动择优，最终取舍仍由主 Agent 与 04 核验负责。
- `evidence/ticket-03/extract_real.py` 记录了本次真实任务所在路径；重放需保留该运行目录或改路径。
