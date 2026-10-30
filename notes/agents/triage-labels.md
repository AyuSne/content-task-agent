# Triage Labels

本地工单使用以下默认角色名称，记录在文件开头的 `Status:` 行。

| 技能中的角色 | 本地状态 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者评估 |
| `needs-info` | `needs-info` | 等待补充必要信息 |
| `ready-for-agent` | `ready-for-agent` | 已充分定义，可以交给Agent处理 |
| `ready-for-human` | `ready-for-human` | 需要人工处理 |
| `wontfix` | `wontfix` | 决定不予处理 |

技能提到分流角色时，使用上表对应名称。Wayfinding的领取和解决状态按[工单配置](issue-tracker.md)另行解释。
