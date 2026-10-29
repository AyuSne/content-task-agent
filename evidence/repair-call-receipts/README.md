# 精确本地工具回执恢复

日期：2026-10-29。模式：Mock／脚本模型；无真实 API 调用。本证据只验证确定性恢复、计量和消息协议。

## 原问题与红测

从 `894677f` 开始，通过公开 CLI 先比较策略，再执行一次实际计费 20 分的基础检索，在同一 UTC 秒内提交完全相同的第二次比较并硬退出。固定的是外部时间读取，SQLite、CLI 以及退出均真实执行；测试不直接改数据库。

原代码在 `after_tool_request` 退出后，把旧比较误认成本次成功结果，回执的已发生费用为 0，实际账本已为 20。`after_tool_effect` 退出后的手工重建结果还缺少原返回的 `mode_table`。新增的两个比较回归在修复前分别以 `assert True is False` 和 `KeyError: 'mode_table'` 失败。

同类 `estimate_cost` 的独立红测也实际失败：执行前中断却恢复成成功；执行后中断却重新估算，返回新 `estimate_id`，新增估算记录。

## 修复及可复制检查

本地结果以任务、消息序号、工具调用 ID 和工具名称精确关联，完整原返回与数据库副作用在同一事务中提交。恢复不按时间匹配，不重新执行；无精确回执只追加 `local_action_interrupted`。

五种动作是 `compare_strategies`、`choose_strategy`、`estimate_cost`、`record_disposition` 和 `record_dispositions`。其中单项和批量处置均保留原工具接口，失败结果也保留其完整返回。

在项目根目录，使用项目 Python 环境执行：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
.venv/Scripts/python.exe -m pytest tests/test_local_action_recovery.py tests/test_strategy.py tests/test_review_save.py tests/test_process_resume.py --tb=short
.venv/Scripts/python.exe -m ruff check src/cta/loop.py src/cta/recovery.py src/cta/repository.py src/cta/db.py tests/test_local_action_recovery.py
```

`tests/test_local_action_recovery.py` 包含 23 项公开 CLI 检查：五种动作在请求后／效果后硬退出、同秒旧结果与资源变更、五种动作在本地写入后但回执写入前退出并整体回滚、不可行策略的原失败返回复用。检查恢复不新增费用、尝试或业务记录，保留已用轮数，并保持调用与结果配对合法。

实际结果：上述四文件共 **59 passed in 267.08s**；ruff 检查退出码 0（`All checks passed!`）；四个修改源文件的 `compileall` 通过。

## 历史库迁移

[`legacy-migration.json`](legacy-migration.json) 保留真实公开 CLI 的 create、旧版 inspect、新版 resume 和新版 inspect 输出。用 `git archive 894677f src fixtures knowledge pyproject.toml` 提取原版本，在独立数据目录运行其原生 CLI，实际创建 schema 7 数据库，并在比较已执行后 `os._exit(70)`。随后用修复版 CLI 对该库执行 `resume --max-rounds 0`；新表通过幂等 schema 初始化创建。

结果：旧估算仍保留一条，费用仍为 0，消息补为 `local_action_interrupted`。因为历史库没有本请求的精确回执，恢复没有把旧结果补称为已执行，没有重放动作，也没有新增估算或尝试。
