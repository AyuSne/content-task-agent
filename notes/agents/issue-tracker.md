# Issue tracker: Local Markdown

本仓库的规格与工单以本地Markdown文件管理。以下路径以 `content-task-agent/` 为根。

## 文件约定

- 每项功能或改造使用独立目录：`.scratch/<feature-slug>/`。
- 规格文件：`.scratch/<feature-slug>/spec.md`。
- 实施工单：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，每张工单一份文件，按依赖顺序从01编号。
- 工单开头用 `Status:` 记录[分流状态](triage-labels.md)，用 `Blocked by:` 记录真正阻塞它的工单。
- 评论和后续讨论追加到文件末尾的 `## Comments`。
- “发布到工单系统”表示按上述约定写入本地文件；“获取工单”表示完整读取指定文件及其评论。
- 工单就绪状态描述准备程度；实际工作范围仍以用户当前指示为准。

## 历史材料

父项目 `D:/AAA小影科技/.scratch/content-task-agent-build/` 中的旧规格与14张工单保留原位，作为历史依据。新一轮改造使用新的功能目录，保留新旧规格之间的追溯关系；当前确认状态沿本仓库设计与进度记录核对。

## Wayfinding约定

需要wayfinder时，地图位于 `.scratch/<effort>/map.md`，子工单位于 `issues/NN-<slug>.md`。

- `Type:` 使用 `research`、`prototype`、`grilling` 或 `task`。
- 地图内决策工单的 `Status:` 使用 `claimed`、`resolved` 标记领取及解决；它们是地图工作状态，与分流标签分别解释。
- `Blocked by:` 列出前置决策工单；按编号优先处理前置已解决且未领取的工单。
- 领取时先保存 `Status: claimed`；解决后追加 `## Answer`、改为 `resolved`，并在地图中加入带链接的结论。
