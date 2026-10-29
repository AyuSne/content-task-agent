# 证据索引

- [完整修复验收报告](10-repair-acceptance.md)：实现、C-01至C-12、真实核心、校准、对照和未完成项。
- [机器汇总](../../examples/acceptance-summary.json)：19个真实内容任务（含历史失败），任务API用量与未知项。校准、诊断和外部评价另计。
- [真实样例](../../examples/repair-live/)：每例create/run/resume/inspect/export、版本、评审、账本及Trace；故障前后对照在对应recovery目录。
- [运营改稿](../../examples/repair-human/import-01/)：原已核实副本、编辑文件、导入、主模型复审与保存。
- [当前六对校准](../../examples/repair-calibration/knowledge2/result.json)及[标签来源](../../fixtures/calibration/2026-10-29.v2-notes.md)。旧版与旧结果保留。
- [真实三方式](../../examples/repair-comparison/normal-01/suite.json)和[有限回读](../../examples/repair-comparison/readback-01/result.json)。
- [干净环境204项回归](../../evidence/repair/final-clean-tests.txt)、[ruff](../../evidence/repair/final-clean-ruff.txt)、[89文件字节核对](../../evidence/repair/portable-artifact-check.txt)。
- [Mock完整演示](../../examples/offline/model-script.json)与[实际输出](../../examples/offline/actual-result.json)，不证明真实决策。
- `evidence/ticket-01`至`ticket-14`：9d00fc9之前的历史证据，未删除或回填为修复后的成功。

`audited-inspect.json`是当前权威快照；原`05-inspect.json`保留场景结束时事实。`invariants_hold`只表示账本、协议和文件一致性，不等于业务已完成。真实早期失败不加入当前成功率的分母后再隐去；报告按阶段与条件分组。
