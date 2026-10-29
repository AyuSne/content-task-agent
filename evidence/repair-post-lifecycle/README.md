# 同一真实上下文的连接生命周期核对

日期：2026-10-29。起点 `0d200e8`，分支 `repair/post-lifecycle`。本目录是外部网络诊断，未修改产品连接配置，未将诊断请求记入原内容任务的模拟账本。

## 输入与方法

使用主控任务 `t_2c8aac8edf5e` 的 `normal-knowledge2-01/08-final-inspect.json` 全部49条合法主消息，配本次版本的完整工具定义。固定 `max_tokens=128`，显式非思考/非流式，SDK零重试，所有Python客户端 `trust_env=False`，所有curl请求 `--noproxy '*'`。未使用7890，未修改系统代理/TUN，TLS证书校验保持开启。

SDK实际序列化的同一119515字节body用于全部请求，SHA256 `77fd345a6ec88ec2ebdfc4c8b05c2c9899e112fec5a62457dba14f02fc9884d6`。curl认证只经stdin配置输入，没有写入命令行、文件或日志；落盘的 `request-body.json` 只有业务消息和模型参数，没有认证头。

固定顺序各三次：默认HTTP/1.1（keepalive_expiry=5秒）、HTTP/1.1保留连接180秒、HTTP/2（expiry仍5秒）、Windows原生curl/Schannel。每组内等待6秒再请求。httpcore trace记录新建TCP/TLS和失败阶段，响应hook记录HTTP版本；curl每次新进程，并记录状态、协议、目标IP和耗时。

## 实际结果

| 组 | 真实成功 | 每次新TLS数 | 实际协议 |
| --- | --- | --- | --- |
| 默认5秒 | 3/3 | 1、1、1 | HTTP/1.1 |
| 保留180秒 | 3/3 | 1、0、0 | HTTP/1.1 |
| HTTP/2，5秒 | 3/3 | 1、1、1 | HTTP/2 |
| Windows curl，新进程 | 3/3 | 各自新建 | HTTP/1.1 |

每条请求的实际开始/结束、body大小/哈希、模型/响应ID、usage及HTTP/TLS事件完整保存在 `real-01/result.json`。所有已发起请求都保留；本次没有未知模型用量，不包含自动重试。原任务此前的TLS失败仍由主控保存，本实验未覆写或抵消它们。

主控CLI环境也已只读核对：`cta.model.__file__` 指向主仓库 `src/cta/model.py`，Python为主仓库`.venv`，httpx0.28.1，OpenSSL3.5.4；未发现editable安装指向旧worktree。

## 结论和边界

可以确认：默认5秒连接到期确会使6秒间隔的三次调用分别重新握手；保留180秒确实复用连接。此次大上下文新握手、HTTP/2和原生curl均成功，不能据此把生产间歇EOF归因为连接过期，也没有证据支持改用curl比Python更稳定。本轮没有更换transport、放宽TLS或宣称稳定性已解决。

若主控选择延长keepalive，只能把它解释为减少握手机会，后续仍须用长任务真实证据核对；不能把本次12次外部诊断成功当作内容任务交付验收通过。

## 补充包装器核对

主控随后保留了 `wrapper-uncapped/`、`wrapper-pair/` 和 `wrapper-output-limits/`：最初128上限成功而未设置上限的三个调用TLS失败；后续8192上限也失败，紧接着未设置上限和16384又都成功。`curl-uncapped.json` 的原生TLS请求也成功。这些反转说明不能把输出上限称为已定位原因，因此没有据此修改生产输出上限或加入curl回退。所有失败保留未知用量。脚本 `diagnose_wrapper_pair.py --arms ...` 可明确选择对应有限采样。

生产连接池采用180秒保留时间，只依据已实测的握手次数减少；每次实际请求保存 `request_config`，TLS校验和SDK零重试不变，未声称间歇EOF消失。

## 复现

`scripts/diagnose_model_lifecycle.py --env-file <本地.env> --inspect <任务inspect.json> --output <新目录>`。HTTP/2探针依赖h2==4.4.1（hpack4.2.0/hyperframe6.1.0），本次只安装在worktree `.scratch/diagnostic-deps`，运行时加入PYTHONPATH；没有修改主虚拟环境、依赖文件或产品代码。
