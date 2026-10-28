# 14 全量交付与验收

日期：2026-10-29。

## 交付内容

- 代码：`src/cta/`（CLI、主循环、评审、保存、导出、续跑、策略、导入、校准）。
- 测试：`tests/`，全量 **112 passed**；`ruff check src tests` 通过。
- 文档：`README.md`、`docs/architecture.md`、`docs/delivery/01–08`。
- 证据：`evidence/ticket-01` … `ticket-13`，每票 README/red/green/JSON/交付样本。
- 进度：`docs/progress/PROGRESS.md`、`01/02/03-2026-10-29.md`。
- 复盘：`docs/delivery/08-retro.md`。
- 最终双轴评审：`docs/delivery/07-final-review.md`。

## 验收对照（C-01..C-12）

| 编号 | 验收点 | 证据 |
| --- | --- | --- |
| C-01 | 输入校验、身份、模式隔离、跨进程 | ticket-01 |
| C-02 | 简报、知识、检索、计量 | ticket-02 |
| C-03 | 独立生成完整草稿、素材/证据 | ticket-03 |
| C-04 | 独立核验、保存、导出、报告 | ticket-04 |
| C-05 | 跨进程续跑/暂停/中断 | ticket-05 |
| C-06 | 处置/返修/复审/边界 | ticket-06 |
| C-07 | 资源反馈策略调整 | ticket-07 |
| C-08 | 保存未知/导出修复 | ticket-08 |
| C-09 | 输入更新与有效性 | ticket-09 |
| C-10 | 人工稿导入/冲突 | ticket-10 |
| C-11 | 评审校准/反例 | ticket-11 |
| C-12 | 真实核心、对比、交付限制 | ticket-12、13、12 README |

## 最终限制（必须随交付一起说明）

- 真实任务在 12 轮内最远到草稿 v1 + 评审 fail；**没有真实“评审通过→保存”**，真实返修也未跑通。
- 评审校准有 1 个合格稿误报、2 个未完成；评审不能当绝对真值。
- 13 是描述性对比，不是受控 A/B。
- 05–13 未逐票做独立双轴评审（01–04 有）；最终双轴评审覆盖整体，残余项在 `07-final-review.md` 记录。

## 复现

```powershell
cd D:\AAA小影科技\content-task-agent
uv run pytest -q
uv run ruff check src tests
```

真实/确定性运行见 `docs/delivery/05-operations.md` 与各票 README。
