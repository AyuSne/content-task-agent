# 任务版本有效性修复

日期：2026-10-29（北京时间）。基线：`ce90f21`。测试 interface：公开 CLI，模型在既有脚本注入 seam，恢复使用真实新进程和落盘数据。未请求真实模型。

## 已复现的缺陷

1. `test_adopted_material_bodies_are_used_after_new_process_resume`：先读 M-006、显式采用 `2026-10-30.v1`，新进程 `resume` 仍返回 `2026-10-29.v1` 和待获取授权旧正文。
2. `test_adopted_knowledge_reaches_main_and_preserves_previous_body`：采用新版创作知识后，主消息只有版本号与 hash，实际新版正文不在主模型输入中。
3. `test_import_validates_against_frozen_task_materials`：任务已有有效冻结证据后，未显式采用的外部资料改变使人工稿导入以 `invalid_campaign` 拒绝，提示原引用在新外部正文中找不到。
4. `test_resource_update_records_reuse_without_rewriting_origin_and_appends_new_constraints`：资源更新没有产物/评审继续适用新输入版本的权威关联，也没有向主模型追加更新后的预算与截止。
5. `test_resource_update_save_reuses_semantic_review_and_original_output`：纯预算更新后保存被 `input_changed` 拦截；本支提供明确复用依据，保存端由总控同时修复。

## 实现与契约

- 内容任务创建时冻结实际资料正文；首次执行前的外部修改也不替换该版本。显式采用资料时，把完整旧版与新版、来源和变化说明追加至主消息，再更新当前投影。`inspect.material_history` 提供完整历史；后续读取与重启均使用新投影，旧消息保持原样。
- 显式采用知识时，实际新版正文追加至主模型消息。旧加载正文及版本、hash和新正文另以消息元数据保留，`inspect.knowledge_history` 可回查。`knowledge_loads` 与 `material_snapshots` 是当前采用投影，历史在 append-only 消息中；生成继续读当前创作知识投影，核验知识显式采用同步到 reviewer 投影。
- 导入校验改用本任务已冻结、已采用的资料，保持外部文件不自动更新任务的规则。
- 每次任务更新立即追加完整新输入。纯资源更新逐字段核对原产物输入的语义一致，保留 `outputs.input_version` 原形成版本；以 `input_update` 消息元数据中的 `reuse` 建立当前适用关联。`output_reuse_basis(conn, task, output)` 为保存端提供明确依据，`status.content_reuse` 同源显示。语义待复核的稿件不因后续资源更新获得适用依据。
- 资源更新估算完整剩余生成、核验、保存路径，已通过当前稿则只剩保存；已完成则没有新业务动作。费用或模拟截止不可行时明确未完成，估算不计已发生消耗，后续工具仍逐次守卫。

## 当前验证

`tests/test_version_adoption.py`、`test_update_validity.py`、`test_human_import.py` 除待主控保存端配合的第5项外，22个定向测试通过；修改文件 ruff 与 `git diff --check` 通过。第5项的 red 已保留为集成验收测试，未用跳过或弱化断言隐去。完整离线测试与独立双轴复查由总控在集成后核对。
