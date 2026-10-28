# 10 人工稿导入及重新核验

日期：2026-10-29。实现：票 10（显式 Markdown 导入、基稿冲突、待核验新稿与重导出）。

## red → green

在 09 基线 `e0e963c`（无导入入口）上运行：

```text
$ py -m pytest tests/test_human_import.py
FFF.FF                                                                   [100%]
5 failed, 1 passed
```

实现后：

```text
$ uv run pytest -q
93 passed
$ uv run ruff check src tests
All checks passed!
```

## 确定性证据

- `01-draft.json`：完整草稿 v1。
- `02-export.json`：导出可编辑 Markdown，含 `<!-- cta:{task_id,output_id,output_version,input_version} -->` 元数据。
- `original-v1.md` / `edited-v1.md`：运营在任务外修改标题后的人工稿。
- `03-import.json`：显式导入形成产物 v2（`pending_review`），基稿 v1 保留，导入文件路径与 sha256 记录。
- `04-review-v2.json`：v2 重新核验通过；旧稿通过结论未继承。
- `05-save-v2.json`：保存 v2 并核实文件，任务 `complete`。
- `06-inspect.json`：两个产物版本、导入来源、评审、保存与账本。

测试覆盖：有效导入→复核→保存；缺必要章节拒绝且保留原稿；过期基稿拒绝静默覆盖；未导入外部编辑不改变权威事实；正文改变已确认受众要求先 `update`；缺少元数据拒绝。

## 验收对照

| 验收点 | 证据 |
| --- | --- |
| 可编辑方案有稳定章节与来源表达，导入关联任务/基稿 | `02-export.json`、`original-v1.md`；固定八章节；元数据 |
| 只改正文形成新候选产物，保存编辑来源/原稿/历史 | `03-import.json`；`imports` 表；`outputs` v1/v2 |
| 新稿标为待核验，当前采用稿明确 | `outputs[-1].status=pending_review`；`current_output` v2 |
| 改变目标/约束须显式 update，不静默升级授权 | 受众变化测试 `semantic_change_requires_update`；09 update |
| 外部文件未导入不改变数据库事实/完成结论 | `test_unimported_external_edit_*` |
| 基稿落后时说明冲突、拒绝覆盖、保留编辑文件 | `stale_base`；文件仍在 |
| 显式续跑后主 Agent 读取修改并检查整体关联 | `review` v2；旧评审不适用 |
| 核验保存成功后从当前权威版本重导出 | `05-save-v2.json`；文件哈希与 v2 内容 |
| CLI 覆盖有效导入、缺章节/引用、过期基稿、未导入编辑、改硬要求 | 6 个测试 + 证据 |

## 限制

- Markdown 解析器按固定业务章节与行格式工作；格式外自由改写会被拒绝，不猜测。
- 不在正文中自动识别所有“硬要求变化”；目前对已确认受众变化显式拒绝，其他语义变化由用户显式 `update` 处理。
- 不做自动合并、文件监视或实时同步；未导入的编辑永远不会改变任务事实。
- 导入文件路径依赖本机；证据中记录绝对路径，复现需按 README 重新导出。
