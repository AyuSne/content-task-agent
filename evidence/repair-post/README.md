# 真实模型 GET / POST 差分诊断

日期：2026-10-29。起点 `48407bd`，分支 `repair/post-diagnosis`。用户明确授权真实模型请求，不设本项目 API/Token 额度；本实验有限执行，SDK零重试，全部记录。

## 错误可观察性

实际SDK在网络连接失败时原来只返回 `APIConnectionError: Connection error.`，丢失底层异常。公开 `DeepSeekClient.complete` 回归使用本地真实HTTP端点接收请求后断开，修复前缺少 `RemoteProtocolError`（`error-chain-red.txt`），修复后保留SDK到HTTP传输的脱敏异常链（`error-chain-green.txt`）。既有错误字段仍有800字符上限；常见3层网络原因都可完整保留。TLS校验、直连、超时与零重试保持原配置。

## 真实有限采样

复现入口：`scripts/diagnose_model_post.py --env-file <本地忽略的.env> --output <新证据JSON>`。密钥仅由配置加载至进程，未进入请求元数据、输出或日志。脚本清代理环境且所有客户端 `trust_env=False`，未使用7890或修改系统代理/TUN。

固定顺序连续三组：无鉴权 GET /models → 最小实际模型 POST → 主提示词及完整真实工具定义 POST。每次新建HTTP/SDK客户端，不预设缓存，不重试。原始每次开始/结束、耗时、响应模型、用量、错误及发出请求的body长度/哈希保存在 `real-python-sequence.json`。

- 第一条GET：5.205秒，TLS握手EOF，异常链为 httpx.ConnectError ← httpcore.ConnectError，底层文本 `SSL: UNEXPECTED_EOF_WHILE_READING`；无模型用量。
- 后续GET两条：正常无鉴权401。
- 最小POST：3/3成功，每条输入8、输出1 Token。
- 主提示词及全部工具定义POST：3/3成功，每条输入4414，输出85/74/94 Token；模型实际选择了工具动作，没有执行对应业务工具。
- 采样说明TLS连接会间歇失败，但本次未观察到POST/body特有失败。GET成功也不能替代真实POST验证；六条POST均记录了实际模型用量。

## 可执行结论

本次可以继续现有SDK直连路径，并通过新增错误链定位后续真实失败。尚无证据支持引入curl/Schannel HTTP transport、变更cipher或关闭TLS校验；这些适配未实现，也没有宣称间歇故障根因已确定或问题已永久消失。本实验只诊断连接，不计为内容任务质量或核心场景验收通过。
