# content-task-agent

沿用[父项目约定](../AGENTS.md)。继续设计或改造时，先读[当前进度](notes/progress/PROGRESS.md)确认阶段与范围；已确认设计取舍见[设计改进记录](notes/design-improvements.md)。交付文档位于 `docs/`，开发记录位于 `notes/`。

## Agent skills

### Issue tracker

规格与工单采用本地Markdown，存于本仓库的 `.scratch/<feature-slug>/`。规则见[工单配置](notes/agents/issue-tracker.md)。

### Triage labels

使用五个默认分流状态。名称与含义见[状态配置](notes/agents/triage-labels.md)。

### Domain docs

单一业务上下文，复用父项目现有术语表。读取和维护规则见[领域文档配置](notes/agents/domain.md)。
