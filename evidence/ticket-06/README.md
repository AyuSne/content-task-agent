# 06 验收证据

日期：2026-10-29。实现：票 06（有界评估、意见处置与返修复审）。

## red → green

在 04 基线 `2309b38`（有核验/保存，但无意见处置、返修与复审）上运行本票 CLI 测试：

```text
$ py -m pytest tests/test_revision_disposition.py
FF.FF.                                                                   [100%]
4 failed, 2 passed
```

失败的 4 项是处置/返修/驳回路径不存在；回读上限与多调用拒绝已在 04 存在（见 `red.txt`）。实现后：

```text
$ uv run pytest -q
60 passed
$ uv run ruff check src tests
All checks passed!
```

## 确定性完整路径（Mock 注入）

`generate_scripted.py` 可复现：草稿 v1 → 核验 fail（F1 must_fix）→ accepted 处置 → 独立重新生成脚本 → 完整方案 v2 → 核验 pass → 核实保存 v2。

- `04-review-v1-fail.json`：v1 核验 fail，1 条 must_fix；费用 80。
- `05-revision.json`：处置 accepted 后返修；输出 v2；费用累计 140+80+120=340；旧稿与旧评审保留。
- `06-review-v2-pass.json`：v2 核验 pass；费用 420。
- `07-save.json`：`complete`，保存 v2；累计 430 分；5 个交付文件。
- `delivery/`：v2 方案、报告、Trace、简报。
- `08-inspect.json`：两条产物版本、两次核验、处置记录、两次生成请求、账本与保存。

覆盖：accepted 必须返修；rejected 需有理由且可解除 must_fix（测试）；accepted 未返修仍阻止保存；未知意见 ID 被拒；回读上限 2 次且第三次被拒；多调用拒绝后继续单动作核验并成功。

## 验收对照

| 验收点 | 证据 |
| --- | --- |
| 评估独立上下文、有限回读、次数/用量留痕 | 04 证据；`reviewer_messages`、`reviews.requests_used/readbacks_used` |
| 新增资料/澄清/改稿/保存交回主 Agent | 评估无搜索/保存工具；只读材料与报告 |
| 意见含版本、位置、要求/证据、severity | `04-review-v1-fail.json`；校验拒绝 pass+must_fix/needs_evidence/gap |
| 主 Agent 记录采纳/拒绝/待查证及依据 | `record_disposition`；`05-revision.json`、测试 |
| 返修获得当前稿/意见/知识/应保护内容，形成新产物版本 | 生成上下文 + `outputs` v2；`test_accepted_*` |
| 复审检查修改与整体影响，旧稿通过不继承 | 新 review 对应 v2；`review_stale` 测试（04） |
| 返修完整路径估算、内部读取/请求受限、计量延续 | 生成前 full-path guard；140+80+120+80+10 账本 |
| 评估中断/触顶/证据不足不算通过 | 04 real incomplete、readback limit 测试 |
| 经 CLI 覆盖无需回读、回读、缺口、越界、采纳/误报、返修/复审、中断 | 6 个 06 测试 + 04 测试 + 真实 fail 轨迹 |

## 已知限制

- 真实任务的返修未执行：`t_708f84239aa0` 已用满 12 轮，无法在同一任务继续主循环；真实返修记录由 12 的三类核心运行补充。06 的确定性证据证明机制，不证明真实模型返修语义质量（由 11 校准）。
- 处置记录是主 Agent 判断，不是客观真值；误报驳回必须有理由，但本票不评估理由的说服力。
- 评估中断后的“只重跑未完成有界步骤”依赖 05 的恢复实现；本票在中断时明确 incomplete。
