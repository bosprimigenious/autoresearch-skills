# Docker 执行与 Harness 边界

## 推荐拓扑

受信控制层持有 API/provider 凭据、调度与原始事件；两个无密钥 Agent sandbox 使用同一冻结任务镜像 digest 和不同可写卷；可信 evaluator 持有隐藏资产并生成 receipt/reward；公共模型与数据缓存只读共享。

- Agent 不挂载宿主 Docker socket、SSH 私钥、peer 卷、Reference 或隐藏 tests。
- 单卡通过受信锁串行分配 trainer/evaluator；双卡显式记录宿主 device ID 与容器可见设备。
- controller 若能执行任意 root shell，必须限制模型可调用的参数与路径，否则容器隔离只是表象。
- 镜像、代码、轨迹和正式证据同步到租赁节点之外的持久位置。

`assets/compose.yaml` 是普通 NVIDIA Docker 主机的开发模板。`lane-a`、`lane-b` 和 `evaluate` 是互斥 profile；单卡时由受信控制层一次只启动一个 profile，不能执行无 profile 的全量并发启动。运行前至少执行 Compose 配置解析、容器内 GPU 可见性和真实最小前后向；这些结果不能替代目标 Harness 的 parser/build/trial/verifier/reward 链。单卡 NVIDIA 虚机的实测故障分级、ENTRYPOINT、durable evaluator 和 CUDA cache 处理见 [现场蒸馏](nvidia-docker-field-lessons.md)。

## 服务器内置 Docker 的接管顺序

“服务器已装 Docker”只省去安装步骤，不代表任务环境已经可用。先在目标服务器运行 `python3 scripts/docker_host_preflight.py --requires-gpu --out docker-host.json`，再按以下顺序留证：

1. 核对 Docker daemon、Compose plugin、Docker root 可用空间和 NVIDIA runtime；静态预检失败就不拉大镜像。GPU 任务再用本地 digest 固定镜像执行 `--pull=never` 的短暂容器 probe。
2. 分别构建 Agent 与 Verifier 镜像，记录 Dockerfile、context 和最终 image digest。不能因为二者共用基础镜像就合成一个包含 Hidden/Reference 的镜像。
3. 用两个不同可写卷和控制目录启动 `lane-a`、`lane-b`，验证不能读取 peer 卷、宿主凭据或可信评测目录。
4. 在每个 Agent 镜像内做入口、保存/重载和网络策略 smoke；GPU 任务还要在容器内完成真实最小前后向，而不只看宿主 `nvidia-smi`。
5. Agent 结束后才把最终产物移交给独立 Verifier，跑一条完整 parser → trial → verifier → reward 链并保存 receipt。
6. 在释放实例前把镜像 digest、合同、轨迹、checkpoint 和 receipt 同步到机外，并从新容器恢复一次。

内置环境若只有 Docker CLI、daemon 权限不足、空间不够、没有 GPU runtime、Compose 版本不兼容或无法取得固定 digest，均应在正式计费前失败关闭。`docker_host_preflight.py` 默认只做静态宿主检查；只有显式给出 `--gpu-probe-image` 时才会启动一个禁止拉取、完成即删除的 GPU probe 容器。它不会构建或启动任务镜像，也不能替代第 2–6 步的动态证据。

平台或 Harbor 的 backend 支持会变化。Harbor 当前公开任务格式允许声明 `gpus`/`gpu_types`，Docker 环境也可读取 Dockerfile、镜像或 Compose，但资源执行方式随 provider 而异，许多云 sandbox 只接受 Dockerfile。每次固定目标版本与 provider，检查安装版本的命令帮助和官方文档；先查 backend capability，再把 dry-run 记为静态解析证据，最后用真实 Trial 验证 GPU 分配。不能凭宿主 `docker run --gpus`、旧示例或 dry-run 推定 backend 的 GPU、Compose、挂载、工作目录或 reward 路径仍相同。Oracle 证明 reference solution 与 verifier 正向闭环，Starter/负例检查平凡满分和 reward hack，真实 Agent Trial 才证明实际 agent 路径。

## 外部依据

实现时优先核对一手文档，并在每次目标平台升级后重新确认：

- [Docker Compose GPU support](https://docs.docker.com/compose/how-tos/gpu-support/)
- [Docker Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- [Harbor task structure and resource requirements](https://harborframework.com/docs/tasks)

这些链接只说明通用能力或当前公开接口，不是某次任务已经通过的证据。最终以目标版本、目标 backend 和真实 trial receipt 为准。
