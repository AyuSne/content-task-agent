# 工单 11：同场景运行与盲评工作记录

基线：新协议 `16a1716fe52af7adad608989ae8f17d69769a32e`；旧协议固定 `836aa827d835e54a8e9385c18e7a3ff05ed3e600`。本工作只负责同场景、可复现运行和匿名盲评；新机制语义成对样例由另一实现者独立负责。

## 执行计划

1. 冻结同一场景和材料、记录源码状态和依赖配置，隔离每个任务的数据目录。
2. 分别运行旧、新公开 CLI；保留首次失败、续跑、退出码、耗时、脱敏输出和状态，单场景错误不阻断其他场景。
3. 按各自协议核对账本、用量、版本和保存；模拟价目不混同，实际金额未知不推算。
4. 去标签后审查事实、要求、制作和不确定性，再揭盲；保留映射和未完成记录。
5. 执行真实同场景、测试和集成同步，记录尚未验证的内容。

## 首次尝试

- 原仓库 `.venv` 启动器因中文路径启动失败；其基础 Python 遭沙箱拒绝。改用系统 Python 3.12.3 和本机依赖，未更改代理。
- 新协议 `campus-15` 首次真实运行后，旧评估脚本读取 `output.content.script.duration_s` 时抛出 `KeyError: 'duration_s'`，退出 1；新协议实际字段为 `duration_seconds`。原始结果保存在父项目 `.scratch/content-agent-redesign-worktrees/11-runs/initial-new/campus-15/`；评估器失败不等于模型或任务失败。
- 该次 `inspect` 报告 `complete`、已核实保存；19 次真实请求，输入 408405、输出 17680 token，缓存命中 353536、未命中 54869，模拟费用 490 分、模拟耗时 37000 毫秒，`run` 墙钟 95.953 秒；真实金额未知。状态不代表内容语义合格，仍待盲评。

## 本轮真实结果

本表均为 2026-10-30 本轮新建、隔离任务；不是历史运行。各列 token 均来自真实响应，未知用量请求单列；墙钟为 `run`，有恢复时斜杠后为 `resume`。状态和结构检查不等于语义合格。

| 场景 | 代码 | 状态 | 请求 | 未知用量请求 | 输入 token | 输出 token | 缓存命中 | 缓存未命中 | 模拟分 | 模拟毫秒 | 墙钟秒 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| campus-15 | 旧 | complete | 28 | 0 | 637822 | 30096 | 571264 | 66558 | 430 | 33000 | 151.056 |
| campus-15 | 新 | complete | 19 | 0 | 408405 | 17680 | 353536 | 54869 | 490 | 37000 | 95.953 |
| unsupported-cantonese | 旧 | needs_input | 20 | 0 | 393471 | 20182 | 333568 | 59903 | 440 | 32000 | 113.713 |
| unsupported-cantonese | 新 | needs_input | 5 | 0 | 55824 | 773 | 49536 | 6288 | 120 | 12000 | 9.622 |
| budget-impossible | 旧 | incomplete | 7 | 0 | 69938 | 2634 | 63104 | 6834 | 40 | 4000 | 21.154 |
| budget-impossible | 新 | incomplete | 6 | 0 | 66446 | 943 | 60288 | 6158 | 20 | 2000 | 10.610 |
| deadline-impossible | 旧 | incomplete | 8 | 0 | 87668 | 2890 | 79104 | 8564 | 40 | 4000 | 24.029 |
| deadline-impossible | 新 | incomplete | 10 | 0 | 127134 | 4747 | 119040 | 8094 | 40 | 4000 | 31.625 |
| save-crash-recovery | 旧 | complete | 18 | 0 | 366028 | 18122 | 318464 | 47564 | 370 | 29000 | 97.308 / 1.731 |
| save-crash-recovery | 新 | incomplete | 24 | 0 | 880302 | 48748 | 834432 | 45870 | 200 | 16000 | 191.259 |

总计 145 个真实请求，未把实际金额、活动成本或未获得的成本数据推断为零。旧价目为 `2026-10-29.v1`，新价目为 `2026-10-30.v2`；模拟费用只是各自规则下账本金额，不能直接作同价经济性结论。以上任务的材料目录 SHA-256 为 `8a7afcdc3896bf9cd05aeec7049d41d7434797b5a0505bdadfbb24ae991a0010`，场景 SHA-256 为 `03c5cfb682334b6773f5931e404ee47d5ab710c0e9694b82f6e4454b0b9413ae`；两代码版本的锁文件 SHA-256 均为 `70e9908f236437a8db752b38db2674af1bace69e2e85132333381e57932b46da`。实际使用系统 Python 3.12.3、openai 1.99.9、httpx 0.28.1，不冒称使用锁文件中的完整环境。模型公开配置为官方 HTTPS `deepseek-flash`，旧、新客户端均用 `httpx.Client(trust_env=False)`；未修改系统代理或关闭 TLS 验证。

随后对已提交的新源码 `52b79e830ff4766b87badaae08d30271f4334494` 和固定旧源码，使用新增配对运行器另建任务验证：`missing-input` 两侧均为 `needs_input`、零真实请求、零模拟消耗；`budget-impossible` 两侧均为 `incomplete`、检查通过。后一对的旧/新分别为 7/6 次真实请求、输入 token 66203/64373、输出 2546/922、缓存命中 58368/58496、未命中 7835/5877、未知用量请求均为 0、模拟费用 40/20 分、模拟时间 4000/2000 毫秒；运行器所计整个场景墙钟 20.422/12.094 秒，与上表只计 `run` 的口径不同。两次预算验证均完整保留，不能把后次当作前次覆盖。加上该对请求，本记录共 158 次真实请求；实际金额仍未知。配对运行器的单独清单含源码提交、干净状态、锁文件、场景和材料哈希及依赖版本。

首次新正常例的旧检查器异常保留在本记录和原始 `01`–`06` 结果中；按新协议改正检查器后，对原 `05-inspect.json` 重新检查，账本、时间、消息配对、版本、保存文件哈希及 15 秒时长检查均为 true。这只是对**同一次**原始结果的事后检查，不是第二次模型运行。四组后续新例执行时产品代码仍基于 `16a1716`，评估脚本带未提交适配差异，其脚本 SHA-256 为 `ef414913bef73702e2a26929381b3779c4bb6a9e8ca06b027ca9d1c24846c7d0`；提交前已同步纯进度提交 `dfd6ac7`。旧代码始终为固定 `836aa827`。

### 恢复失败的事实

新版 `save-crash-recovery` 未达到保存动作，未触发注入故障，也没有发生续跑；其结果是 `incomplete`、`passed=false`，不是恢复通过。原记录显示 24 个真实请求均有用量，主循环达到 24 轮上限；存在 1 个已提交脚本但没有活动方案。13 次 `submit_campaign` 中 6 次持久参数不是合法单个 JSON（解析器报 extra data），7 次外层是合法 JSON、顶层仅有 `arguments` 字符串，其内层字符串再解析同样报 extra data；schema 要求外层直接提供 `script_ref` 与 `campaign`。工具对应返回 6 次 `invalid_arguments` 和 7 次 `invalid_campaign`，并非 13 次都返回非法 JSON。未见网络错误。

13 次持久消息与原始 SQLite 记录完全一致，其中 12 次也出现在后续模型请求历史（最后一次后没有下一请求）。实现中 `DeepSeekClient` 从 SDK 的 `call.function.arguments` 取字符串，`ToolCall.as_openai` 保存该字符串，工具解析对它只做一次 `json.loads`。`tests/test_paired_sdk_arguments.py` 将真实记录的两类参数通过本地 HTTP 假对端和实际 OpenAI SDK、公开 CLI 回放：参数逐字保持，分别得到上述两种拒绝，2 项通过；这是确定性回放，不是新的远程模型响应。**本轮没有单独记录 SDK 原始响应体**，因此不能独立从网络原文证明畸形字符串最初来自服务端还是 SDK 层；代码路径和本地回放没有复现额外包装或二次解码缺陷，也不足以断言完全排除所有实现缺陷。该次真实模型交互未完成；是否需要改进反馈仍待独立评审，不能因补跑成功覆盖此记录。

旧版同场景 `02-run` 因 `after_save_effect` 退出 70；独立进程检查后 `04-resume-0` 退出 0，账本与已核实保存检查通过、保存尝试未重复。它只证明本次旧协议恢复，不证明新版恢复。

## 保全与盲评

`ticket11-paired-raw/` 包含本轮场景与材料快照、每次 CLI 的 stdout/inspect/export、逐次清单、各场景运行器日志、`result.json` 和导出文本。`copy-manifest.json` 记录前 116 份原文件与复制件 SHA-256，`driver-copy-manifest.json` 记录后来配对运行器的 51 份文件；复制时以当前进程可见敏感环境值分别扫描 UTF-8/UTF-16，均未发现命中。其后附加的测试日志不在上述清单内。该目录的 `.gitattributes` 禁止换行转换以保护原始字节。SQLite 数据库、原始目录和对应导出仍保留在父项目 `.scratch/content-agent-redesign-worktrees/11-runs/`，没有提交数据库或 `.env`。首次启动器错误仅保留本记录，首次检查器 traceback 未被重定向，已在本记录忠实说明；不能伪称其原始终端流已存盘。

`ticket11-paired-blind/blind/` 含随机编号、相同原始要求、材料和内容；无方案的一侧保留 `available=false`，其他未运行场景也保留为未知。`mapping.json` 单独保存，应由非评审人员保管；方案结构仍可能暴露新旧实现，不能保证完全盲态。本次尚未完成独立盲评，`reviews.json` 为空，尚未揭盲，也没有标签纠正。需要审查事实/范围支持、原始要求、制作可用性和不确定性，记录原文位置与争议，不以结构检查代替语义判断。

盲评包在运行器自检之前冻结，所以其 `missing-input` 两侧仍标记未知；后来的零请求自检事实另存于 `driver-selfcheck/`，不改写原盲评包。未运行 `travel-60`、`subtitle-30`、`minor-15` 的本轮同场景；真实 Skill 选择、直接创作、完整返修、引用及有据驳回的专门成对语义案例由另一实现者负责，本记录不宣称完成。范围自由概括错误可在脚本化核验 pass 后保存的既有风险未解除。运营采用、真实制作和投放无数据。

## 复现入口

准备固定旧基线与新提交的干净工作树，使用本地依赖安装；无需复制或提交配置。以下命令中的 `.env` 仅由进程读取，路径不含密钥，脚本要求官方 HTTPS 和已有密钥，子进程清除代理环境变量且不改变系统配置。新旧分别采用各自代码和价目；每个场景新建数据目录，错误或异常按场景留在输出中并继续下一个场景。

```powershell
python scripts/run_paired_eval.py --old <旧工作树> --new <新工作树> --cases fixtures/eval/2026-10-29.v1.json --materials fixtures/materials/catalog.json --env-file <本地配置路径> --output <全新输出目录> --case campus-15 --case save-crash-recovery
python scripts/blind_paired_eval.py prepare --cases <输出目录>/cases.json --materials <输出目录>/materials.json --old <输出目录>/old --new <输出目录>/new --output <全新盲评目录>
```

评审者只读取盲评目录的 `blind/`，在 `reviews.json` 为每个可用编号填写 `facts`、`requirements`、`production`、`uncertainty` 和 `remarks`（均为非空原始评语，包含证据位置及争议），再运行 `python scripts/blind_paired_eval.py reveal --package <盲评目录>`。揭盲后如发现标签错误，以 `correct --package <盲评目录> --candidate <编号> --side old|new --reason <原因>` 追加记录，不覆盖原始映射和评语。真实评审若未发生应保持空记录，不能伪造。

## 确定性检查

- 在同步 `dfd6ac7` 后，以系统 Python 运行 `PYTHONPATH=.;src python -m pytest tests tests/scope_risk_probe.py -q -o addopts='' -p no:cacheprovider --basetemp D:/AAA小影科技/.scratch/11final --tb=short`：326 项通过，376.49 秒。完整日志见 `ticket11-paired-raw/final-test.log`；此轮收集后才新增 SDK 回放测试，故不将其计入 326 项。
- 单独运行 `tests/test_paired_sdk_arguments.py`：2 项通过，日志见 `ticket11-paired-raw/sdk-replay-test.log`。本地 HTTP 假对端是测试替身，不是远程模型调用。
- 使用原仓库环境中的 Ruff 包检查 `src tests scripts` 通过。固定范围的独立代码审查尚未完成，不声称已审查通过。
