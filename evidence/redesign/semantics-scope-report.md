# 工单 11：独立核验范围语义探针

## 范围和来源

- 基线：`16a1716fe52af7adad608989ae8f17d69769a32e`。本记录仅覆盖范围语义探针，不是工单 11 的完整验收。
- [`redesign-semantics-scope-v1.json`](../../fixtures/eval/redesign-semantics-scope-v1.json) 派生自旧校准集第一对的合格稿；两稿只改变 `campaign.evidence[0].scope`。标签和依据附在样例文件，校准 CLI 不把标签传给模型。
- `M-001` 材料范围为「iOS/Android 4.2 及以上」，问题稿的 E1 范围却声称「所有 iOS 和 Android 版本，包括 4.2 以下」；合格稿沿用材料范围。两稿正文、引文和制作路径不变。问题是证据范围，不是字幕本身。
- 校准入口构造独立核验上下文，包含待核验方案、原始要求和材料，但 `brief` 为 null；源方案来自旧校准稿，不是通过新协议提交的产物，不含新协议各处版本引用。因此这次只证明该入口对这个具体样例的判断，不能证明新协议生产核验、保存门禁、版本返修或其他范围措辞已通过。

## 实际结果

- 原始文件：[`semantics-scope-real-01.json`](semantics-scope-real-01.json)，保存完整上下文、工具定义、响应、工具反馈、逐请求记录和用量。模式为真实 DeepSeek SDK 直连，模型 `deepseek-flash`，非思考、非流式、SDK 自动重试 0；代码使用 `httpx.Client(trust_env=False)` 和默认 TLS 校验。未使用 7890 或改变代理。
- 错误范围：完整核验，`fail`；F1 为 `must_fix`，明确定位 `campaign.evidence[E1].scope`，引用 M-001 的 4.2 及以上范围并指出扩大至 4.2 以下。它准确命中预设缺陷；另外两条为可选建议。
- 合法范围：完整核验，`pass`，没有必修问题，三条可选建议。建议中关于字幕限定和制作先后顺序属于可讨论建议，不等于对合法范围的必修误报。
- 两次请求均成功，无读取材料工具调用；每次用时分别 6136、6325 ms，墙钟 12.881 秒。输入 10598、输出 1866、合计 12464 tokens，缓存命中 5248、未命中 5350；未知用量请求 0。真实金额未知；校准不创建任务，不消耗任务模拟预算。
- 未发生标签纠正，也无失败请求；原始输出没有删除或改写评审建议。单次派生样例不能推断泛化率，也不代替真实主模型的处置判断。

## 复现

在本 worktree 使用 Python 3.12+ 和已安装依赖，令 `PYTHONPATH=src`、`PYTHONUTF8=1`，生成新文件：

```powershell
python scripts/eval_redesign_semantics.py prepare fixtures/eval/redesign-semantics-scope-v1.json
python -c "from cta.config import load_env_file; from pathlib import Path; from cta.cli import main; load_env_file(Path(r'D:\AAA小影科技\content-task-agent\.env')); raise SystemExit(main(['--mode','real','calibrate','--pairs','fixtures/eval/redesign-semantics-scope-v1.json','--output','evidence/redesign/semantics-scope-real-NEW.json']))"
```

不要复用已有输出名，以免覆盖原始记录。产品配置加载 `.env` 时不覆盖已有环境变量；模型输出可能含业务数据，发布前仍需检查脱敏。运行会产生实际 API 费用；该命令走公开 CLI 的 `main`，不是 Mock。原有旧代码恢复入口为 `836aa827d835e54a8e9385c18e7a3ff05ed3e600`，不得用新协议重放旧任务。

## 尚未验证

真实主模型对真实缺陷的错误驳回、对误报的有据驳回、可行版本引用及完整返修均未在此探针中执行；现有程序仅验证驳回依据可定位，不能把该事实当作语义正确。生产 CLI 的范围拦截风险仍按 `spec-evidence-review.md` 保留。新旧完整任务对照、整体质量与恢复开销属于并行工作流，不能以本结果代替；不得将工单 11 标为完成。
