# 内容任务规划 Agent

为虚构剪辑 App「帧剪」准备有证据、可核验、可续跑的内容活动方案。Python + uv + openai SDK + DeepSeek `deepseek-flash`；SQLite 是权威状态。

历史代码基线 `836aa827d835e54a8e9385c18e7a3ff05ed3e600` 的真实运行与评审证据见[新鲜eval报告](docs/delivery/11-fresh-eval.md)和[修复验收报告](docs/delivery/10-repair-acceptance.md)，它们不代表新协议已通过全部真实验收。当前新协议已实现主 Agent 直接提交完整脚本、引用方案、核验及核实交付；用户决定以现有成果结项，未验证的扩展验收和已知限制如实保留。

## 旧任务恢复

新任务记录 `direct-script-v1` 协议，新程序拒绝执行或修改未标记的旧任务，查询仍保留。旧任务须使用固定旧代码和任务原采用的资料、费用表及本地环境，不能由新 handler 自动迁移或续跑。可在工作区外另设旧代码工作树，例如从本仓库运行：

```powershell
git worktree add --detach ../content-task-agent-legacy-836aa82 836aa827d835e54a8e9385c18e7a3ff05ed3e600
```

在该工作树按旧锁文件同步依赖，运行时指定原任务数据目录和原有本地凭据；不要提交或打印凭据。旧资料和运行证据保持原位，模拟预算、截止与轮数不能重置。

已初始化的新协议任务还冻结实际处理版本与工具参数契约。当前代码不适用时，状态仍可查询，执行和修改会给出不兼容原因与对应代码恢复入口；不要仅依冻结的工具定义判断 handler 语义可用。后续修改处理语义时同步更新 `src/cta/protocol.py` 的处理版本，并保留可从 Git 历史找到的原版本；任务状态的 prompt 字段显示实际采用值，未初始化时为空。

新版主请求在系统提示中预先说明工具选择、版本引用、完整方案参数和核验保存的责任；工具定义仍是参数契约。JSON 错误返回错误类别和零基位置，错误的 `arguments` 包装被拒绝并说明正确顶层字段。相同工具和参数连续重复，或同一工具出现同类失败时，在第 3、5、8 次依据持久历史追加提醒；两类计数可跨续跑保留，真实输入或资料更新后重新计算。提醒不会自动修正参数或改变任务预算、截止及轮数上限。旧处理版本 `evidence-version-roundtrip-v1` 的任务请用固定代码 `b49e8b5` 恢复。

独立核验提示要求逐条核对事实引用的范围、对应材料的适用条件和能力限制；允许准确概括，并非要求逐字复制。该指导依赖模型语义判断，不保证发现所有错误范围；通过或保存后仍需人工终审。此前处理版本 `tool-guidance-feedback-v1` 的任务请使用固定代码 `dd665dc` 恢复。

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

创作方向由主 Agent 根据任务选择，功能演示、用户情境、教程仅作参考；正式脚本提交与返修按任务采用的费用版本计量。新默认费用表为 `2026-10-30.v2`，返修条目不再显示旧独立生成调用的内部请求上限，已有旧费用版本保留原义。

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

证据行的 ID 和主张中如需输入右方括号，请在 Markdown 源码中写 `&#93;`；避免与证据行的分隔符混淆。含歧义 ID 的旧导出文件应先从当前权威方案重新导出，再进行编辑；拒绝导入不会修改原方案。

外部文件未导入不成为权威内容；普通导出发现外部编辑会保留文件并报告不一致，`export --repair` 显式重建权威版本。纯资源更新保留有效成果和已发生消耗，语义更新使受影响内容和评审待复核。旧协议任务的人工导入、核验及保存请使用固定旧代码。

`review` / `save` 保留为明确单步接口，完整自主验收使用 `run/resume`。纯资源更新记录语义复用依据；`adopt-knowledge` / `adopt-material` 显式采用新版正文并保留历史。详细参数见 `uv run cta --help`。

## 证据与复现

本次改造的工具前馈、错误纠偏与真实恢复对照见 [工具验证记录](evidence/redesign/tool-guidance-validation.md)；范围语义复测及标签争议见 [核验记录](evidence/redesign/scope-review-live-01-notes.md)。两次对照的源码、请求、状态与文件均留存；工单 11 按用户决定以现有成果结项，但模型语义质量尚未完成独立人工复核，最终改造也未完成全范围独立双轴审查。

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
# 旧三方式对照需在固定旧代码 836aa827d835e54a8e9385c18e7a3ff05ed3e600 的工作区运行
uv run python -m cta.comparison --mode real --method all --case fixtures/comparison/campaign-2026-10-29.v1.json --output <new-experiment-directory>
```

前三条分别离线核对历史89个、初始新集合35个和复测10个当前导出文件的原始字节，不依赖原D盘路径。校准和旧代码中的三方式对照会真实调用模型并保留结果；当前新代码明确拒绝旧三方式入口，历史断言保存在固定提交和 `tests/legacy/comparison-836aa827.py.txt`。有限回读对照 `--method readback` 保持可用；新旧同场景对照另见本轮验收。托管模型可能返回不同内容。

真实/Mock使用独立数据库与导出目录，默认 `.cta-runs/<mode>/`；`--data-dir` / `CTA_DATA_DIR` 可指定根目录。本题合成资料与费用表在 `fixtures/`，两类工作知识在 `knowledge/`。实际用量和金额未知项分列；模拟按时不证明真实墙钟按时。

## 文档

[需求](docs/delivery/01-requirements-analysis.md) · [架构](docs/delivery/02-architecture.md) · [设计](docs/delivery/03-design.md) · [测试](docs/delivery/04-testing.md) · [运维](docs/delivery/05-operations.md) · [证据索引](docs/delivery/06-evidence-index.md) · [独立评审](docs/delivery/07-final-review.md) · [复盘](docs/delivery/08-retro.md) · [AI使用](docs/delivery/09-ai-usage.md)

没有远程发布或考试平台提交。本地分支为 `repair/autonomous-delivery`，最终提交以 `git rev-parse HEAD` 为准。
