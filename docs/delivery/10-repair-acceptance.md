# 修复与实际验收报告

日期：2026-10-29（北京时间）。原交付基线 `9d00fc9` 保留在 `integration`；本轮分支 `repair/autonomous-delivery`。

**本轮授权范围内的修复、本地验收与独立评审闭环已完成。** 真实自主交付、反馈/恢复、人工稿、校准、对照及最新增量独立复查均有实际证据。两个早期子Agent曾受用量限制，后续可用的独立Standards/Spec上下文完成了剩余复查，未用主控自测代替。断点见[进度](../progress/PROGRESS.md)。

## 实现

| 公开 interface | 现在承载的行为 |
| --- | --- |
| `run` / `resume` | 主模型持续选择检索、简报、策略、独立生成、整合、核验、意见处置、返修、保存或停止；草稿不再自动成为终点。人工稿完整正文进入当前视图。 |
| `evaluation.evaluate` | 生产核验、校准和回读对照共用一个有界协议：权限、单动作、非法/截断拒绝、报告校验、请求和回读限额、合法消息配对。 |
| `save` / `reconcile` | 同产物版本复用业务保存记录，新业务重试另计费；明确失败保留，未知先核实。全部文件核实后才提交完成；修复失败保持未知与未完成。 |
| `update` / `adopt-*` / `import-draft` | 采用实际新版正文并留旧正文历史；仅资源更新留复用依据，语义变化重新核验。导入使用任务冻结资料，不静默换外部正文。 |

主工具增加了核验、保存和原子批量意见处置。每条意见仍有自己的决定及依据；非法批次整批拒绝。已满足质量前提时，模型获得“保存当前合格稿”与“值得投入的改进”两种选择，避免为了清空 optional 反复重写。

任务固定实际系统消息、工具定义、知识与材料正文；请求记录保存采用配置。新任务默认24轮，旧任务及迁移仍保留12轮，续跑不重置。原因与影响见 [初值修订](../../evidence/repair/runtime-24.md)。模拟预算、截止和质量门槛保留。

SDK 显式直连、不读取代理配置，自动重试0、非思考、非流式、保留TLS校验。连接保留180秒的实测效果是减少握手次数；未宣称修复间歇TLS EOF。输出上限对照出现反转，因此没有据偶然成功修改生产输出上限或添加curl回退。

主循环、生成和核验在请求启动前写入请求记录；API返回前硬中断时保留 `interrupted` 和未知用量，实际重试另建记录。此项三类硬中断由真实子进程、Mock模型边界验证，不伪称真实API用量已知。

## 确定性验收与复现

从最终运行提交 `60249d6` 归档到独立目录，锁定同步依赖后 **204项测试通过，ruff通过，compileall通过**。首次离线构建缓存缺少依赖的失败也保留，随后联网按锁文件同步成功。

- [干净环境同步](../../evidence/repair/final-clean-sync.txt)
- [全量测试](../../evidence/repair/final-clean-tests.txt) / [静态检查](../../evidence/repair/final-clean-ruff.txt)
- [跨进程请求中断 red](../../evidence/repair/request-start-red.txt) / [green](../../evidence/repair/request-start-green.txt)
- [本地处置与策略缺回执恢复](../../evidence/repair-local-recovery/green.txt)
- [保存、当前有效性与Trace一致性](../../evidence/repair/save-validity-green.txt)、[事务内最终事实](../../evidence/repair/trace-save-green.txt)

| 场景 | 主要公开接口回归 |
| --- | --- |
| C-01 正常与改进 | `test_autonomous_delivery`、`test_revision_disposition` |
| C-02 输入 | `test_task_intake`，无稿也能导出报告/Trace |
| C-03 素材事实 | `test_campaign_draft`、`test_brief_loop`、`test_review_feedback_context` |
| C-04 资源 | `test_strategy`、`test_version_adoption` |
| C-05 工具失败 | `test_process_resume`、`test_autonomous_delivery` |
| C-06 保存导出 | `test_save_reconcile`、`test_review_save` |
| C-07 重复更新 | `test_task_intake`、`test_update_validity`、`test_version_adoption` |
| C-08 跨进程 | `test_process_resume`、`test_request_interruption`、`test_local_action_recovery` |
| C-09 人工稿 | `test_human_import`、`test_autonomous_delivery` |
| C-10 有界评估 | `test_evaluation_protocol`、`test_revision_disposition`、`test_calibration` |
| C-11 Context与协议 | `test_protocol_freeze`、`test_runtime_limits`、`test_evaluation_protocol` |
| C-12 隔离真实性 | `test_model_direct`、`test_task_intake`及真实产物审计 |

最新新增的审计脚本、CI和文档不改变上述运行代码。CI配置已落盘，未远程执行。

## 真实核心集合

表中为各场景结束时的事实。费用单位为**人民币分**，时间为模拟毫秒。网络失败和未完成没有删掉。正常01之后另做了人工稿导入，原场景快照仍保留。

| 样例 | 结果 | 轮数 | 模拟费用 | 模拟耗时 | API尝试 / 未知用量 |
| --- | --- | ---: | ---: | ---: | ---: |
| [正常01](../../examples/repair-live/normal-delivery-01/05-inspect.json) | v2核验通过并保存 | 14 | 490 | 37000 | 19 / 0 |
| [正常02](../../examples/repair-live/normal-delivery-02/05-inspect.json) | v2核验通过并保存 | 16 | 530 | 41000 | 21 / 0 |
| [正常03](../../examples/repair-live/normal-delivery-03/05-inspect.json) | v2核验通过并保存 | 16 | 650 | 49000 | 22 / 2 |
| [预算01](../../examples/repair-live/budget-delivery-01/05-inspect.json) | 600分内保存 | 21 | 290 | 23000 | 24 / 1 |
| [预算02](../../examples/repair-live/budget-delivery-02/05-inspect.json) | 600分内保存 | 17 | 550 | 41000 | 22 / 2 |
| [预算03](../../examples/repair-live/budget-delivery-03/05-inspect.json) | 两次核验连接失败后剩余40分，无可行核验保存路径，未完成 | 19 | 560 | 42000 | 24 / 4 |
| [预算04](../../examples/repair-live/budget-delivery-04/05-inspect.json) | 同条件定向补充，600分内保存 | 13 | 370 | 29000 | 17 / 0 |
| [生成中断续跑](../../examples/repair-live/recovery-delivery-01/manifest.json) | 实际exit70，新进程恢复并保存 | 23 | 470 | 35000 | 28 / 0 |
| [核验回执中断](../../examples/repair-live/recovery-delivery-02/manifest.json) | 实际exit70，复用核验事实并保存 | 13 | 330 | 25000 | 16 / 1 |
| [保存回执中断](../../examples/repair-live/recovery-delivery-03/manifest.json) | 实际exit70，核实复用同次保存 | 18 | 450 | 35000 | 22 / 1 |
| [资料冲突](../../examples/repair-live/insufficient-knowledge2-01/05-inspect.json) | 查证粤语非正式能力，集中澄清，needs_input | 3 | 40 | 4000 | 3 / 0 |

当前版本精确事务回执及导出组合修复后，另做[真实集成冒烟](../../examples/repair-live/post-refactor-smoke/05-inspect.json)：14轮、490分、v1 fail→v2 pass→核实保存。它作为最新版本验证单列，不替换旧失败。

正常3/3保存，恢复3/3保存；预算共4次，3次保存、1次在新增失败消耗后正确停止。后者不计入合格交付。预算输入要求比较含一次返修复核预留的路径，没有预写“先昂贵后便宜”的动作。

每个恢复样例保留 `03-before-resume.json`、恢复回执、最终inspect、账本及Trace；主模型在真实反馈后产生后续动作。模拟按时不等于真实墙钟按时，制作可用不表示已制作成片。

## 校准、返修和人工稿

旧v1校准稿标签有混杂，保留旧版；v2逐对从完整合格稿只注入目标缺陷，依据在 [标签核查](../../fixtures/calibration/2026-10-29.v2-notes.md)。教学知识、校准与正式样例分开。

[工作知识v2真实校准](../../examples/repair-calibration/knowledge2/result.json)：12份全完成，问题稿6/6检出，漏检0，合格稿必须修改误报0；14请求，77179输入 / 9134输出tokens，采得API耗时58334ms。报告保存了每次请求、消息、回读及全部结果，不从小样本推断生产可靠性。

正常01–03均有真实的 fail→意见处置→新版本→pass→保存。最终稿未继承旧通过；资料与正确内容按新版整稿复核。中间无效候选和未完成核验保留。未发现最终稿新增关键缺陷的证据；这不等于所有可能缺陷均已排除。

[人工稿真实路径](../../examples/repair-human/import-01/04-inspect.json)：由验收脚本模拟运营编辑，在已保存v2上只改标题，保留原文件副本，显式导入v3；任务输入版本仍为1，新稿待核验，主模型读取完整修改后复审并保存。新增核验80分、保存10分，没有重做生成。

## 三种方式和有限回读对照

[实际三方式实验](../../examples/repair-comparison/normal-01/suite.json)使用同模型、资料、输入、规则和相同起始资料hash，固定顺序一次性→固定流程→增强，没有给增强独享预热。

| 方式 | 实际结果 | 模拟执行费 | 可比解释 |
| --- | --- | ---: | --- |
| 一次性 | 1次真实产稿，引用正文校验失败，无合格交付 | 120分 | 原始完整候选在request-trace中；无动态恢复。未触发外部评价，不虚构评价消耗。 |
| 固定流程 | 1次真实简报提交，引用校验失败，按固定规则停止 | 0分 | 真实API用量仍记录；未启动生成不记模拟生成费，没有最终活动稿。 |
| 增强 | 经过反馈修正、核验与保存，完整交付 | 410分 | 额外请求和耗时都计入，不假定增强成本更低。 |

一次性不是手写校准坏稿，固定流程没有暗中返修。外部评价与一次性生成预算/时钟分开；150分/10秒可生成的情况有CLI回归。基线失败是实验结果，不将其改写成增强在所有任务上必胜。

主控按同一标准作非盲表达比较：一次性与增强均有“情境—操作—微调—行动引导”，表达总体**相当**；一次性较简短，增强边界说明更完整但后段文字更密。固定流程无完整稿，表达比较不适用。事实引用和最终交付硬项分别判定，不由文案观感抵消。

[有限回读成对实验](../../examples/repair-comparison/readback-01/result.json)：需要原文时，单次核验1请求/0读取，已经拦住无支持引用，但留下资料缺口；有界核验2请求/1读取，查明普通话/英语正式支持、粤语实验性的具体冲突。资料充分时两臂都是1请求/0读取。两臂都拦住坏稿，所见漏检没有下降；本次回读价值是补出确定依据，不能用意见数量声称提升。所有拒绝同等停止，额外请求仅用于成功回读后的继续核对。

缓存条件为服务缓存不可控，按实际命中数据观察；本轮同时开展其他样例，不声称冷缓存或受控性能因果结论。

## 资源、证据与剩余问题

[机器汇总](../../examples/acceptance-summary.json)纳入19个不同内容任务，包括早期失败、人工稿和最终冒烟：393次模型请求尝试，22次用量未知；已知输入8232992、输出381161 tokens，缓存命中7216256、未命中1016736。校准、外部对照评价和网络诊断另列，不混入任务模拟预算。真实金额缺可靠来源，保持未知；开发Agent Token用量不可得。有错误的请求只证明尝试，不能据配置模型名认定服务端已经处理。

全部19个任务的账本、时钟、轮数、消息配对及当前保存一致性审计成立。提交保留证据原始字节，`verify_saved_artifacts.py` 可不依赖原D盘路径或SQLite核验89个当前导出文件，均与记录hash一致。审计成立不是业务完成：8个历史/预算失败任务仍未完成，另1个needs_input。

所有独立发现均已修复并复查关闭；最终Standards在`894677f...83fff19`及`83fff19...60249d6`无新增问题，Spec确认`60249d6`无遗留问题，模式归属在`83fff19`关闭。完整范围见[评审记录](07-final-review.md)。

保留的实际边界：

1. DeepSeek直连曾间歇TLS EOF，原因未证实；全部失败保留，SDK没有隐藏重试。正常与恢复真实闭环已成功，但不宣称生产稳定性。
2. 一个模型停止说明把人民币分误称“美分”；权威账本明确为CNY_cent，数值核算正确，原说明未被删改。没有真实制作、投放或转化数据。

本地交付没有远程发布、GitHub推送或考试平台提交；仓库未配置remote。源码与证据最终提交可用 `git rev-parse HEAD` 核对；这不是远程提交完成声明。


## 最后增量的组合验收

- 五种本地动作以任务、agent、请求消息序号、调用ID及名称精确关联完整返回，与副作用同事务提交；不再按秒时间认领旧结果。schema8不猜测回填历史，旧pending无精确回执时明确中断。23项本地恢复回归和独立同秒/事务中断复查通过。
- 当前导出只关联当前产物的核验/保存；pending v2不会返回已保存v1文件。资源复用稿保留形成输入版本，同时标明当前有效输入版本，最新稿可再次人工导入；语义过期稿继续拒绝。
- 普通重复导出持续保留未导入活动稿，未知/失败保存仅导出真实执行证据；显式修复才重建活动稿。原业务阶段在纯文件修复后保留，不误改为保存未知。
- CRLF人工稿原文件不动，导出规范化行尾且连续核实达到固定点。Mock异常在生产、对照、校准及回读入口均不再误记成真实调用。以上旧组合回归的红测与绿测保留在evidence/repair。
