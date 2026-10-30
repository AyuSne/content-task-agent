# 内容任务规划 Agent

选做第 2 题。为虚构剪辑 App「帧剪」准备内容活动方案：查资料、写脚本、核验返修，再保存方案与执行报告。任务按给定预算和模拟截止推进，支持中断续跑。

Python 原生工具循环，SQLite 保存状态，DeepSeek 提供真实模型决策，业务工具使用本地合成资料和费用表。

## 先看结果

| 入口 | 内容 |
| --- | --- |
| [30秒校园推广方案](examples/submission/live-normal/campaign-readable.md) | 真实任务最终稿，展示活动安排、分镜、素材和制作步骤 |
| [正常任务执行报告](examples/submission/live-normal/execution-report.md) | 17 个主轮次，核验后返修并保存，模拟工具费 4.70 元 |
| [保存中断与恢复](examples/submission/live-save-recovery/summary.json) | 保存已生效后注入进程中断，续跑核实交付，保存尝试未重复 |
| [全部提交样例](examples/submission/README.md) | 三组真实记录、六组离线场景及题面覆盖表 |
| [方案与设计](docs/02-solution.md) | 调用流程、动态决策、职责和技术取舍 |

## 安装与离线体验

需要 Python 3.12+ 和 uv。进入克隆后的仓库根目录，以下命令均为单行。

安装锁定依赖：

```powershell
uv sync --locked
```

无需密钥，运行当前协议的完整离线演示：

```powershell
uv run python examples/submission/run_offline.py
```

会执行正常保存、检索失败后续跑、缺预算、缺截止、预算不足和截止不足六组场景，并检查重复创建与执行。结果写入新的 `.cta-runs/submission-offline/` 子目录，包含方案、报告、Trace 和实际状态。

离线演示使用预设模型响应，工具执行、校验、计量和保存都走真实程序。真实模型的决策过程见上面的三组实测。

## 真实模型体验

将 `.env.example` 复制为本地 `.env`，填写 `DEEPSEEK_API_KEY`；已有配置直接保留。模型使用官方接口直连。下面的 `run` 会产生真实 API 费用。

先给新任务指定独立数据目录：

```powershell
$env:CTA_DATA_DIR = Join-Path $PWD '.cta-runs/try'
```

创建任务：

```powershell
$task = uv run cta --mode real create --new --topic '校园社团招新自动卡点推广' --budget 10 --deadline '2026-10-29T01:00:00+08:00' --audience '18—24岁大学生' --requirement '一份包含30秒主视频脚本的活动方案，突出自动卡点与微调，说明素材状态和制作步骤' | ConvertFrom-Json
```

本题默认模拟起点为北京时间 2026 年 10 月 29 日 00:00，示例给出一小时模拟时间。任务预算是合成工具费，实际模型费用、制作和投放费用分别记录。

开始执行：

```powershell
uv run cta --mode real --format text run $task.task_id
```

查看状态：

```powershell
uv run cta --mode real --format text status $task.task_id
```

导出已有方案和执行记录：

```powershell
uv run cta --mode real export $task.task_id
```

文件位于当前数据目录的 `real/exports/<task_id>/`。方案阅读用 `campaign-v*.md`，过程核对用 `execution-report.md`，工具往返见 `trace.jsonl`。

中断后续跑：

```powershell
uv run cta --mode real --format text resume $task.task_id
```

新任务默认 24 个主轮次，每轮一个动作。续跑延续原成果、账本和轮数；待补充任务先用 `update` 补齐输入。更多操作见[运行说明](notes/delivery/05-operations.md)。

## 本地检查

全量测试：

```powershell
uv run pytest tests -q
```

静态检查：

```powershell
uv run ruff check src tests scripts examples/submission
```

核对提交样例的原始文件哈希，无需原电脑或数据库：

```powershell
uv run python examples/submission/verify.py
```

本轮回归为 **343 项通过**，原始输出见[检查记录](examples/submission/validation/README.md)。

## 代码与文档

```text
README.md
src/                         # 程序与模型、工具接入
tests/                       # 可执行测试
examples/                    # 输入、实际输出、Trace 与检查结果
data/                        # 合成资料、费用和耗时规则
docs/
  01-requirements.md          # 需求与成功标准
  02-solution.md              # 流程、决策和设计取舍
  03-evaluation.md            # 测试与实际运行结果
  04-ai-usage.md              # AI 使用与个人贡献
  05-retrospective.md         # 项目复盘
pyproject.toml
uv.lock
.env.example
```

业务 Skill 位于 `.agents/skills/`，版本由 `knowledge/` 管理。`scripts/` 保存辅助检查和评估脚本。开发讨论、草稿和进度归入 `notes/`，历史证据继续保留在 `evidence/` 及原样例目录。

[需求与题面覆盖](docs/01-requirements.md) · [方案与设计](docs/02-solution.md) · [评估](docs/03-evaluation.md) · [AI使用与个人贡献](docs/04-ai-usage.md) · [复盘](docs/05-retrospective.md)

开发记录：[notes 索引](notes/README.md) · [历史证据](notes/delivery/06-evidence-index.md) · [旧任务恢复](notes/legacy-recovery.md) · [提交清单](notes/submission-checklist.md)
