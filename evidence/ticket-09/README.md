# 09 输入更新与成果有效性复核

日期：2026-10-29。实现：票 09（输入版本、资源/语义更新、知识/资料采用与复核）。

## red → green

在 07/06/11 集成基线 `4cf0595`（无 update/adopt 入口）上运行：

```text
$ py -m pytest tests/test_update_validity.py
FFFFFFF                                                                  [100%]
7 failed
```

实现后：

```text
$ uv run pytest -q
87 passed
$ uv run ruff check src tests
All checks passed!
```

## 确定性证据

- `01-create.json`：输入版本 1。
- `02-resource-update.json`：预算 10→20 元，输入版本 2；`change.kind=resource`；模拟已用/轮数未重置。
- `03-semantic-update.json`：受众改为研究生，`change.kind=semantic`；阶段 `input_updated`；旧简报/产物不冒充新输入。
- `04-knowledge-adoption.json`：显式采用 `campaign-creation 2026-10-30.v1`；知识正文/版本/哈希更新，阶段 `input_updated`。
- `05-material-adoption.json`：显式采用资料 `2026-10-30.v1`；任务版本记录更新，受影响主张/评审需复核。
- `06-inspect.json`：两个输入版本、知识加载、消息追加与版本。
- `07-complete-before-update.json` / `08-resource-update-after-complete.json` / `09-save-reused-after-resource-update.json`：已完成任务仅改预算后仍 `complete`，既有消耗/轮数/保存记录复用，不重新执行。
- 语义更新后保存被 `input_changed` 拒绝（测试）：旧产物不能在新输入下直接完成。

## 验收对照

| 验收点 | 证据 |
| --- | --- |
| 目标/受众/约束/确认要求更新形成任务版本，保留原输入与来源 | `task_inputs` 多版本；`inspect.input_versions`；`03`/`06` |
| 同输入修订形成产物版本，二者不混用 | `outputs`/`briefs` 版本；06/04 证据 |
| 更新继承消耗/模拟时间/轮数/历史，显式新建才独立 | `02`、`07`–`09`；`create --new`（01/04） |
| 仅资源变化重估，语义未变的事实/内容/评审复用并记依据 | `02` 资源路径；完成态 `09` 复用保存 |
| 受众/目标变化更新简报与策略，检查受影响内容/评审 | `03`、`06`；`run` 追加输入更新事实；`input_changed` |
| 产品事实/品牌/素材变化标记受影响主张/画面/评审 | `05`；`adopt-material` 阶段 `input_updated` |
| 任务外知识显式采用，不静默替换；记录来源/变化/复核 | `04`；`knowledge_loads` 正文/版本/哈希；`knowledge_adoption` 消息 |
| 主 Agent 通过既有续跑/取舍/返修处理变化，无路径未完成 | 复用 `run`/策略/评审；`stop_incomplete` 路径（07） |
| 少量明确规则，不建设文件监视/复杂依赖图 | 显式 CLI 操作；无 watcher/自动合并 |
| CLI 演示资源/语义/事实/知识更新并核对版本、复用与资源 | `01`–`09`；7 个测试 |

## 限制

- 语义更新后的完整重做（新简报→新草稿→新评审→保存）只在机制上由 `run` 与现有能力承接；本票证据没有跑完整真实重做，避免与 12 的真实核心重复。
- 素材/知识采用是显式操作；外部文件变化不会被监视或自动纳入。
- 旧产物/旧评审保留为历史，不自动删除；保存前 `input_changed` 拒绝旧版本冒充新交付。
