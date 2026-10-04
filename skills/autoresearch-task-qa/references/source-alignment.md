# 来源、版本与适用范围

本公开版采用 QA schema v0.3.4。规则来源分为两类：目标版本的公开 Harbor 规范，以及经过脱敏后保留的任务验收契约。当前批准教程快照属于私有来源，不随公共 Skill 分发；其内部修订号、附件标识和协作链接不得写入公开文件，也不能作为外部用户可访问的证据。

## 当前契约

- 对外验收所用的期次、题号和记录主键必须在提交时重新从当前期次的权威题库或验收表读取，并保留可复核的记录定位信息。旧期表、本地文件夹名、压缩包名或历史报告不能代替当期题号；权威表不可达或权限不足时，对外填报状态为 `INCOMPLETE`，不猜测、不沿用。
- Agent 与 Verifier 分别以 `environment/Dockerfile`、`tests/Dockerfile` 构建，并显式设置 `[verifier] environment_mode = "separate"`。
- 公开 Dev 评测必须供 Agent 迭代；最终私有 Hidden 材料留在独立 Verifier。Hidden 可采用预置、可复现生成或安全注入，但必须有材料、实现和真实调用证据，不能用空目录或目录名代替。
- 必须提交一次当前题包版本的 NOP 自检记录。核对同一 Trial 的配置、结果、reward、日志、任务版本和 separate 运行证据；缺失时 H06 与 QA17 失败。可复用平台已有的同版本记录，NOP 分数本身不决定结论。
- 源码 `solution/` 是可选 Oracle；它与 Agent 的运行时提交目录不是同一概念。
- 两条 Agent 轨迹每轮包含八字段：`round`、`policy_name`、`method_summary`、`status`、`score`、`failure_reason`、`retained_best`、`time`。旧两字段轨迹不再满足契约，人工填写 pass 也不能覆盖格式反证。

## 研究质量裁定

- 随机评估采用 Baseline 样本标准差：3σ 是最低改善门槛，3–5σ 可接受并建议复核，5σ 以上是强证据。确定性评估使用真实正向改善、归一化范围和任务预声明容差，不恢复统一 5% 规则。
- 两条 Agent 轨迹默认各至少 10h 有效迭代；各 7h 例外仅适用于不涉及训练或微调、单轮迭代很短且证据完整的任务。排队、安装、构建故障和阻塞不计。
- Baseline 默认来自未经修改的 Starter；无 method 的 Scaffold 或合理 naive Starter 也可接受，但必须可运行、可解释、公平，并明确映射到评价锚点。
- 固定框架里的常量或超参数搜索不构成开放优化面；能够实现新方法的接口不能仅因某个参考实现改动较小就自动判为纯调参。
- Headroom 原则用于风险复核，不升级成未声明的一刀切门槛。

## 公开依据与静态边界

Harbor 相关语义优先核对目标版本的官方 [Environment](https://docs.harborframework.com/core-concepts/tasks/environment)、[Verifier](https://docs.harborframework.com/core-concepts/tasks/verifier)、[Configuration](https://docs.harborframework.com/core-concepts/tasks/configuration)、[Separate verifier](https://docs.harborframework.com/core-concepts/tasks/separate-verifier) 和 [官方仓库](https://github.com/harbor-framework/harbor)。`main` 分支不自动等同于已部署版本，报告必须写明读取版本或日期。

平台完整要求与自动静态质检的覆盖范围分开写。QA07/08 只检查题面泄露，QA15 不审平台资源上限；这不代表完整物理隔离、资源限制或稳定性已通过。Skill 可检查已有 Trial 证据，默认不执行未知代码、不构建镜像，也不冒充完成动态验收。

格式扩展只要等价、路径有效、证据完整就不自动扣错；必需字段缺失、真实路径错误和调用失败不能用“允许扩展”豁免。所有报告和附件继续受 [隐私与可移植性门禁](privacy-and-portability.md)约束。
