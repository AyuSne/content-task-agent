# 内容任务规划 Agent

为虚构剪辑 App「帧剪」准备有证据、可核验、可续跑的内容活动方案。Python + uv + openai SDK + DeepSeek `deepseek-flash`；SQLite 是权威状态。

当前：自主生成→核验→返修→核实保存已真实跑通。新增9个场景及2个修复复测均达到预期，覆盖15/30/60秒、不同受众、约束停止及保存中断；本轮修复策略旧场景污染与任务策略版本加载问题，见[新鲜eval报告](docs/delivery/11-fresh-eval.md)。历史真实集合及固定范围的独立Standards/Spec记录保留在[修复验收报告](docs/delivery/10-repair-acceptance.md)。

## 安装与检查

本轮实测 Python 3.13.11、uv 0.9.27、openai 2.54.0；依赖由 `uv.lock` 锁定。

```powershell
uv sync --locked
uv run pytest -q
uv run ruff check src tests scripts
```

首次安装需要可访问依赖源；本机也可使用 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`。模型SDK显式直连，不读取代理环境或系统代理配置；TLS校验保留、非思考、非流式、SDK重试0。

真实模式把密钥放入被忽略的本地 `.env`，字段见 `.env.example`。CLI只显示密钥是否配置，不回显值。

## 离线完整演示

```powershell
$env:CTA_MODEL_SCRIPT = (Resolve-Path examples/offline/model-script.json).Path
$task = uv run cta create --new --topic '校园剪辑挑战' --budget 10 --deadline '2026-10-30T12:00:00+08:00' --audience '大学生' --requirement '突出自动卡点' | ConvertFrom-Json
uv run cta run $task.task_id
uv run cta inspect $task.task_id
```

这是模型边界脚本注入，明确为Mock，只验证流程与确定性规则。[实际离线结果](examples/offline/actual-result.json)不证明真实决策或语义质量。

## 真实运行与续跑

```powershell
$task = uv run cta --mode real create --new --topic '校园社团招新自动卡点推广' --budget 10 --deadline '2026-10-30T12:00:00+08:00' --audience '18-24岁大学生' --requirement '含一条30秒主视频，突出自动卡点和微调' | ConvertFrom-Json
uv run cta --mode real run $task.task_id
uv run cta --mode real status $task.task_id
uv run cta --mode real resume $task.task_id
```

预算单位为元，只约束合成费用表计量的方案准备工具费；制作、采购与投放费用另列。示例截止属于模拟业务时钟，默认起点为2026-10-29北京时间00:00；暂停不推进模拟时间。

`run` 持续到核实保存、澄清或可信停止；核验和保存由主模型选择。`resume` 先核实未完成/未知动作，再恢复原消息、资料、知识、账本和轮数。新任务默认24轮，每轮一个动作，失败/非法响应也计数；旧任务保留原12轮，重启不获取额度。`CTA_MAX_ROUNDS` 只决定新任务初值。

新任务默认采用通用策略表 `2026-10-29.v2`。`CTA_STRATEGY_VERSION` 只选择新任务初始策略版本；已有任务的查询、比较与执行沿用已记录版本，旧v1资料保留。

## 查询、修改与导出

```powershell
uv run cta list
uv run cta status <task_id>
uv run cta inspect <task_id>
uv run cta pause <task_id>
uv run cta strategies <task_id>
uv run cta update <task_id> --budget 20
uv run cta import-draft <task_id> --file <edited-campaign.md>
uv run cta resume <task_id>
uv run cta reconcile <task_id>
uv run cta export <task_id>
uv run cta export <task_id> --repair
```

这些命令也可加 `--mode real`。人工导入关联当前基稿，只修改正文形成新产物版本；旧通过不继承。过期基稿拒绝覆盖。外部文件未导入不成为新任务内容；发现文件与保存记录不符时保持未完成，显式修复从权威内容重建。

`review` / `save` 保留为明确单步接口，完整自主验收使用 `run/resume`。纯资源更新记录语义复用依据；`adopt-knowledge` / `adopt-material` 显式采用新版正文并保留历史。详细参数见 `uv run cta --help`。

## 证据与复现

- [验收报告与12组场景](docs/delivery/10-repair-acceptance.md)
- [机器汇总](examples/acceptance-summary.json)：合格交付、正确停止、早期失败分列
- [真实核心样例](examples/repair-live/) / [人工稿](examples/repair-human/import-01/)
- [六对真实校准](examples/repair-calibration/knowledge2/result.json)
- [三方式实际对照](examples/repair-comparison/normal-01/suite.json) / [回读对照](examples/repair-comparison/readback-01/result.json)

```powershell
uv run python scripts/verify_saved_artifacts.py
uv run cta --mode real calibrate --output <new-result.json>
uv run python -m cta.comparison --mode real --method all --case fixtures/comparison/campaign-2026-10-29.v1.json --output <new-experiment-directory>
```

第一条离线核对89个存档导出的原始字节，不依赖原D盘路径。后两条会真实调用模型，保留全部结果；不保证托管模型内部实现或随机输出不变。

真实/Mock使用独立数据库与导出目录，默认 `.cta-runs/<mode>/`；`--data-dir` / `CTA_DATA_DIR` 可指定根目录。本题合成资料与费用表在 `fixtures/`，两类工作知识在 `knowledge/`。实际用量和金额未知项分列；模拟按时不证明真实墙钟按时。

## 文档

[需求](docs/delivery/01-requirements-analysis.md) · [架构](docs/delivery/02-architecture.md) · [设计](docs/delivery/03-design.md) · [测试](docs/delivery/04-testing.md) · [运维](docs/delivery/05-operations.md) · [证据索引](docs/delivery/06-evidence-index.md) · [独立评审](docs/delivery/07-final-review.md) · [复盘](docs/delivery/08-retro.md) · [AI使用](docs/delivery/09-ai-usage.md)

没有远程发布或考试平台提交。本地分支为 `repair/autonomous-delivery`，最终提交以 `git rev-parse HEAD` 为准。
