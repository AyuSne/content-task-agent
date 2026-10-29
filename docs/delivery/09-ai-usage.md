# AI使用与实际投入

本轮由Codex主控及实施/独立评审子Agent完成源码、测试、诊断和文档工作。用户提供目标、规格与工程约定，并纠正API直连要求；没有把无证据的人工源码修改或验证写成用户贡献。

项目实际使用 implement-spec、implement/tdd、codebase-design、diagnosing-bugs、code-review、writing-for-agents、progress、retro。相关Skill正文已读，实施有CLI red/green与提交，独立评审有固定比较点与真实发现；中途子Agent用量限制如实保留，后续两个可用独立上下文完成增量复查，未由主控自测替代。

本轮可回查修复提交窗口从2026-10-29 10:28:40（北京时间，2731a22）起，结束以最后提交时间为准；初始阅读发生在该提交前，因此它不是完整人工工时。API等待、并行Agent和主控工作存在重叠，不相加伪造工时。

19个内容任务保留393次API尝试，22次用量未知，已知输入8232992与输出381161 tokens；包括早期失败和人工稿。校准、外部评价及网络诊断单独保留，不重复混计。真实金额无可靠来源，开发Agent Token用量不可得，均保持未知。明细见[机器汇总](../../examples/acceptance-summary.json)和[验收报告](10-repair-acceptance.md)。

业务方案与评审使用DeepSeek deepseek-flash，通过openai SDK显式非思考、非流式、零重试接入；真实与Mock独立。单次有界评估上下文独立不等于统计独立，小样本也不证明生产效果。密钥只在本地忽略配置，日志与提交仅保留配置存在状态。
