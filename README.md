# 内容任务规划 Agent（content-task-agent）

为虚构视频剪辑 App 的短视频推广活动准备有依据、可核验、可续跑的内容活动方案。
实现依据：`D:/AAA小影科技/.scratch/content-task-agent-build/spec.md`；本仓库按十四张实现票逐票交付。

当前进度：**02 资料检索、业务简报与关键缺口**。已可通过 CLI 运行主循环、形成经证据校验的业务简报；完整方案生成、核验、保存由 03/04 加入。本页随能力扩展更新。

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
uv run cta inspect <task_id>      # 简报、计划、知识、消息、工具结果、尝试、账本、逐请求用量
```

- `run` 显式执行或续跑；默认最多 12 轮，每轮只接受一个已校验动作。失败、非法响应、多调用与截断同样计数。
- 业务简报通过程序校验后阶段为 `brief_ready`；再次 `run` 不会重复执行或重复计费。
- 工具启动即按版本化费用表记模拟费用与模拟耗时；预算或模拟截止不足时在执行前拒绝，不记消耗。
- 真实模型请求逐次写入 `llm_requests`（用量、缓存命中/未命中、耗时、错误）；未知用量保持未知，不补零。
- Mock 离线演示通过 `CTA_MODEL_SCRIPT=<json>` 注入模型响应，仅用于测试/复现，不是产品对外能力。

重复与显式新建：

- 同一模式内，规范化后的主题、预算、截止时间、受众、创作要求完全一致时，`create` 返回原任务（`"duplicate": true`），不触发新执行。
- 加 `--new` 显式新建同内容任务，获得独立任务 ID 和统计单位。
- 状态为 `in_progress` 只表示任务已创建且未完成，不代表方案已生成或保存。

数据位置：

```text
.cta-runs/mock/tasks.db      # Mock 模式权威 SQLite
.cta-runs/real/tasks.db      # 真实模式权威 SQLite
.cta-runs/<mode>/exports/    # 后续导出位置
```

`CTA_DATA_DIR` 可覆盖 `.cta-runs` 根目录（测试使用临时目录）；`CTA_SIM_START` 只用于测试注入模拟起点。

## 测试

```powershell
uv run pytest -q
```

测试通过 CLI 公开接口断言，跨进程用例使用真实落盘 SQLite；模型、工具与时钟在系统边界注入。

## 当前范围与限制

- 当前完成到业务简报阶段；尚未生成完整活动方案、未做内容核验与保存，因此任务不会进入 `complete`。
- 真实模式已接入 DeepSeek 并执行过最小真实运行；真实/Mock 的数据库、导出目录与请求身份独立。
- 预算只约束模拟工具费用；真实 Token 用量另记。模拟按时不代表真实墙钟按时。
- 交付文档、证据索引与完整复现步骤由 14 票汇总。
