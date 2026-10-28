# 内容任务规划 Agent（content-task-agent）

为虚构视频剪辑 App 的短视频推广活动准备有依据、可核验、可续跑的内容活动方案。
实现依据：`D:/AAA小影科技/.scratch/content-task-agent-build/spec.md`；本仓库按十四张实现票逐票交付。

当前进度：**07 资源和工具反馈后的策略调整**。已可完成完整正常路径：简报 → 策略比较与选择 → 完整方案草稿 → 独立内容核验 → 核实保存 → Markdown/JSON/Trace 导出；策略选择按版本化模式表比较完整剩余路径，预算/截止不足或生成失败时可改走可行替代，无路径时明确停止并列出缺口。意见处置与返修由 06 加入。本页随能力扩展更新。

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
uv run cta review <task_id>       # 独立上下文核验当前完整方案（最多 3 次请求/2 次回读）
uv run cta save <task_id>         # 核对核验、阻塞项、预算与截止后保存并核实文件
uv run cta export <task_id>       # 从 SQLite 重新导出方案、报告与 Trace
uv run cta strategies <task_id>   # 只读比较创作方向与完整剩余路径（不计费、不执行）
uv run cta inspect <task_id>      # 简报、计划、知识、消息、工具结果、尝试、账本、逐请求用量
```

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

## 当前范围与限制

- 当前可完成正常核验与保存；存在 must_fix、核验不完整、关键阻塞、预算/截止不足或保存失败时明确未完成。
- 策略比较与选择使用 `fixtures/strategy/2026-10-29.v1.json` 与 `fixtures/pricing/2026-10-29.v1.json`；表值、候选数与失败规则固定留版本，可用 `CTA_STRATEGY_VERSION` / `CTA_PRICING_VERSION` 指向新版本文件。
- 估算、预留与实际消耗分开：只有账本 `nature=incurred` 计入预算；真实模型金额因缺少可靠单价而保持未知，活动费用未涉及为不适用。
- 意见处置、返修复审与评估中断恢复由 06 加入；保存未知核实与导出修复由 08 加入。
- 真实模式已接入 DeepSeek 并执行过简报、草稿与核验的真实运行；07 的确定性样例均为 Mock 注入，未新增真实 API 运行。真实/Mock 的数据库、导出目录与请求身份独立。
- 预算只约束模拟工具费用；真实 Token 用量另记。模拟按时不代表真实墙钟按时。
- 交付文档、证据索引与完整复现步骤由 14 票汇总。
