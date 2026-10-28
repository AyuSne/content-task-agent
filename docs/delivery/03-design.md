# 设计（交付文档 03）

## 核心设计选择

1. **CLI 为唯一公开 seam**：所有行为可由 `python -m cta` 跨进程观察；测试不直接查库断言业务行为。
2. **SQLite 权威**：消息、尝试、账本、版本、评审、保存均落库；导出是投影，可重建。
3. **模型边界可替换**：真实 `DeepSeekClient` 与脚本化 `ScriptedClient` 同 Protocol；真实/Mock 数据、请求身份、报告分离。
4. **单轮单工具**：每轮只接受 0/1 个已校验工具调用；拒绝也计轮并反馈，避免“模型说完成”绕过状态机。
5. **完成需证据**：核验通过 + 无 must_fix/证据缺口/关键阻塞 + 输入版本一致 + 保存文件哈希核实，缺一不可。
6. **未知优先于猜测**：保存回执丢失、模型用量未知、真实金额未知、评审未完成都显式记录，不静默补成功。
7. **先记录后副作用**：保存先写 `unknown` 记录再写文件；尝试先落库再调用；崩溃可恢复/核实。
8. **显式变更**：输入更新、知识/资料采用、人工稿导入都是显式 CLI 操作；无文件监视、无自动合并。

## 状态机（简化）

```text
created -> brief_ready -> draft_ready -> reviewed -> complete
                ^              |             |
                |              v             v
             input_updated   review_incomplete/save_failed/save_unknown/stopped
```

- `input_updated`：新输入版本，旧简报/产物需复核；资源更新可保持原阶段。
- `reviewed`：评审完成但未保存；有 must_fix 时需 disposition + 返修 + 复审。
- `complete`：不可再 run；`update` 可新建输入版本并重回 in_progress（资源更新可保持 complete）。

## 关键流程

### 正常路径

`create → run（简报）→ run（策略+生成+草稿）→ review → save → export`。

### 核验/返修

评审 fail → `run` 注入 `[核验结果]` + dispositions → 模型逐条 `record_disposition` → accepted 必须 v2 `propose_campaign` → 再次 `review` → `save`。旧评审不继承。

### 中断/恢复

`run`/`resume` 先 `recover_task`：核实未完成工具调用、运行中尝试、未知保存；完成可证明的结果则补记录，否则标 `interrupted`/`unknown`；不重复计费，不静默重放。

### 保存未知/修复

`save` 先写 unknown 记录；文件核实成功才 verified/complete。`reconcile`/`export --repair` 从权威输出重建文件、更新同一保存记录、不新增业务尝试；`save` 对未知记录保持拒绝。

### 输入更新

`update` 写新 `task_inputs` 版本；资源-only 保持成果，语义变更置 `input_updated` 并拒绝旧产物保存；`adopt-knowledge`/`adopt-material` 显式采用新版本并触发复核。

### 人工稿

`export` 的 Markdown 带 `<!-- cta:{task_id,output_id,output_version,input_version} -->` 元数据；`import-draft` 校验章节/基稿/受众，形成 `pending_review` 新产物；不导入外部文件永不改变事实。

## 错误与退出码

- `3`：任务不存在。
- `4`：操作被阻止（未核验、输入变更、预算/截止、未知保存、导入冲突）。
- `70`：测试故障注入退出（非产品路径）。
- 其余异常：CLI 输出错误 JSON，不静默吞掉。

## 成本与版本

- 模拟价格 `fixtures/pricing/2026-10-29.v1.json`；估算/预留不计入花费。
- 真实 token 用量逐请求记录；金额 `unknown`。
- 提示词/知识/资料/策略/价格均带版本；结果记录版本。

## 取舍

- 不做自动合并/监视：安全优先，人工稿与输入变更显式。
- 不做真实发布/抓取：边界内可验证。
- 不放宽 12 轮：真实模型未完成时如实记录，不用 Mock 冒充。
