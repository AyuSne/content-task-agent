# 最终检查结果（已执行记录）

本目录将已完成检查的原始输出按字节复制到examples/，方便按题面从输入、输出、Trace与测试结果一起评审。此次文档整理没有重新执行这些检查；原始evidence仍保留。

运行代码提交：`63ec7c8dbf005d23d8f5aa1d6eb2226288f8e29d`；原证据交付提交：`207f0704a2b6f0bd1d7c2aa5c2b0fc0d8f6b6a83`。本次为本地锁定依赖环境验证，历史60249d6的独立归档/新虚拟环境204项结果另有记录。

| 检查 | 已记录结果 | 实际输出 |
| --- | --- | --- |
| 全量pytest | 207项通过，退出码0 | [pytest-207.txt](pytest-207.txt) |
| 测试收集数 | 207项，退出码0 | [collected-207.txt](collected-207.txt) |
| ruff | 通过，退出码0 | [ruff.txt](ruff.txt) |
| compileall | 通过，退出码0；安静模式成功时输出为空 | [compileall.txt](compileall.txt) |

[机器记录](results.json)列出原命令、源文件和SHA-256。复制后的四份输出均与原文件hash一致；退出码来自原执行记录及[运行说明](../../../evidence/eval-2026-10-29/environment-note.md)，不是由空日志推断。

对应的场景输入/输出/Trace见[初始9场景](../live/)和[修复后2场景](../post-fix/)；业务状态、评审结果及失败说明见[完整eval报告](../../../notes/delivery/11-fresh-eval.md)。

复现检查：

```powershell
uv sync --locked
uv run pytest -q
uv run ruff check src tests scripts
uv run python -m compileall -q src scripts
```
