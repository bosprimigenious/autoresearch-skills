# 出题合同

## 必须先回答

- 参赛者能修改什么文件、函数和语义？
- 哪些资产、数据、seed、预算和评测逻辑冻结？
- Baseline 为什么合理，Reference 为什么不是泄题？
- 主指标如何从候选运行重新计算，失败如何关闭？
- 质量、速度、显存、稳定性分别是目标还是硬门？
- 证据由哪个步骤生成，路径、schema 和哈希是什么？
- `training_seed`、重复试验编号和 `run_id` 如何区分，分数如何聚合？
- 开发容器与最终 Harness/backend 分别如何验收，GPU 能力依据哪个目标版本？

## 交付目录职责

- `workspace/`：参赛者可见且可执行的题目。
- `expert_evidence/`：专家说明、轨迹与汇总结论。
- `optimization_evidence/`：Baseline/Reference 成对原始结果、日志、模型索引和比较。

平台若要求单业务外层目录，ZIP 根只能有一个目录，该目录内再放以上三项。

## 完成标准

- Starter、Baseline、Reference 都能在声明环境运行。
- B/R 使用同协议，结果可从原始值复算。
- 每个正式结果绑定一个真实 trial；源码、配置、seed、receipt、artifact 与 checkpoint 的 `run_id` 一致，不跨轮拼证据。
- 评分器拒绝旧结果、缺文件、篡改资产和不完整 case。
- instruction 不泄露 Hidden、Reference 代码或作者路径。
- 代码、文档、JSON 和哈希在同一次构建中生成，不靠手工补数字。
- 长跑前先用低成本 pilot 验证完整链路和估计噪声；小卡只用于功能与缩规模调试，不能替代目标 GPU/Harness 的正式证据。
- 若目标任务需要 GPU，必须记录目标 backend 当前版本的支持证据与一次真实运行结果；普通 Docker Compose 能启动不等于平台 GPU 验收通过。
- 冻结唯一完整提交包 ZIP 及其 SHA256，不把散文件或已解压目录作为本地质检输入。
- 使用 `autoresearch-qa-skills-0.3.2`，由两种不同 AI（或同类 AI 的不同版本）在彼此隔离的新会话中，对同一 ZIP 各自完成一次质检实测。每个会话只提供该 ZIP，不提供旧 QA 报告或作者引导。
- 两路 QA 均通过且结论一致；有分歧时回到 ZIP 内证据和对应检查项闭环，不以多数投票掩盖硬失败。
