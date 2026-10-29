# 本轮运行环境记录

日期：2026-10-29（北京时间）。

第一次`uv sync --locked`在受限执行环境内失败，工具返回exit1，原因摘录：`Failed to create temporary virtualenv`，复制`venvlauncher.exe`到项目`.uv-cache/builds-v0/.../Scripts/python.exe`时`拒绝访问。 (os error 5)`。后续经自动批准的正常执行权限运行同一同步命令成功；最终同步日志为`sync.txt`。首次原始文件被重试日志替代，因此此处明确保存的是工具返回摘录，而不是伪装成原始日志。

受限环境中直接启动本地Python也曾返回`Unable to create process`；获得正常执行权限后，同一仓库虚拟环境可运行。没有因此改写产品模型路由、代理、TLS或依赖锁文件。

初始完整回归204项exit0。`tests-final.txt`那次回归在eval测试工具参数修正前已完成收集，使用旧函数，留下1项eval代码失败。`strategy-green-final.txt`为17项定向回归exit0；`tests-final-corrected.txt`在修正后重新收集并完整运行207项，exit0。`source-hashes.json`记录最终运行源码的本地原始字节指纹，对应修复提交`63ec7c8`；Git对源码的换行规范化不等同于运行行为变化。

真实API按直连执行，不指定7890，不修改用户系统代理或TUN；SDK重试0，TLS校验保留。Post-fix旅行场景实际出现一次核验TLS EOF，1次用量未知，后续模型根据失败反馈再次发起核验并完成保存，全部尝试保留。
