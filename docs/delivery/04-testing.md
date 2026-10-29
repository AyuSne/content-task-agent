# 测试与验收

## 当前结果与验证范围

运行代码63ec7c8已通过完整**207项测试、ruff及compileall**，输出与复现命令见[examples中的最终检查结果](../../examples/eval-2026-10-29/validation-results/README.md)。本次在本地锁定依赖环境验证。此前60249d6在独立归档、新虚拟环境通过204项的证据保留，两个环境和提交范围分别说明。

测试以公开CLI行为为主，模型、文件、时间和进程故障在实际交互处注入；预算、轮数、保存、版本与恢复按外部可观察结果判定。Mock验证流程和规则，真实运行验证模型根据实际工具返回采取后续动作。

题面核心五类场景及已选续跑加分项均有覆盖。最新结果和全部失败见[新鲜eval报告](11-fresh-eval.md)，较早的C-01至C-12、重复运行及三方式/回读对照见[修复验收快照](10-repair-acceptance.md)。

## 第2题必需场景映射

| 场景 | 公开行为回归 | 实际运行入口 |
| --- | --- | --- |
| 正常完成并核实保存 | test_autonomous_delivery.py、test_review_save.py | [15秒校园方案](../../examples/eval-2026-10-29/live/campus-15/05-inspect.json)，另有30/60秒及不同受众场景 |
| 缺预算、缺截止或无效输入 | test_task_intake.py覆盖各字段及组合 | [缺输入](../../examples/eval-2026-10-29/live/missing-input/05-inspect.json)，零模型请求、零消耗 |
| 工具失败，保留成功结果并调整或停止 | test_process_resume.py、test_autonomous_delivery.py、test_save_reconcile.py | [生成中断恢复](../../examples/repair-live/recovery-delivery-01/manifest.json)；[核验失败后继续](../../examples/eval-2026-10-29/post-fix/travel-60/05-inspect.json) |
| 预算超限，有替代时调整，无可行路径时未完成 | test_strategy.py、test_regressions.py | [600分预算交付](../../examples/repair-live/budget-delivery-01/05-inspect.json)；[无可行预算](../../examples/eval-2026-10-29/live/budget-impossible/05-inspect.json) |
| 重复执行，结果、身份及费用可核对 | test_task_intake.py、test_autonomous_delivery.py | [重复创建/run/resume与资源更新操作eval](../../examples/eval-2026-10-29/operations-v2/result.json) |
| 失败步骤续跑（已选加分项） | test_process_resume.py、test_request_interruption.py、test_local_action_recovery.py | [保存副作用后exit70及新进程恢复](../../examples/eval-2026-10-29/live/save-crash-recovery/manifest.json) |

截止不足、执行上限、非法模型响应、人工稿、资源/语义更新、保存失败/未知、导出修复、有限回读与权限分别有CLI回归。新增test_strategy_task_scope.py覆盖策略旧场景污染和任务策略版本锁定；实际红测及修复后的17项定向回归保留。

## 真实模型、内容评审与失败

进度08新增9个初始场景和2个策略修复复测：7个核实保存，4个因缺输入、资料冲突、预算或截止正确停止。人工稿导入、保留外部编辑、显式修复及预算更新复用另有操作证据。45个本轮当前导出文件及历史89文件hash核对通过。

有效评审集18份：既有6对重新运行，加上3对新构造样例；9份问题稿全部检出目标缺陷，9份合格稿无must_fix误报。初版新样例的一份“合格稿”实际存在时间码矛盾，原始输入与结果保留；修正后完整重跑。样例由完整基稿派生，小样本结果不代表生产漏检率。

历史失败、eval自身的断言/参数错误及一次TLS EOF均保留。最终207项通过来自修正后的完整重跑；invariants_hold只表示协议、计量与文件一致，业务完成和内容质量仍需分别核对。

## 独立审查与CI

独立Standards/Spec结论固定在截至60249d6的记录范围，见[详细范围](07-final-review.md)。63ec7c8的策略修复由主控执行红绿测、完整回归及两个真实复测，未新增独立子Agent审查。

.github/workflows/check.yml配置已落盘，本地相应检查通过；远程CI尚未运行。
