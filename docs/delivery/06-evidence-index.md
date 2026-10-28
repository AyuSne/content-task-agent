# 证据索引（交付文档 06）

路径相对 `content-task-agent/`。每票 README 是该票验收对照与限制的权威说明。

| 票 | 证据目录 | 关键结果 | 主要限制 |
| --- | --- | --- | --- |
| 01 | `evidence/ticket-01/` | 输入校验/身份/模式隔离/跨进程；red 9→green 14 | 无 |
| 02 | `evidence/ticket-02/` | 简报、知识、检索、计量；跨进程 | 真实简报 7 轮/220 分 |
| 03 | `evidence/ticket-03/` | 独立脚本生成、完整草稿、素材/证据；12 轮真实草稿 | 真实草稿评审 fail |
| 04 | `evidence/ticket-04/` | 核验/保存/导出/执行报告；230 分 4 轮 | 真实验收 fail；04 双轴评审已修 |
| 05 | `evidence/ticket-05/` | 跨进程续跑、暂停、生成/保存中断；13 CLI 子进程测试 | 无真实 API；保存修复归 08 |
| 06 | `evidence/ticket-06/` | disposition、返修 v2、复审、边界；430 分 7 轮 | 真实返修未完成 |
| 07 | `evidence/ticket-07/` | 方向比较、完整路径、预留、失败/部分成功；19 记录 | Mock；真实反馈归 12 |
| 08 | `evidence/ticket-08/` | 保存未知、文件修复、不重复计费；4 测试 | 版本不一致保持未知 |
| 09 | `evidence/ticket-09/` | 输入 v2、语义/资源更新、知识/资料采用、旧产物拒绝 | 完整语义重做未入证据 |
| 10 | `evidence/ticket-10/` | Markdown 导入、待核验 v2、基稿冲突/章节/受众校验 | 固定章节格式 |
| 11 | `evidence/ticket-11/` | 六对校准；问题 6/6、误报 1、未完成 2；19 请求/112.3 s | 小样本、P6 误报 |
| 12 | `evidence/ticket-12/` | 6 个真实任务；草稿成功；评审 fail 4 must_fix；14 请求/201,549 输入 | **无真实完成保存** |
| 13 | `evidence/ticket-13/` | 基线 vs 完整管线描述性对比 | 非同任务受控 A/B |
| 14 | `docs/delivery/` | 需求/架构/设计/测试/运行；README/进度/复盘/评审记录 | 汇总限制 |

## 全量验证

```powershell
cd D:\AAA小影科技\content-task-agent
uv run pytest -q          # 112 passed
uv run ruff check src tests
```

## 真实 API 证据

- 11：`evidence/ticket-11/real-calibration.json`、README。
- 12：`evidence/ticket-12/real-*.json`、`delivery-draft/`、README。
- 真实金额未知；Key 不在证据中。

## 双轴评审

- 01–04：`reviews.md`（运行记录目录）。
- 14 最终：`docs/delivery/07-final-review.md`（本次交付的最终双轴评审与未解决项）。
