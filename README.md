# 内容任务规划 Agent

为虚构剪辑 App「帧剪」准备有证据、可核验、可续跑的内容活动方案。Python + uv + openai SDK + DeepSeek `deepseek-flash`；SQLite 是权威状态。

历史代码基线 `836aa827d835e54a8e9385c18e7a3ff05ed3e600` 的真实运行与评审证据见[新鲜eval报告](docs/delivery/11-fresh-eval.md)和[修复验收报告](docs/delivery/10-repair-acceptance.md)，它们不代表新协议已通过真实验收。当前正在实施新协议：主 Agent 直接提交完整脚本，完整方案引用、核验和交付仍以各工单的实际状态为准。

## 旧任务恢复

新任务记录 `direct-script-v1` 协议，新程序拒绝执行或修改未标记的旧任务，查询仍保留。旧任务须使用固定旧代码和任务原采用的资料、费用表及本地环境，不能由新 handler 自动迁移或续跑。可在工作区外另设旧代码工作树，例如从本仓库运行：

```powershell
git worktree add --detach ../content-task-agent-legacy-836aa82 836aa827d835e54a8e9385c18e7a3ff05ed3e600
```

在该工作树按旧锁文件同步依赖，运行时指定原任务数据目录和原有本地凭据；不要提交或打印凭据。旧资料和运行证据保持原位，模拟预算、截止与轮数不能重置。

## 安装与检查

本轮实测 Python 3.13.11、uv 0.9.27、openai 2.54.0；依赖由 `uv.lock` 锁定。

```powershell
uv sync --locked
uv run pytest -q
uv run ruff check src tests scripts
```

首次安装需要可访问依赖源；本机也可使用 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`。模型SDK显式直连，不读取代理环境或系统代理配置；TLS校验保留、非思考、非流式、SDK重试0。

真实模式把密钥放入被忽略的本地 `.env`，字段见 `.env.example`。CLI只显示密钥是否配置，不回显值。

## 历史离线完整演示

以下脚本属于旧协议，请在上节固定旧代码工作树中运行；新协议的完整演示待后续工单验收后补充。

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

`run` 持续到核实保存、澄清或可信停止；核验和保存由主模型选择。`resume` 先核实未完成/未知动作，再恢复原消息、资料、知识、账本和轮数。新任务默认24轮，每轮一个动作，失败/非法响应也计数；已有任务沿用创建时上限，历史12轮任务仍为12轮，重启不获取额度。`CTA_MAX_ROUNDS` 只决定新任务初值。

创作方向由主 Agent 根据任务选择，功能演示、用户情境、教程仅作参考；正式脚本提交与返修按任务采用的费用版本计量。新默认费用表为 `2026-10-30.v1`，已有旧费用版本保留原义。

## 查询、修改与导出

```powershell
uv run cta list
uv run cta status <task_id>
uv run cta inspect <task_id>
uv run cta pause <task_id>
uv run cta update <task_id> --budget 20
uv run cta import-draft <task_id> --file <edited-campaign.md>
uv run cta resume <task_id>
uv run cta reconcile <task_id>
uv run cta export <task_id>
uv run cta export <task_id> --repair
```

这些命令也可加 `--mode real`。先导出当前方案，在 Markdown 中保留 cta 元数据、章节和字段标签，然后编辑相应正文并显式导入；标题和活动主题须一致。为准确往返，字段内容中的换行和部分分隔符在 Markdown 源码中以 HTML 字符实体保存，显示时仍为原文；编辑时可保留或使用相应实体。脚本证据须包含材料 ID、采用版本、原文、范围和用途；素材表保留素材 ID，待制作或待获取素材可留空 ID。缺失或不明确的字段会指出位置，修正后可再次导入。导入脚本修改会创建人工来源的新脚本版本和引用它的新方案，无额外模拟生成收费；未修改脚本则复用原版本。人工稿必须重新核验并核实保存，旧评审结论不自动继承。

外部文件未导入不成为权威内容；普通导出发现外部编辑会保留文件并报告不一致，`export --repair` 显式重建权威版本。纯资源更新保留有效成果和已发生消耗，语义更新使受影响内容和评审待复核。旧协议任务的人工导入、核验及保存请使用固定旧代码。

`review` / `save` 保留为明确单步接口，完整自主验收使用 `run/resume`。纯资源更新记录语义复用依据；`adopt-knowledge` / `adopt-material` 显式采用新版正文并保留历史。详细参数见 `uv run cta --help`。

## 证据与复现

- [题面要求与证据映射](docs/delivery/01-requirements-analysis.md) / [完整证据索引](docs/delivery/06-evidence-index.md)
- [最新eval报告](docs/delivery/11-fresh-eval.md) / [机器汇总](examples/eval-2026-10-29/summary.json)：11个场景按完成、正确停止和待补充分列
- [207项测试及静态检查输出](examples/eval-2026-10-29/validation-results/README.md)：运行代码63ec7c8，本地锁定依赖环境；历史60249d6干净环境204项另有记录
- [最新人工稿](examples/eval-2026-10-29/human-import/) / [重复执行、文件修复和资源更新](examples/eval-2026-10-29/operations-v2/)
- [原6对评审重新运行](examples/eval-2026-10-29/reviewer-baseline.json) / [新增3对修正后结果](examples/eval-2026-10-29/reviewer-holdout-v2.json)：合计18份有效样例，原错误标签与失败保留
- [历史修复验收及12组场景](docs/delivery/10-repair-acceptance.md) / [历史19任务汇总](examples/acceptance-summary.json)
- [历史三方式对照](examples/repair-comparison/normal-01/suite.json) / [历史回读对照](examples/repair-comparison/readback-01/result.json)

历史独立Standards/Spec复查截至60249d6；63ec7c8的策略修复经主控回归及真实复测，审查范围见[独立评审记录](docs/delivery/07-final-review.md)。个人贡献、AI使用及可核实时间口径见[投入说明](docs/delivery/09-ai-usage.md)。

```powershell
uv run python scripts/verify_saved_artifacts.py
uv run python scripts/verify_saved_artifacts.py --root examples/eval-2026-10-29/live
uv run python scripts/verify_saved_artifacts.py --root examples/eval-2026-10-29/post-fix
uv run cta --mode real calibrate --output <new-result.json>
uv run python -m cta.comparison --mode real --method all --case fixtures/comparison/campaign-2026-10-29.v1.json --output <new-experiment-directory>
```

前三条分别离线核对历史89个、初始新集合35个和复测10个当前导出文件的原始字节，不依赖原D盘路径。后两条会真实调用模型并保留结果；托管模型可能返回不同内容。

真实/Mock使用独立数据库与导出目录，默认 `.cta-runs/<mode>/`；`--data-dir` / `CTA_DATA_DIR` 可指定根目录。本题合成资料与费用表在 `fixtures/`，两类工作知识在 `knowledge/`。实际用量和金额未知项分列；模拟按时不证明真实墙钟按时。

## 文档

[需求](docs/delivery/01-requirements-analysis.md) · [架构](docs/delivery/02-architecture.md) · [设计](docs/delivery/03-design.md) · [测试](docs/delivery/04-testing.md) · [运维](docs/delivery/05-operations.md) · [证据索引](docs/delivery/06-evidence-index.md) · [独立评审](docs/delivery/07-final-review.md) · [复盘](docs/delivery/08-retro.md) · [AI使用](docs/delivery/09-ai-usage.md)

没有远程发布或考试平台提交。本地分支为 `repair/autonomous-delivery`，最终提交以 `git rev-parse HEAD` 为准。
