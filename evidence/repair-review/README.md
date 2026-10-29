# 核验反馈与校准修复

日期：2026-10-29（北京时间）。基线：`9d00fc9`。分支：`repair/review`。

本证据使用 CLI interface 和模型边界脚本注入，不发起真实 API 请求。生产核验、持久状态和意见处置均使用实际实现。

## Red → green

- `tests/test_calibration.py::test_calibration_keeps_incomplete_reports_out_of_completed_metrics`：12 个合法 `incomplete/partial` 报告在修复前得到 `incomplete_reviews=0`，预期为 12。修复后为 12，均不进入可判定的漏检或误报统计；结果列出有效问题稿、合格稿数量。
- `tests/test_review_feedback_context.py`：CLI 生成 v1、核验、采纳意见、返修 v2 和复审后，`inspect` 中找不到待核验输入，原实现也未向复审传递原意见与处置。修复后实际初始核验消息持久保存，包含完整稿、原评审、逐条处置、任务及产物版本和当前适用性。`accepted` 仍待重新核对，`needs_evidence` 意见延续。
- `tests/test_calibration.py::test_calibration_retains_complete_report_and_actual_evaluation_trace`：修复前读取结果 `summary` 抛出 `KeyError`。修复后完整摘要、资料缺口、实际消息与逐请求模型信息、用量、错误和工具调用均保留。请求抛异常时也记录尝试及未知用量。

完成语义集中在 `review.review_report_status`：只有 `pass/fail` 且 `completeness=full` 才是完整核验；生产核验与校准共同使用。未完成不能成为保存通过结论。

## 执行

在此 worktree 运行，显式设置 `PYTHONPATH=src`，使用主仓库锁定虚拟环境：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& D:/AAA小影科技/content-task-agent/.venv/Scripts/python.exe -m pytest tests/test_review_feedback_context.py tests/test_calibration.py tests/test_revision_disposition.py tests/test_review_save.py -q
& D:/AAA小影科技/content-task-agent/.venv/Scripts/ruff.exe check src/cta/review.py src/cta/calibration.py tests/test_review_feedback_context.py tests/test_calibration.py
```

结果：21 个定向回归全部通过，ruff 通过。真实语义质量仍须由主控进行新版实际校准和返修验收；此文件不把离线通过声明为真实验收完成。

本支随后执行 `python -m pytest -q` 全量离线回归，115 个测试全部通过（退出码 0）。全量运行采用相同 `PYTHONPATH` 与锁定虚拟环境，包含真实落盘的跨进程恢复用例。
