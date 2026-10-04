---
name: autoresearch-run-isolation
description: 为 AutoResearch 的双 Agent 轨迹、付费 GPU 长跑、Docker 执行、可信评测与恢复建立共享协议、成本决策和隔离边界。用于小时/包日选择、启动或恢复 campaign、设计证据与防止题目或轨迹串用；不替代具体任务算法或最终平台 QA。
---

# AutoResearch 运行协议与隔离

先读 [协议与隔离不变量](references/protocol-and-isolation.md)。涉及租卡先读 [成本与容量](references/cost-and-capacity.md)；涉及容器或 Harbor 再读 [Docker 执行与 Harness 边界](references/docker-and-harness.md)；接管单卡 NVIDIA 虚机、排查长跑停滞、处理 GPU backend 或 CUDA 缓存问题时读 [单卡 NVIDIA Docker 现场蒸馏](references/nvidia-docker-field-lessons.md)；准备停机、释放实例或冻结最终包时再读 [最终化与关机](references/finalization-and-shutdown.md)。优先复用成熟运行协议；每题只实现任务适配器与可信评测器。

运行前冻结任务树并计算逐文件哈希；两条 Agent 轨迹使用同一公共任务摘要，但使用独立 workspace、控制目录、端口、上下文、凭据和轨迹目录。候选进程只能看到 Starter 与公开资产，Reference、专家证据和另一条轨迹不得进入其挂载范围。模型名称与 provider 是任务参数，不写死为某一家服务。

把指标、阈值、随机性协议、两条轨迹、开发环境、目标 Harness、持久快照、停止条件、liveness 协议、provider 硬失败策略和 durable evaluator 重试规则写入 JSON 合同，并在租卡前运行 `scripts/preflight.py`。GPU 任务还必须记录目标 backend 当前版本的能力依据。

第三方服务器声称内置 Docker 时，先用 `scripts/docker_host_preflight.py` 检查 daemon、Compose、存储和可选 NVIDIA runtime；GPU 场景再用本地已有、digest 固定的镜像执行显式动态 probe。随后分别取得 Agent 镜像、Verifier 镜像和目标 backend 的真实 GPU Trial。宿主有 Docker、通用 CUDA 容器成功或 Harness dry-run 通过，都不能替代两条隔离 lane、双镜像构建或完整 reward 证据。

每轮保存原始 RPC、命令、评测摘要、receipt 和来源哈希。每个正式结果只绑定一个真实 run/trial；源码、配置、seed、receipt、artifact、checkpoint 与时间窗不得跨轮拼接。可用 `scripts/verify_lineage.py` 检查结构化索引。

有效时长以闭合 turn 窗口为基础，未闭合、排队、安装、阻塞、睡眠和故障不计。两条轨迹分别达门槛，不能相加。单 GPU 默认串行训练/评分；只有显式绑定不同设备且协议允许时才并行，避免资源竞争污染结果。

把外部模型数据流纳入隔离合同：明确会发送的 prompt、工具结果和评测摘要；子进程环境使用最小白名单，凭据只以显式环境变量注入。落盘前按真实 secret 值和通用 token 形态脱敏，但不得把脱敏当作零外发。公开轨迹只保留完成审计所需字段，不发布原始 RPC、作者路径、主机信息或 capability 链接。

恢复运行前比较任务摘要、driver capability、镜像 digest、已完成轮和远端 lease；不复用旧 PID 或不明控制目录。租赁实例不是唯一存储，关机前同步代码、轨迹、checkpoint、receipt 和成本台账，并至少做一次新容器或新实例恢复演练。

默认采用分阶段资源策略：本地/小卡清除功能问题，目标 GPU 按小时完成真实端到端 pilot，再按 `scripts/plan_capacity.py` 比较全小时、全包日和“小时 pilot + 包日正式窗口”。将关机后租不到、重建、延期和无法补测的期望损失纳入小时制；包日空闲是容量保险，不冒充计算利用率。模型 API 先检查模型、区域、配额周期和并发是否符合，再用 pilot 的真实用量比较套餐与按量价格。API、GPU 和存储分开记账，追加消费不能修复协议、血缘或 Harness 缺陷。

打包只从冻结源和显式白名单构建；构建后解压到新目录，比较完整路径集合与 SHA256，再交给 QA。主包、自检、轨迹和证据附件分别执行隐私检查，任一附件失败都不能发布。

本 skill 的 `assets/compose.yaml` 是普通 NVIDIA Docker 主机上的开发模板，不代表目标 Harbor backend 支持 GPU。目标平台能力以当前版本和真实运行证据为准；没有动态运行时只报告静态检查。
