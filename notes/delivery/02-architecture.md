# 架构

CLI 接收运营输入，以一条主循环推进任务。主 Agent 持有连续上下文，负责查证、创作、计划和返修；独立评估上下文核验完整方案；SQLite 保存任务事实。

```text
CLI：create / run / resume / inspect / export
                  │
                  ▼
主 Agent：目标、规则、创作 Skill、当前工作视图
                  │ 选择动作，观察结果
                  ▼
工具：资料检索、计划、估算、创作提交、核验、处置、保存
          │                 │
          ▼                 ▼
   SQLite 权威状态      有界内容核验
          │
          ▼
内容活动方案 + 执行报告 + JSON / JSONL Trace
```

## 主要职责

| 位置 | 职责 |
| --- | --- |
| `cli.py`、`service.py` | 创建、查询、显式更新、人工稿和资源采用 |
| `loop.py`、`prompts.py` | 模型循环、系统前馈、原生工具契约、当前状态 |
| `artifacts.py`、`campaign.py`、`task_plan.py` | 接收脚本、组装方案、记录任务计划 |
| `materials.py`、`knowledge.py` | 采用资料、Skill 正文和版本 |
| `review.py`、`evaluation.py`、`dispositions.py` | 独立核验、有限回读和意见处置 |
| `repository.py`、`db.py` | 输入、消息、产物、尝试、账本和保存事实 |
| `save.py`、`recovery.py`、`export.py` | 保存前提、中断核实和可读导出 |

模型接入使用 openai SDK 调用 DeepSeek，离线在同一接入位置注入预设响应。业务规则仍由同一套工具执行。依赖版本由 `uv.lock` 固定。

更详细的维护入口见[实现架构](../architecture.md)，设计原因见[构建思路](../agent-design-rationale.md)。
