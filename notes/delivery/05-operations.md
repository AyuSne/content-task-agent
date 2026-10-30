# 运行与操作

安装和首次体验按[README](../../README.md)执行。命令在仓库根目录运行；PowerShell 中逐条复制单行命令。

## 常用命令

下面的 `$task.task_id` 来自 README 中的创建命令。重新打开终端后，可以用 `list` 找到任务 ID，并重新指定原数据目录。

| 操作 | 单行命令 |
| --- | --- |
| 列出真实任务 | `uv run cta --mode real list` |
| 看状态 | `uv run cta --mode real --format text status $task.task_id` |
| 看完整消息和工具结果 | `uv run cta --mode real inspect $task.task_id` |
| 续跑 | `uv run cta --mode real --format text resume $task.task_id` |
| 更新预算 | `uv run cta --mode real update $task.task_id --budget 20` |
| 导出方案与执行记录 | `uv run cta --mode real export $task.task_id` |
| 核实保存状态 | `uv run cta --mode real reconcile $task.task_id` |
| 查看全部参数 | `uv run cta --help` |

`--mode real` 调用真实模型，默认模式为 Mock。`.env.example` 给出配置字段，密钥放入被忽略的本地 `.env`。SDK 直连官方接口，TLS 校验开启，非思考、非流式，SDK 自动重试为 0。

## 输入更新与人工稿

`update` 形成新输入版本。仅资源变化可以复用适用成果，主题、受众或创作要求变化后需要重新判断内容。传入 `--requirement` 会替换要求列表，更新时保留仍适用的原要求。

运营可以先导出 Markdown，保留元数据和字段标签，修改正文后显式导入：

```powershell
uv run cta --mode real import-draft $task.task_id --file 'edited-campaign.md'
```

随后执行 `resume`。人工稿形成新的待核验版本；旧稿、原要求和已有费用保留。普通导出保留外部编辑，明确需要从权威记录重建时使用 `export --repair`。

## 数据与时钟

默认数据根目录是 `.cta-runs/`，模式下保存 `tasks.db` 和 `exports/<task_id>/`。可通过 `--data-dir` 或 `CTA_DATA_DIR` 使用独立根目录。

模拟起点默认是北京时间 2026 年 10 月 29 日 00:00。业务动作按费用表推进模拟时间，暂停期间保持原进度；真实 API 耗时单独记录。

新任务默认 24 轮，`CTA_MAX_ROUNDS` 只影响创建时初值。续跑延续已有轮数、账本、资料和 Skill 版本。遇到未知保存结果时，程序先核实副作用；明确失败后的新尝试按规则另计费。

## 读取导出

- `campaign-v*.md`：活动方案，适合运营阅读与修改。
- `execution-report.md`：计划、实际步骤、预算、模型用量与保存状态。
- `trace.jsonl`：实际工具调用、结果和版本变化，适合追溯。
- JSON：结构化方案、简报和检查结果。

原始记录中的绝对路径标识当时的运行位置。跨电脑核对提交样例，使用 `uv run python examples/submission/verify.py`，脚本按仓库内归档文件验证哈希。

历史任务按[旧任务恢复](../legacy-recovery.md)选择适用代码和资料。
