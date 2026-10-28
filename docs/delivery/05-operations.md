# 运行、复现与运维（交付文档 05）

## 环境

- Python 3.13.11，uv 0.9.27；`uv.lock` 锁定依赖。
- 虚拟环境：`content-task-agent/.venv`；命令用 `.venv/Scripts/python.exe -m cta`。
- 外部网络需代理时设置 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`（本环境 `http://127.0.0.1:7890`）。
- 真实模式：`content-task-agent/.env` 的 `DEEPSEEK_API_KEY`；不要提交/打印 Key。

## 安装

```powershell
cd D:\AAA小影科技\content-task-agent
$env:UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
uv sync
uv run pytest -q
```

## 常用命令（Mock）

```powershell
$data = "D:\AAA小影科技\.scratch\content-task-agent-build\run-state\demo"
$env:CTA_DATA_DIR = $data
uv run cta create --topic "校园剪辑挑战" --budget 10 \
  --deadline "2026-10-30T12:00:00+08:00" --audience "大学生" --requirement "突出自动卡点"
uv run cta run <task_id>          # 简报
uv run cta run <task_id>          # 草稿
uv run cta review <task_id>
uv run cta save <task_id>
uv run cta export <task_id>
uv run cta inspect <task_id>
```

## 续跑/暂停

```powershell
uv run cta pause <task_id>        # 模拟时钟不推进
uv run cta resume <task_id>       # 先核实未完成/未知动作
```

## 保存未知/导出修复

```powershell
uv run cta reconcile <task_id>     # 核实保存记录；从权威内容修复文件
uv run cta export <task_id> --repair
```

## 输入/知识/资料/人工稿

```powershell
uv run cta update <task_id> --budget 20 --audience "研究生"
uv run cta adopt-knowledge <task_id> --knowledge-id campaign-creation --version 2026-10-30.v1
uv run cta adopt-material <task_id> --version 2026-10-30.v1
uv run cta export <task_id>
uv run cta import-draft <task_id> --file <编辑后 campaign-v1.md>
```

## 真实模式

```powershell
$env:HTTP_PROXY="http://127.0.0.1:7890"
$env:HTTPS_PROXY="http://127.0.0.1:7890"
$env:ALL_PROXY="http://127.0.0.1:7890"
$env:CTA_DATA_DIR="...\run-state\real-trial"
uv run cta --mode real create ...
uv run cta --mode real run <task_id> --max-rounds 12
uv run cta --mode real review <task_id>
```

- 真实/Mock 数据目录、导出、身份完全分开。
- 真实金额保持 `unknown`；模拟费用单列。
- 12 轮为每任务累计硬上限；触顶保留成果并标 `incomplete`。

## 交付导出

`save`/`export` 在 `<data-dir>/<mode>/exports/<task_id>/` 写：

- `campaign-vN.md`（含 cta 元数据）、`campaign-vN.json`
- `execution-report.md`、`trace.jsonl`
- `brief-vN.json`

`execution-report.md` 是给人看的；JSONL 是逐事件 Trace；两者与 SQLite 状态一致。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `needs_input` | 看 `validation.errors`，重新 create 或 `update` |
| 预算/截止被拒 | 看 `run`/工具返回 reason；预算不足走替代或停止 |
| 评审 fail | 看 findings/dispositions；accepted 返修后重新 review |
| `review_incomplete` | 重新 review；不算通过 |
| `save_unknown` | `reconcile` 核实/修复；不要重复 `save` |
| 导出文件缺失/不一致 | `export --repair` 或 `reconcile` |
| 输入已变更 | `run` 重新形成简报/草稿/review；旧产物保存被拒 |
| 人工稿 stale_base | 重新 export 最新版再改；不覆盖 |
| 真实模式 401/网络 | 检查 Key/代理；不要打印 Key；错误会保留进度 |

## 隐私与费用

- `.env` 已在 `.gitignore`；证据只显示 `api_key: set`。
- 真实 token 用量逐请求记录，金额未知；模拟费用按价格表，预估/预留不进入花费。
- 不保存用户隐私数据；资料均为合成 fixture。

## 已知运行限制

- 真实模型在 12 轮内完成“草稿→评审通过→保存”未达成；最远为草稿 + 评审 fail。
- 真实返修未跑通；确定性返修有证据。
- 人工稿只接受固定章节；无自动合并/监视。
