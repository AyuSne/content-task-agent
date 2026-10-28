# 01 验收证据

日期：2026-10-29。实现：票 01（创建、查询及重复请求）。

## red → green

skeleton 阶段（`src/cta/cli.py` 仅解析参数并返回 70）运行：

```text
$ uv run pytest -q
FAILED tests/test_task_intake.py::test_missing_required_inputs_create_needs_input_task_with_reasons
FAILED tests/test_task_intake.py::test_invalid_values_are_explained_and_raw_input_preserved
FAILED tests/test_task_intake.py::test_complete_input_creates_queryable_task_without_work_or_spend
FAILED tests/test_task_intake.py::test_duplicate_request_returns_same_task_and_does_not_execute
FAILED tests/test_task_intake.py::test_equivalent_normalized_input_is_treated_as_duplicate
FAILED tests/test_task_intake.py::test_explicit_new_request_gets_independent_identity
FAILED tests/test_task_intake.py::test_persistence_is_visible_to_a_real_separate_process
FAILED tests/test_task_intake.py::test_modes_have_separate_stores_and_identities
FAILED tests/test_task_intake.py::test_status_never_echoes_the_api_key
9 failed in 1.05s
```

失败原因均是行为未实现（CLI exit 70 / 无 JSON），不是导入或环境错误。实现后：

```text
$ uv run pytest -q
...........                                                              [100%]
11 passed
$ uv run ruff check src tests
All checks passed!
```

## CLI 实测

环境：`CTA_DATA_DIR=evidence/ticket-01/data`，`CTA_SIM_START=2026-10-29T00:00:00+08:00`，模式默认 mock。

| 文件 | 命令 | 关键观察 |
| --- | --- | --- |
| `01-missing.json` | `cta create` | `status=needs_input`，三个 `required` 错误，任务已持久化 |
| `02-create.json` | 完整输入（预算 10 元） | `status=in_progress`，`budget_cents=1000`，`used_rounds=0`，`sim_spent_cents=0` |
| `03-status.json` | `cta status <id>` | 同一任务、输入与状态 |
| `04-duplicate.json` | 规范化等价的重复请求 | 相同 `task_id`，`duplicate=true`，仍 0 轮 0 费用 |
| `05-new.json` | 加 `--new` | 不同 `task_id`，`duplicate=false` |
| `06-list.json` | `cta list` | 只列 mock 模式任务 |
| `07-real.json` | `cta --mode real create ...` | 独立 real 库与任务 ID |

数据库文件位于 `data/mock/tasks.db` 与 `data/real/tasks.db`（运行产物，不提交）。密钥从未出现在任何输出；`model_config.api_key` 仅为 `set`。

## 验收对照

- **环境与 CLI 步骤**：`README.md` 的 uv sync、create/status/list 命令；`uv.lock` 锁定。
- **缺失/无效输入**：`01-missing.json`、`test_invalid_values_are_explained_and_raw_input_preserved`、`test_unparseable_deadline_is_explained`；原输入在 `input.raw`，解释在 `validation.errors/interpretations`。
- **SQLite 持久与跨进程**：`test_persistence_is_visible_to_a_real_separate_process` 用真实子进程查询同一 `tasks.db`。
- **重复/显式新建**：`04-duplicate.json`、`05-new.json`；指纹只由规范化明确字段构成。
- **模式隔离**：`07-real.json` 与 `data/real/tasks.db`；`test_modes_have_separate_stores_and_identities`。
- **状态视图**：`needs_input` / `in_progress` 已由 CLI 产生；`incomplete` / `complete` 的视图字段已就位，待 04/05 产生。
- **密钥不外泄**：`test_status_never_echoes_the_api_key`；所有证据文件已检查不含密钥。
- **不宣称完成**：完整输入仅 `in_progress`，`current_output=null`、`save=null`。
