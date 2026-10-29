# 模型 API 显式直连

日期：2026-10-29。起点 `567841e`，分支 `repair/direct`。

## 现场证据与结论

用户明确模型 API 走直连，不使用7890。无鉴权检查安装的 httpx 源码确认：默认 `trust_env=True`，其 `get_environment_proxies()` 调用 urllib 的 `getproxies()`，可读取环境变量并在 Windows 回退到系统注册表。

本次现场清除大小写 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY、NO_PROXY 后，`urllib.request.getproxies()` 与 `httpx._utils.get_environment_proxies()` 实际均返回 `{}`。因此不能把系统代理或7890称为已确认的间歇 TLS EOF 根因。早期无鉴权正负探针由主控另存；本修复实现明确的直连约定，不添加 TLS 兼容分支。

## 变更与验证

- `DeepSeekClient` 将显式 `httpx.Client(trust_env=False)` 交给 openai SDK，忽略进程及系统代理配置，TLS 验证保持默认开启；SDK 自动重试仍为0，原 CTA_LLM_TIMEOUT / 默认180秒同时应用到 SDK 与 HTTP 客户端。
- 公开模型配置标记 `network_route=direct`。httpx 从已有的传递依赖提升为直接依赖，锁定版本仍为0.28.1，没有更新其它依赖版本。
- 回归使用真实 openai/httpx adapter 连接本地 HTTP 模型端点，环境注入不可达代理且清空 NO_PROXY，验证返回内容、用量、非流式和非思考请求，并断言只收到一次请求。仅模拟外部 HTTP 端点，没有真实付费请求。
- 修复前 `tests/test_model_direct.py` 返回 `APITimeoutError`，模型端点收不到请求（`red.txt`）。修复后该测试通过；与任务接收、冻结协议合计20条定向测试通过（`green.txt`）。

本地 HTTP 用于隔离验证路由选择，不证明真实 DeepSeek 语义能力，也不证明最初的间歇连接问题已经消失。主控继续按真实任务证据核对。
