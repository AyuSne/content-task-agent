# 检查记录

## 目录整理后：2026-10-31

交付文档统一为 `docs/` 下五份，合成资料迁至 `data/`，开发记录移至 `notes/`。运行时与测试同步更新读取路径，业务规则保持原样。

| 检查 | 结果 | 输出 |
| --- | --- | --- |
| 全量离线回归 | 343 项通过 | [pytest.txt](layout-2026-10-31/pytest.txt) |
| Ruff | All checks passed | [ruff.txt](layout-2026-10-31/ruff.txt) |
| compileall | 退出码 0 | [compileall.txt](layout-2026-10-31/compileall.txt) |
| 归档核对 | 85 份文件哈希一致，390 条 Trace 可解析 | [artifact-check.txt](layout-2026-10-31/artifact-check.txt) |
| 离线复现 | 六组场景达到预期，真实请求 0 次 | [offline-check.txt](layout-2026-10-31/offline-check.txt) |
| 目录与链接 | 五份交付文档、资料完整、用户正文与本地链接核对通过 | [document-check.txt](layout-2026-10-31/document-check.txt) |

实际 Python、依赖版本、改动范围与保留情况见 [environment.json](layout-2026-10-31/environment.json)。20 份合成资料字节不变，AI 使用说明仅调整链接，用户复盘逐字节一致。

## 复现命令

```powershell
uv run pytest tests -q
```

```powershell
uv run ruff check src tests scripts examples/submission
```

```powershell
uv run python -m compileall -q src tests scripts examples/submission
```

```powershell
uv run python examples/submission/verify.py
```

```powershell
uv run python examples/submission/run_offline.py
```

## 上一轮记录：2026-10-30

目录调整前的 [回归日志](pytest.txt)、[环境](environment.json)、[文档检查](document-check.txt) 和 [离线复现](offline-check.txt) 保留在本目录。真实模型记录独立保存在[提交样例](../README.md)中。
