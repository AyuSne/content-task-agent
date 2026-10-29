# DeepSeek 直连连接诊断

2026-10-29，北京时间。全程未读取密钥，未向端点发送鉴权；未修改产品、代理或系统设置。用户澄清后所有探针清除 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY，按当前直连网络路径执行。

## 观察

- 原反馈循环：Python 3.13.11 / OpenSSL 3.5.4，`httpx.Client(trust_env=False).get('https://api.deepseek.com/models')`，在TLS握手阶段返回 `UNEXPECTED_EOF_WHILE_READING`。原生Windows curl在同端点返回401（无鉴权的正常返回）。
- `probes.json`：默认httpx、stdlib原始SSL、ALPN、仅IPv4及TLS1.2+DEFAULT cipher都复现EOF，约5.2秒；同Python访问清华PyPI镜像成功301。说明不是Python所有HTTPS均失效，也不足以证明单一TLS版本或代理设置是根因。
- DNS在原生curl和Python均解析为198.18.0.20。curl使用Windows Schannel，返回401；日志报告两次TLS renegotiation。这里只记录现场，不据此推断上游网络配置。
- Python3.12.3 / OpenSSL3.0.13的默认握手也EOF，而单cipher TLS1.2探针成功401；紧随其后的Python3.13默认握手已成功401（TLS1.3 / TLS_AES_256_GCM_SHA384），未应用产品或环境修复。
- `repeated-httpx.json`：之后六次顺序探针，默认TLS与小TLS1.2交替，全部401；默认三次分别0.313、0.171、0.163秒。原httpx反馈循环已恢复。

## 结论与处置

可确认的结果是：本次连接失败在没有代码或系统设置变更的情况下消失，原直连httpx路径随后连续成功。无法从这些证据确认最初EOF的根因；不能把特定cipher或Python版本称为已验证修复。主控可继续显式续跑原任务，保留首次失败与未知用量，不引入curl transport、关闭证书校验或TLS兼容分支。

`runtime_probe.py`为无鉴权stdlib复现入口；`probes.py`为最小差分诊断脚本。初次失败采样及恢复连续采样分别保存在上述JSON，不应改写为所有真实模型请求成功。
