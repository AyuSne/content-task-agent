# 08 保存未知核实及导出修复

日期：2026-10-29。实现：票 08（保存回执丢失、文件缺失/不一致、导出修复）。

## red → green

在 06 基线 `ede0448` 上运行本票 CLI 测试：

```text
$ py -m pytest tests/test_save_reconcile.py
FFFF                                                                     [100%]
4 failed
```

实现后：

```text
$ uv run pytest -q
67 passed
$ uv run ruff check src tests
All checks passed!
```

## 确定性证据

- `01-save.json`：正常核验+保存完成，5 个文件逐一哈希。
- `02-missing-file.json`：删除执行报告文件。
- `03-export-repair.json`：`cta export --repair` 从 SQLite 权威内容重建缺失文件；保存记录仍为同一业务记录，不新增保存尝试/费用，`verified=true`。
- `04-reconcile.json`：把方案 JSON 故意改坏后 `cta reconcile` 从权威产物修复，恢复正确内容，任务保持 `complete`，不新增尝试。
- `05-inspect.json`：同一保存记录、文件哈希、账本与尝试事实。

另有两个跨进程/状态测试：
- `CTA_FAULT_SAVE_AFTER_RECORD=1` 在保存记录写入后杀进程（退出 70）：新进程查询为 `unknown`，`cta reconcile` 补写核实事实并完成；保存业务尝试仍只有 1 次，累计 230 分未重复计费。
- 无保存记录时 `cta reconcile` 返回 `not_applicable`，明确不能据此声称完成。

## 验收对照

| 验收点 | 证据 |
| --- | --- |
| 明确任务/产物版本/保存记录/目标文件，区分成功、失败、未知 | `reconcile` 返回 `not_applicable`/`version_mismatch`/`unknown`/`verified`；保存记录 `status`/`verified` |
| 记录与文件一致时补记并复用，不重新生成/不重复保存 | 正常 `cta save`；`reconcile` 对完整文件直接复用；无新尝试 |
| 明确失败交回主 Agent 决定重试；无法核实保持未知 | 失败尝试记录；`CTA_FAULT_SAVE_AFTER_RECORD` 场景 |
| 文件缺失/版本不一致从权威产物修复并留痕；外部改动不静默写回 | `export --repair`、`reconcile`；哈希与 saves 记录更新 |
| 纯核实/重建导出不新增生成消耗；新业务保存另记 | `sim_spent_cents` 不变、`attempts` 不增；新保存尝试按表计费（04） |
| 重复业务记录/防重复写入/重试资源分别核对 | 同一 `save_id`、文件表更新；`cta save` 复用 |
| 最终状态/保存记录/Markdown/JSON/报告同一版本 | `05-inspect.json`；`campaign-v1`、`execution-report.md`、`trace.jsonl` |
| 独立进程故障注入覆盖失败、回执丢失文件完整、缺失、不一致、无法核实 | 4 个测试 + 证据；`red.txt` |

## 已知限制

- 版本不一致时本票保持 `version_mismatch`，不自动覆盖新产物；人工稿/输入更新后的版本选择由 09/10 处理。
- `CTA_FAULT_SAVE_AFTER_RECORD` 是测试故障注入，不是产品能力。
- 真实任务上的保存回执丢失未执行（需要真实运行中断）；由 12 的真实失败/续跑覆盖。
