# 单卡 NVIDIA Docker 现场蒸馏

本页来自一次单卡 RTX 4090 Linux 虚机上的 Docker、GPU Harness、长程 Agent 与最终复验实践。只保留能改变后续决策的机制；云厂商、账号、地址、题目、时间戳、价格、私有路径、凭据和产物哈希均不进入本页。

## 四层能力必须分别证明

| 层 | 最小证据 | 不能推出 |
|---|---|---|
| 宿主 GPU | 驱动信息与 `nvidia-smi -L` | Docker 容器可见 GPU |
| NVIDIA Docker | digest 固定的本地镜像执行 `docker run --pull=never --gpus all ... nvidia-smi -L` | 目标 Harness backend 接受 GPU 请求 |
| Harness backend | 固定版本/provider 的 capability 证据与真实最小 Trial | 正式任务的 Agent→Verifier→reward 链通过 |
| 正式任务 | 当前题包的 NOP/真实 Trial、独立 Verifier、reward 和完整 receipt | 另一版本、另一 provider 或修改后题包也通过 |

真实现场中，宿主和普通 NVIDIA Docker 均正常，但目标版本的本地 Docker backend 在镜像构建前拒绝任务的 GPU 请求。其 `dry-run` 仍能成功，因为它只解析 job shape。以后必须先验证 backend capability，再安装大依赖或等待镜像构建。

本地兼容层只能作为显式记录的工程适配：必须保留上游版本、补丁、GPU reservation、网络策略和真实 Trial 证据。若提交平台要求官方 backend，兼容层通过也不能冒充官方 provider 通过。

## 虚机与容器接管

需要运行 Docker daemon 时选择 Linux 虚机/系统镜像；平台提供的“容器实例”通常不能再安全运行嵌套 Docker。接管顺序：

1. 固定驱动、Docker、Compose、NVIDIA Container Toolkit 与目标 Harness 版本。
2. 先运行静态宿主预检；再用已经本地存在且 digest 固定的镜像执行短暂容器 GPU probe，避免 probe 意外拉取浮动镜像。
3. 在 Agent 和 Verifier 两个真实镜像内分别做 GPU smoke。宿主 probe 或通用 CUDA 镜像通过不能替代这一步。
4. 固定构建 context、运行用户、工作目录、网络策略和 GPU reservation；保存解析后的 Compose 配置及 image digest。
5. 最后运行目标 backend 的真实最小 Trial。parser/dry-run 只记为静态证据。

Verifier 镜像若自带业务 `ENTRYPOINT`，而 Harness 预期先启动 keepalive 容器再 `docker exec` 调评分入口，两者会冲突。适配时应在运行层显式重置 ENTRYPOINT，并验证目标用户存在、评分脚本可执行、reward 路径正确。Agent 使用任务根 context 与 Verifier 使用独立 tests context 是两个边界，不因共享基础镜像而合并。

## 长跑卡住时的分级

不要按 stderr 中第一条红字决定重启。按下面顺序回读同一时点：

1. 本地 controller/scheduler 是否仍存活，停止标记是否触发；
2. 原始 RPC 最后更新时间、当前 turn 是否闭合、最后 receipt 是否可解析；
3. 远端 evaluator job 是否仍运行，是否已经写出 durable result；
4. SSH 控制连接与远端主机是否可达；
5. GPU 是否有进程，以及该阶段本来是否需要 GPU。

模型列表刷新、可选 Skill 加载或子 Agent 创建失败，在主 RPC、闭合回合与远端评测继续推进时属于旁路告警；不能因此重启主链。provider 鉴权/余额的确定性 4xx、冻结身份不匹配、控制器退出、receipt 不能闭合或 evaluator 确认失败属于硬阻塞，该回合计零并停止新消费。

传输中断也不自动等于评测失败。重试前先按 durable job ID 查结果目录；已有完整结果就复用同一 job，缺失或原子写入未完成才重跑。否则会制造重复 GPU 消费和一份源码对应多个不清晰 trial 的血缘问题。

健康判定不使用固定“几分钟无 GPU”阈值：方法推理、CPU contract、编译和回合收尾阶段都可能合法空闲。真正的停滞是 controller、RPC、远端 job 与 receipt 同时没有可解释进展，并达到合同定义的 liveness deadline。

## 隔离与 CUDA 缓存

严格文件系统沙盒下，补丁工具需要在源码同目录创建临时文件，Python 可能写 `__pycache__`，CUDA/Triton 会写编译缓存和临时目录，并可能使用本地 Unix socket。只开放源码文件而没有可写 scratch/cache，会把权限错误误判为候选算法失败。

- Agent 源码目录允许任务合同内的原子替换；其余任务资产只读。
- 每个角色、候选 SHA 和运行时组合使用独立 cache/scratch，不共享可写编译缓存。
- cache 身份至少绑定源码 SHA、依赖/驱动/设备摘要和冻结配置；最终独立复验使用新 cache namespace。
- 预热只能加速编译，不能把旧候选产物当成当前候选证据。
- 发布包从白名单源重建，拒绝 Python、测试、编辑器和 AppleDouble 缓存。

## 计时、协议修订和收尾

有效时长只取闭合、receipt-backed、非 blocked、包含真实方法/评测循环的区间并集。安装、依赖下载、模型列表刷新、断线重连、等待 GPU 和未闭合尾轮均为零信用。

模型/provider 需要变更时创建显式、前瞻生效的协议修订，记录原因、授权、原协议摘要、新身份和生效边界；修订前失败时间与身份不匹配的试跑不能追溯计入。达到时长后继续执行当前 SHA 的独立 GPU 复验、机外拉回和逐文件哈希核验，再按 [最终化与关机](finalization-and-shutdown.md) 关机。

## 可复用命令入口

静态检查，不启动容器：

```sh
python3 scripts/docker_host_preflight.py \
  --requires-gpu \
  --minimum-free-gib 50 \
  --out docker-host.json
```

动态 probe 需要显式提供已经存在于本机、以 digest 固定的镜像；命令只启动一个 `--rm` 容器且禁止拉取：

```sh
python3 scripts/docker_host_preflight.py \
  --requires-gpu \
  --gpu-probe-image 'registry.example/cuda-probe@sha256:<digest>' \
  --out docker-gpu.json
```

该 probe 通过后，报告仍会声明它没有证明目标 Harness/backend 或正式任务通过。
