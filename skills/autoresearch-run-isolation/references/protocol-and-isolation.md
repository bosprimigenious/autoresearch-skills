# 协议与隔离不变量

- 任务摘要与轨迹来源绑定；修改冻结题目后旧轨迹失效。
- GPT/Seed 不共享可写目录、agent 配置、会话凭据或结果文件。
- 远端同步只上传白名单输入，只下载报告和声明证据。
- 评测器不读取候选自报 reward；结果目录先清理，成功后原子替换。
- 网络、子进程、offload、缓存、seed 检测等限制由运行时和 grader 双重执行。
- 原始证据不可由说明文档替代；派生报告必须记录来源路径与哈希。
- 中断恢复先审计现场，再继续；不自动恢复可能产生费用的旧任务。
- 包、轨迹、说明和 JSON 必须在同一最终数据上重建。
- 每个正式 result 的 execution、source、configuration、receipt、artifact 与 checkpoint 必须属于同一真实 run/trial；同分数不构成身份相同的证据。
- `training_seed`、`replicate_id` 与 `run_id` 分开记录；是否要求多 seed 由当前协议决定，同 seed 重复不能改标签冒充多 seed。
- 单 GPU 的两条轨迹可以保留各自容器状态，但训练和可信评分默认排队串行；双 GPU 才显式绑定不同设备。
- 开发 Compose、目标平台 Harness 和最终 GPU backend 分层验收，任一层的成功都不自动证明下一层。
- 长跑健康度由 controller、RPC、闭合 receipt、远端 durable job 与阶段预期共同判断；可选能力报错不自动重启主链，确定性 provider 4xx、身份漂移或无法闭合回合立即停消耗且计零。
- evaluator 传输中断先按原 job ID 查 durable result，再决定是否重跑；同一候选不能因客户端断线制造多个身份不清的正式 trial。
- 租赁节点不是权威存储；关机前必须完成最终方法当前 SHA 的独立复验、持久快照、机外逐文件哈希核对和可执行恢复说明。操作系统关机与云平台停止计费分别回读，不能只凭 SSH 不可达宣称计费已停止。

推荐流水线：冻结 → preflight → 小跑 → 双轨迹 → 严格计时 → 合并 → 重建证据 → 白名单打包 → 解压同一性 → QA → 上传。
