# 内容任务规划 Agent（content-task-agent）

为虚构视频剪辑 App 的短视频推广活动准备有依据、可核验、可续跑的内容活动方案。
实现依据：`D:/AAA小影科技/.scratch/content-task-agent-build/spec.md`；本仓库按十四张实现票逐票交付。

当前进度：**01-11 已集成**（含 05 跨进程续跑、06 处置返修、07 策略比较、08 保存核实、09 输入更新、10 人工稿导入、11 评审校准）。已可完成完整正常路径：简报 → 策略比较与选择 → 完整方案草稿 → 独立内容核验 → 核实保存 → Markdown/JSON/Trace 导出；支持真实新进程显式续跑与暂停、意见处置与返修、资源/输入更新、人工稿导入和评审校准。本页随能力扩展更新。

## 环境

- Python 3.12+（当前锁定 3.13.11）
- [uv](https://docs.astral.sh/uv/) 管理虚拟环境与依赖
- 网络依赖安装如在本机需要代理，先设置 `HTTP_PROXY` / `HTTPS_PROXY`（本环境为 `http://127.0.0.1:7890`）

```powershell
$env:UV_CACHE_DIR = "D:\AAA小影科技\content-task-agent\.uv-cache"
$env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
uv sync
```

`uv.lock` 锁定实际依赖。真实密钥只放在项目内 `.env`（已被忽略），字段见 `.env.example`：

```text
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

`cta status` 等输出只显示密钥是否已配置（`set`/`missing`），不会回显值。

## CLI

命令默认输出 JSON；加 `--format text` 输出简短文本。`--mode mock|real` 选择模式（默认 `mock`），两种模式使用独立数据库、输出目录和请求身份。

创建任务（完整输入）：

```powershell
uv run cta create --topic "校园剪辑挑战" --budget 10 --deadline "2026-10-30T12:00:00+08:00"
uv run cta create --topic "校园剪辑挑战" --budget 10 --deadline "2026-10-30T12:00:00+08:00" --audience "大学生" --requirement "突出自动卡点"
```

- `--budget` 单位为元，最多两位小数，必须大于 0。
- `--deadline` 使用 ISO-8601；只给日期按北京时间当日 23:59:59 解释；无时区按北京时间解释。截止时间必须晚于模拟起点（默认 `2026-10-29T00:00:00+08:00`）。
- 主题、预算、截止时间缺失或无效时仍会持久保存一次输入，状态为 `needs_input`，`validation.errors` 给出逐字段原因。

查询与列表：

```powershell
uv run cta status <task_id>
uv run cta list
```

执行主循环与查看执行事实：

```powershell
uv run cta run <task_id>          # 真实模式调用 DeepSeek；Mock 模式需注入脚本
uv run cta resume <task_id>       # 显式续跑：先核实进行中/缺回执/未知动作，再继续
uv run cta pause <task_id>        # 暂停；模拟时钟与已用轮数不推进
uv run cta review <task_id>       # 独立上下文核验当前完整方案（最多 3 次请求/2 次回读）
uv run cta save <task_id>         # 核对核验、阻塞项、预算与截止后保存并核实文件
uv run cta reconcile <task_id>    # 核实保存记录；必要时从权威内容修复文件
uv run cta export <task_id>       # 从 SQLite 重新导出方案、报告与 Trace
uv run cta export <task_id> --repair  # 修复缺失/不一致的导出文件
uv run cta strategies <task_id>   # 只读比较创作方向与完整剩余路径（不计费、不执行）
uv run cta update <task_id> --budget 20 --audience "研究生"  # 新输入版本
uv run cta adopt-knowledge <task_id> --knowledge-id campaign-creation --version 2026-10-30.v1
uv run cta adopt-material <task_id> --version 2026-10-30.v1
uv run cta import-draft <task_id> --file <edited-campaign-v1.md>
uv run cta calibrate --pairs fixtures/calibration/2026-10-29.v1.json --output <result.json>
uv run cta inspect <task_id>      # 简报、计划、消息、恢复事实、快照、资料/知识版本、尝试、账本、逐请求用量
```

- `run` 执行主循环；`resume` 是显式续跑入口，会先核实未完成调用与结果未知动作，追加恢复事实与当前快照。暂停后的任务必须 `resume` 才继续；普通 `run` 在暂停时只返回 `paused`。
- `resume` 复用已保存的消息、检索结果、简报、草稿、评审、资料与知识正文；已登记启动的尝试不重复收费，实际重试才新增尝试。达到轮数上限、模拟截止或预算耗尽后，续跑不获得新额度；模型不可用时保留进度并明确未完成，后续由显式续跑重试。
- 保存记录与文件无法核实时进入 `save_unknown` 并安全停止，不盲目重放；完整保存未知核实与导出修复由 08 交付。
- 故障注入只用于测试：`CTA_FAULT_CRASH=<测试注入点>` 让真实子进程在指定系统边界中断，不是面向用户的能力；Mock 脚本 `CTA_MODEL_SCRIPT` 仍是离线模型边界的测试注入。
- `run` 显式执行或续跑；默认最多 12 轮，每轮只接受一个已校验动作。失败、非法响应、多调用与截断同样计数。
- 第一次 `run` 形成业务简报（`brief_ready`）；再次 `run` 进入方案草稿：调用 `generate_script` 在独立上下文生成候选，再由主 Agent 整合并 `propose_campaign`（`draft_ready`）。阶段完成后再 `run` 不会重复执行或重复计费。
- `compare_strategies` 按同一版本化费用表比较功能演示/用户情境/教程的表达目标、资料需求、风险与完整剩余路径（已知必要检索、生成、核验、保存，可选返修复核）；`choose_strategy` 记录方向、模式、理由与改进预期收益，不可行时拒绝并返回可行替代。二者均不执行、不计费。
- `review` 在独立上下文核验完整方案；`pass + full` 且无 must_fix 才能继续。`save` 在满足版本、核验、阻塞项、预算与截止条件后写文件并核实哈希，状态才进入 `complete`；否则返回明确未完成。
- 工具启动即按版本化费用表记模拟费用与模拟耗时；预算或模拟截止不足时在执行前拒绝，不记消耗。生成前额外核对生成+核验+保存的完整剩余路径。
- `resources` 分类展示：模拟工具费用（单位 CNY_cent、来源 pricing 版本、已发生/估算/预留分开）、真实模型用量（请求/token，金额未知）、活动费用（未涉及为不适用，涉及时金额未知）；未知与不适用分列，估算和预留不进入 `sim_spent_cents`。
- 真实模型请求逐次写入 `llm_requests`（用量、缓存命中/未命中、耗时、错误）；未知用量保持未知，不补零。
- 停止时 `stops` 与 `gaps` 保留具体缺口、影响和既有成果；轮数/截止/模型不可用等程序停止也会留痕。
- `inspect` 提供产物版本、独立生成/核验上下文、消息、工具结果、尝试、账本、逐请求用量、估算/选择与保存记录。
- Mock 离线演示通过 `CTA_MODEL_SCRIPT=<json>` 注入模型响应，仅用于测试/复现，不是产品对外能力。`CTA_FAIL_SEARCH=<N>` 仅用于测试注入前 N 次检索失败。

重复与显式新建：

- 同一模式内，规范化后的主题、预算、截止时间、受众、创作要求完全一致时，`create` 返回原任务（`"duplicate": true`），不触发新执行。
- 加 `--new` 显式新建同内容任务，获得独立任务 ID 和统计单位。
- 状态为 `in_progress` 只表示任务已创建且未完成，不代表方案已生成或保存。

数据位置：

```text
.cta-runs/mock/tasks.db      # Mock 模式权威 SQLite
.cta-runs/real/tasks.db      # 真实模式权威 SQLite
.cta-runs/<mode>/exports/    # 按任务 ID 存放方案、报告与 Trace
```

`CTA_DATA_DIR` 可覆盖 `.cta-runs` 根目录（测试使用临时目录）；`CTA_SIM_START` 只用于测试注入模拟起点。

## 测试

```powershell
uv run pytest -q
```

测试通过 CLI 公开接口断言，跨进程用例使用真实落盘 SQLite；模型、工具与时钟在系统边界注入。

## 文档

- [需求分析](docs/delivery/01-requirements-analysis.md)
- [架构](docs/delivery/02-architecture.md) / [设计](docs/delivery/03-design.md)
- [测试与验收](docs/delivery/04-testing.md)
- [运行、复现与运维](docs/delivery/05-operations.md)
- [证据索引](docs/delivery/06-evidence-index.md)
- [最终双轴评审](docs/delivery/07-final-review.md)
- [复盘](docs/delivery/08-retro.md)

## 当前范围与限制

- 当前可完成正常核验与保存，并支持主循环/生成中断后的真实新进程续跑；暂停不推进模拟时钟，续跑继续原进度与额度。
- 意见处置、返修复审与评估中断恢复由 06 加入；保存未知的完整核实、回执恢复与导出修复由 08 加入。
- 真实模式已接入 DeepSeek 并执行过简报、草稿、核验与评审校准的真实运行；05/07/08/09/10 的确定性样例均为 Mock 注入，未新增真实 API 运行。真实/Mock 的数据库、导出目录与请求身份独立。
- **真实限制**：12 轮内真实任务最远到“草稿 v1 + 评审 fail”，没有完成“评审通过→保存”；真实返修也未跑通。详见 `evidence/ticket-12/README.md`。
- 预算只约束模拟工具费用；真实 Token 用量另记。模拟按时不代表真实墙钟按时。
- 交付文档、证据索引与完整复现步骤由 14 票汇总。
