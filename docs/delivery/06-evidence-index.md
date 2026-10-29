# 证据索引

## 当前运行代码与新增eval

运行代码63ec7c8，本轮eval证据交付提交207f070；后续文档更新不改变这两个固定范围。当前工作断点以[进度索引](../progress/PROGRESS.md)为准。

- [新鲜eval报告](11-fresh-eval.md)：两处策略修复、完整207项回归、11场景、人工稿与操作eval、18份有效评审样例及实际失败。
- [题面要求映射](01-requirements-analysis.md) / [测试场景映射](04-testing.md)：按第2题和通用交付要求进入。
- [最终检查输出](../../examples/eval-2026-10-29/validation-results/README.md)：pytest、收集数、ruff和compileall，复制自原始evidence，字节hash可核对。
- [新集合机器汇总](../../examples/eval-2026-10-29/summary.json)：11场景的状态、轮数、模拟费用及135次请求尝试（含人工稿），校准另列。
- [初始9场景](../../examples/eval-2026-10-29/live/) / [修复后2场景](../../examples/eval-2026-10-29/post-fix/)：每例输入、完整inspect、工具参数/结果、方案、报告和Trace。
- [保存中断恢复](../../examples/eval-2026-10-29/live/save-crash-recovery/manifest.json)：实际exit70、恢复前inspect及新进程续跑；核实同次保存，无重复业务收费。
- [人工稿导入](../../examples/eval-2026-10-29/human-import/) / [操作eval](../../examples/eval-2026-10-29/operations-v2/)：编辑来源、导入新版本、复核保存、重复执行、外部编辑保留、显式修复和资源复用。
- [原6对重新校准](../../examples/eval-2026-10-29/reviewer-baseline.json) / [新3对修正后结果](../../examples/eval-2026-10-29/reviewer-holdout-v2.json)；[初版标签错误及实际评审](../../examples/eval-2026-10-29/reviewer-holdout.json)保留，有效样本成功率按修正后的完整集合统计。
- [策略红测](../../evidence/eval-2026-10-29/strategy-red.txt) / [17项定向绿测](../../evidence/eval-2026-10-29/strategy-green-final.txt) / [环境与中间失败说明](../../evidence/eval-2026-10-29/environment-note.md)。
- [初始集合审计](../../examples/eval-2026-10-29/live/audit-summary.json) / [复测审计](../../examples/eval-2026-10-29/post-fix/audit-summary.json)：账本、时钟、轮数、消息与当前保存一致；[源码指纹](../../evidence/eval-2026-10-29/source-hashes.json)及[密钥泄漏扫描](../../evidence/eval-2026-10-29/secret-scan.json)。

## 历史修复集合与对照

- [修复验收快照](10-repair-acceptance.md)：截至60249d6的运行代码、C-01至C-12及固定范围独立复查；[机器汇总](../../examples/acceptance-summary.json)纳入19个任务，含早期失败与人工稿。
- [历史真实样例](../../examples/repair-live/) / [历史人工稿](../../examples/repair-human/import-01/)：故障前后状态、账本、版本、核验及保存。
- [历史工作知识v2校准](../../examples/repair-calibration/knowledge2/result.json)及[标签依据](../../fixtures/calibration/2026-10-29.v2-notes.md)；早期版本与失败保留。
- [三方式实际实验](../../examples/repair-comparison/normal-01/suite.json) / [有限回读成对实验](../../examples/repair-comparison/readback-01/result.json)：输入、顺序、额外开销及失败基线可查，本轮未重跑这些效果对照。
- [60249d6干净环境204项](../../evidence/repair/final-clean-tests.txt)、[依赖同步](../../evidence/repair/final-clean-sync.txt)、[89文件离线核对](../../evidence/repair/portable-artifact-check.txt)。
- [独立审查范围](07-final-review.md) / [原始复查记录](../../evidence/repair/final-independent-review.md)：结论不扩展到后来的策略修复。
- [Mock模型脚本](../../examples/offline/model-script.json)与[离线实际输出](../../examples/offline/actual-result.json)：验证流程、规则及输出。
- evidence/ticket-01至ticket-14：原9d00fc9之前的实施证据，保留历史含义。

## 读取口径

05-inspect.json保留场景结束时事实；audited-inspect.json为随后核实的当前状态，同一任务可能已发生显式人工导入或资源更新。invariants_hold只表示账本、协议与文件一致，业务完成仍由状态、当前核验和核实保存判断。失败、未知用量与成功分列；[AI使用与时间记录](09-ai-usage.md)说明统计范围。
