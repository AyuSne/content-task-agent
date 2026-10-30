# 历史任务与代码恢复

提交样例可以直接阅读 JSON、Markdown 和 Trace。需要继续执行已有数据库中的历史任务时，使用任务适用的代码、资料、费用表和本地配置。

## 旧业务协议

旧业务协议固定基线为 `836aa827d835e54a8e9385c18e7a3ff05ed3e600`。当前代码保留查询入口，旧任务的执行、人工稿导入、核验和保存使用该固定代码。

可在另一个工作树中准备环境：

```powershell
git worktree add --detach ../content-task-agent-legacy 836aa827d835e54a8e9385c18e7a3ff05ed3e600
```

进入该目录后按对应锁文件安装依赖，运行时指定原任务数据根目录。原成果、费用、模拟时钟和轮数继续沿用。

## 新业务协议中的处理版本

新任务记录 `direct-script-v1`，初始化时同时记录提示词、工具契约与 handler 版本。处理语义变化后，恢复入口按记录选择。

| 已有任务处理版本 | 固定恢复代码 |
| --- | --- |
| `evidence-version-roundtrip-v1` | `b49e8b5` |
| `tool-guidance-feedback-v1` | `dd665dc` |
| `tool-guidance-review-scope-v1` | 当前代码 |

更早的版本映射集中在 `src/cta/protocol.py`。不兼容时，CLI 会返回处理版本与恢复指引。准备任务原采用的资料和费用版本，密钥由本地配置提供。

历史真实记录见[证据索引](delivery/06-evidence-index.md)，当前协议离线入口见[README](../README.md)。
