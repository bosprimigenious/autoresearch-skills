# AutoResearch 出题

> 从论文与代码仓设计可交付的 AutoResearch 工程题，确定 Starter、Baseline、Reference、评分器、Harbor 结构和证据契约。用于出题、改题或审题；不替代提交包最终 QA。

先读任务平台最新规则、项目示例和目标论文，再读 [authoring-contract.md](references/authoring-contract.md) 与 [前移 QA](references/shift-left-qa.md)。不得从旧题或别的仓库复制未经验证的结构。

1. 建立题目合同：允许修改面、冻结面、输入输出、预算、质量门、主指标方向和 0/1 锚点。完成论文身份、许可、优化面和当前权威题库证据后运行 `scripts/authoring_gate.py selection evidence.json`。
2. Starter 必须朴素、可运行且不故意削弱；Baseline 必须来自正式实现并能解释。
3. Reference 只能证明题目有改进空间，不能把算法答案写进 instruction；正式 B/R 使用相同数据、seed、预算和评测器。
4. 评分器从候选代码重新运行并独立计算结果，不采信候选自报分数；成功结果用同文件系统 stage 后原子替换。
5. 将作者材料、参赛者 workspace 和优化证据分开。Docker COPY、WORKDIR、入口与选定的平台 profile 必须形成一条可执行路径。
6. 先做小规模成对实跑，再决定是否值得开展双轨迹长跑。完成 Baseline/Reference、效应/噪声和独立复算后过 `pilot` 门；完成双镜像、Hidden 隔离和目标 Harness trial 后过 `container` 门。没有动态证据时明确写“未验证运行”。
7. 双轨迹分别保存正式血缘与失败轮，完成机外快照和恢复演练后过 `long_run` 门。失败必须用新 trial 修复，不覆盖旧 receipt 或拼接不同轮次证据。
8. 交付前使用 `autoresearch-qa-skills-0.3.3` 做双路独立本地质检。冻结唯一完整提交包 ZIP 及 SHA256；两种不同 AI 分别新建无历史上下文的会话，每个会话只发该 ZIP。不同模型家族优先，同类 AI 的不同版本也可以。不附带散文件、旧报告、作者解释或另一 AI 的结论。两路首轮质检均须完整实测；任一路命中硬失败或分歧未闭环即 NOT READY。审查报告是 `release` 门的输入，不能由作者原会话内自报结论替代。
9. 开源或外发前对主包、QA/self-check、轨迹和证据附件分别做隐私与可移植性检查；生成报告不得保留作者 home、凭据值或私有文件链接。按 [出题合同](references/authoring-contract.md#release-证据-schema) 生成 `release-evidence.json`，再运行 `python3 scripts/authoring_gate.py release release-evidence.json`。门禁会读取并校验两份 QA 报告、共识、隐私报告和 manifest 的内容与 SHA256；任一引用缺失、摘要被篡改、模型或会话不独立、ZIP 不同源或有未解决分歧都必须失败。

输出至少包括题面、接口、Starter、Baseline、Reference、评分器、冻结清单、证据清单、构建说明和验收命令。
