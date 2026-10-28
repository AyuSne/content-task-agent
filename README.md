# 内容任务规划 Agent（content-task-agent）

为虚构视频剪辑 App 的短视频推广活动准备有依据、可核验、可续跑的内容活动方案。
实现依据：`D:/AAA小影科技/.scratch/content-task-agent-build/spec.md`；本仓库按十四张实现票逐票交付。

当前进度：**01 创建、查询及重复请求**。方案生成、核验、保存等能力由后续票加入；本页随能力扩展更新。

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

- 本票没有模型调用、方案生成、核验或保存；`in_progress` 任务的模拟轮数与费用均为 0。
- 真实模式已隔离存储，但真实 API 接入在 02 票实现并验证。
- 交付文档、证据索引与完整复现步骤由 14 票汇总。
