# 三种方式与有限回读对照入口

日期：2026-10-29（北京时间）。这是票13的实现与离线入口证据，真实对照必须另行执行后才可验收；旧 `evidence/ticket-13/comparison.json` 保留为历史描述性分析，不作为公平基线。

## 实验契约

`python -m cta.comparison` 是实验 CLI，测试通过该公开 interface 检查实际模型调用、候选、核验、失败与费用。三种方式各建立独立任务，获得相同输入、六份必要资料全文（M-001 至 M-006）、创作知识、同一版本的合成资源表与 DeepSeek 配置。各方式保留本身的控制方式：

- `oneshot`：真实模型一次生成完整方案，按直接生成120分计量；随后调用生产独立核验做共同质量评价，评价80分与自身生成费用分列。未执行业务保存；动态反馈和恢复不适用。
- `fixed`：真实模型提交简报，固定功能演示/直接生成策略，使用生产脚本生成工具，再由真实模型整合完整方案；生产核验与保存按固定路径执行。工具/核验/保存失败即停止，保留未完成，无动态返修。故障注入沿生产工具；只有同一工具实际执行时故障才适用。
- `enhanced`：相同初始资料后交由真实主循环判断、生成、核验、返修和保存。不会以已知校准坏稿或预写候选替代任何生成。

`--method all` 固定按 oneshot → fixed → enhanced 顺序连续运行并记录顺序、起止时间和每次实际缓存命中/未命中。未主动预热任何一方；服务缓存不可控，因此不声称冷缓存。单独方法可用 `--order` 记录外部预登记的轮转位置。

输出 `suite.json`、各方法的 `result.json`/`inspect.json`/`request-trace.json` 与原生方案导出，均关联真实任务。预算、模拟耗时与真实 API 用量分开；未知逐请求保留，不补成已核实零。未生成候选也保存失败原因和尝试。业务保存通过与外部评价通过分别报告。

## 复现

从应用仓库运行；真实密钥由本地 `.env` 加载，不写入命令或结果：

```powershell
uv run python -m cta.comparison --mode real --method all --case fixtures/comparison/campaign-2026-10-29.v1.json --data-dir evidence/repair-comparison/data --output evidence/repair-comparison/real-campaign
uv run python -m cta.comparison --mode real --method readback --case fixtures/comparison/readback-2026-10-29.v1.json --output evidence/repair-comparison/real-readback
```

三种方式使用同一资源规则；真实模型、配置或资料变化后不得把旧稿混作同批受控结果。固定/增强可在相同 `CTA_FAIL_SAVE=1` 下另跑故障子集，一次性方案未提供业务保存，故障恢复标不适用。实验 JSON 记录实际环境故障注入。

## 有限回读

留出样例 `fixtures/comparison/readback-2026-10-29.v1.json` 独立于六对校准与教学样例。两对分别提供仅登记语言能力ID、已提供完整语言边界原文；同一对中的完整方案、要求、片段与核验知识相同。单次只允许一次 `report_review`；有界评估允许最多3次请求和2次本任务原文读取。第二对轮转顺序，先有界再单次。

正确标签仅在本地样例/结果中保留，不进入模型输入。完整实际消息、工具调用/结果、逐次请求信息/用量/耗时与未完成均留证；完成语义复用 `review.review_report_status`。结果可能显示收益相当、增加开销或误报，均应如实报告。实验不直接改稿，整体交付影响由查实问题及额外核验成本分析。

## Red → green

- `red-oneshot.txt`：CLI 模块不存在。实现后通过真实模型接口注入获得带唯一主题的新候选，核验和200分总模拟消耗可观察，其中80分为外部评价。
- `red-readback.txt`：入口不接受readback。实现后断言两臂同稿、请求分别1/2次、实际回读1次，隐藏标签未进请求，原文结果在trace中。
- `red-no-output.txt`：预算拒绝后导出无候选抛错导致结果丢失。修复后可信停止并写出result/inspect，未执行请求不计费。
- 固定流程回归验证真实生成与整合共3次内容请求，核验失败后没有第二稿或业务保存，未伪报完成。

定向4个回归通过（`green.txt`）；ruff通过。此记录不证明真实内容质量或对照效果。
与主自主闭环和持久轮数修复集成后，9条定向回归通过，见 `integrated-green.txt`；集成前沿保留在Git父提交中。
