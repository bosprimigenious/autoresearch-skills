# Harbor Harness 兼容性检查

本规范定义 QA17 的六个 Harness 子项，同时关联 QA06、QA18、QA21。默认只读取提交的代码与已有 Trial 证据，不执行待检代码；文件齐全不等于已验证运行。

## 依据与版本

- 本地证据：待检包内的版本锁定、运行配置、Trial 目录与适配说明；它们是待核验材料，不是成功证明。
- 官方：核对 [Environment](https://docs.harborframework.com/core-concepts/tasks/environment)、[Verifier](https://docs.harborframework.com/core-concepts/tasks/verifier)、[Configuration](https://docs.harborframework.com/core-concepts/tasks/configuration)、[Separate verifier](https://docs.harborframework.com/core-concepts/tasks/separate-verifier) 与 [Solution](https://docs.harborframework.com/core-concepts/tasks/solution)；旧 Task Structure 链接会重定向，优先使用这些直接页面。
- 配置与结果的精确类型以目标版本的官方 `src/harbor/models/task/config.py`、`models/trial/config.py`、`models/job/config.py`、`models/trial/result.py`、`models/verifier/result.py` 及目标 provider 实现为准，可从 [官方仓库](https://github.com/harbor-framework/harbor) 读取。不要假定 main 永远等于已部署版本。

先查包内版本锁定、运行配置或平台说明。未声明版本不直接判失败，可以明确采用带读取日期的官方文档快照作为静态对照，并在 `version_basis` 写规范 URL 和“文档静态核对，未做原生 schema 加载”；只有实际读过源码才注明源码。provider 可从明确 Docker 配置等材料推定，注明推定依据；不能确认则写 `unknown` 并维持未完成。不能把文档快照说成已安装版本。

不要求最新 schema_version 必填；按目标版本默认值、别名与实际类型判断。内部旧示例的 memory/storage、当前 memory_mb/storage_mb 等是否可用须核对版本，不能见旧字段就判失败。原生不识别的自定义元数据本身不一定报错，但不能把 [resources] 或 [entrypoint] 当成原生生效的资源限制或入口配置，除非有实际适配映射。

## 教学示例与严格路径

先读 [Docker 路径契约](docker-path-contract.md)。`teaching-task-root-v1` 与 `harbor-environment-v1` 是两套不同上下文，采用时必须记录来源，不能混用。`harbor.path_contract` 的确定路径失败自动使 H03 失败；动态语法未解析须补语义复核。仅运行普通单元测试而未写 reward 的入口不构成正式评分。

## 独立 Verifier 与公开/私有评测

当前 QA 契约要求 `task.toml` 显式包含 `[verifier] environment_mode = "separate"`，并提供 `environment/Dockerfile` 与 `tests/Dockerfile`。Agent 使用公开 Dev 反复验证方法；结束前恢复 Dev 最佳候选和必要模型/配置，平台再移交最终提交给独立 Verifier 正式评分。Verifier 执行评分及候选程序，无需启动另一个 Coding Agent。本版不假设存在每轮 Hidden 调用服务。

公开 Dev 评分代码及公开数据必须供 Agent 使用。若原来位于 tests/，先拆出公开部分，通常放入 environment/public_eval/，也可等价布局；不能笼统禁止 Agent 包含“评分实现”。最终私有测试数据、标签、参考答案或私有评估逻辑才是隔离对象。核对 COPY、忽略规则、镜像层和运行时挂载，不能以目录名称判断最终可见性。

Hidden 评测材料对训练与非训练任务均需要；可以是预置文件、可复现生成逻辑或安全注入的材料。目录不必叫 tests/hidden_assets/，不要求为了过检造一个空目录或占位文件。`review.harbor.hidden_review` 记录实际准备方式、材料和评分调用依据，见 implementation-report.md；只写“运行时注入”而无实现/平台依据不能通过。

Verifier 从 tests/ 上下文构建，镜像自行提供 `/tests/test.sh`、评分实现及必需依赖。专用镜像模式不再由 Harbor 运行时上传 tests/。依赖应在 Verifier 自己的镜像准备，不能依赖 Agent 临时安装包、缓存或未移交文件。正式评分只读取题面及协议允许的提交物。

核对 `test.sh` 的真实读路径与 task.toml 顶层 artifacts 源路径。除默认 `/logs/artifacts/` 外，其他所需输出须明确移交；Verifier 保持源绝对路径，artifacts 的 destination 仅是宿主机收集位置，不能当作容器内重映射。教程运行时提交面是 `/workspace/solution`，其他任务可在 `submission_paths` 据实声明。源码 `solution/solve.sh` 为可选 Oracle，与运行时提交面不同。静态路径通过不表示已完成构建、物理隔离或动态传输。

## 六项复核

| ID | 阅读与判断 |
|---|---|
| H01 任务目录与 Hidden | 选定真实 task 根；核对 instruction.md、task.toml、双 Dockerfile、测试入口及本地依赖。通过 hidden_review 检查实际私有材料/生成/注入与评分器调用，接受等价目录。普通 Linux 单步任务使用 tests/test.sh；Oracle 入口可选。多步/Windows 按目标版本，不把半包当完整任务。 |
| H02 版本与配置 | 读完整 TOML，检查语法、字段类型、配置位置、目标版本以及显式 separate。不能把 TOML 可解析等同原生 schema 验证。Python 3.11+ 的 tomllib 可用于语法检查；其他环境用可信解析器或保留未完成。自定义字段须有真实适配。 |
| H03 环境、路径与提交物 | 分别从 environment/、tests/ 检查双镜像 COPY、WORKDIR、入口、依赖与可见边界；测试启动 cwd 不保证 /tests。沿评分调用确认提交物路径、必要模型/配置及 artifacts 覆盖。历史 task-root profile 需适配依据，不能照搬私有 tests 到 Agent。确定路径错误强制 fail，动态语法未能解析保留 manual。只读检查不认证完整物理隔离、CPU/GPU 上限或12h稳定性。 |
| H04 测试入口与 reward | 沿 test.sh 实际调用链检查必填参数、标量主分数及失败处理；须写 /logs/verifier/reward.txt 的有限数值，或 reward.json 非空有限数值对象。两者并存按优先 reward.json 核对。自定义 result.json、stdout score、注释或死代码不算接口完成。合法负数或 >1 与业务归一化门槛分开判断。 |
| H05 Job/Trial 调用配置 | 有配置时核对所选 task/provider/agent、有效覆盖、启动条件、verifier 未被禁用；检查重复键、错误路径、空选择和明确冲突。单任务 CLI 可用，不强制 job.yaml；确无配置可 not_applicable，已有反证不能跳过。 |
| H06 NOP 自检记录 | 必交当前题包版本的一次 NOP Trial。核对同一次运行的 config.json、result.json、reward、日志、任务版本与双镜像对应关系、separate、结束/异常状态及分数一致性。缺失时 H06 fail、runtime_status=not_run；材料已交但不完整/版本无法解释则 manual，明确接入错误则 fail。其他 Agent Trial 只能作补充材料。 |

H01–H04 与 H06 必查；H05 按已有材料适用。缺少同版本 NOP 记录时 QA17 fail。报告必须同时列静态结论与运行状态；完成 NOP 仍不能替代真实 Baseline/Reference、Agent 迭代或正式评分证据。

## NOP 自检（必交）

NOP 是 no-operation Agent：不调用模型求解，也不修改初始工作区；已有 Starter 会保留，所以不能把 NOP 一律称为“空提交”。它让 Harness 走完启动、Agent 结束、提交物移交和独立 Verifier 的实际执行分支，帮助发现构建、入口、缺依赖、路径及 reward 收集问题。若无提交触发早退，它只覆盖那条分支，不能证明完整 Hidden 评估或有效候选可运行。

专家或平台须在具备 Docker 能力、目标 Harbor 版本一致的环境选择 `nop` 跑一次，保留任务版本对应依据，以及同一 Trial 的配置、结果、reward 和日志。平台已有同版本记录可以复用，不要求重复跑，更不用新增一条10小时求解轨迹。耗时取决于构建与评分器，不能承诺一定很快；本只读 Skill 不自动发起运行。

检查的是是否符合题目约定的处理行为，而非 NOP 得分高低。0 分可能是预期，也可能来自错误早退，必须结合异常、退出状态与评分日志判断。完成了 NOP 仍不能替代公开 Dev 的实际反馈、有效提交的正式评分、模型独立重载与 Baseline/Reference 改善证据。

## 自动证据校验与报告

H06 自动校验支持单步 NOP Trial：config/result 的 Agent 身份一致且为 nop、result 声明 separate、有结束时间且无异常、verifier_result.rewards 与优先 reward 文件一致、必要配置/日志非空且证据属于同目录。任务身份、版本及历史路径通过运行材料与 trial_task_binding 复核；同名不等于同版本。自动检查不鉴定日志真伪，多步或不支持的版本标 manual，不伪造单步材料。

Harbor 的 agent/trajectory.json 若为 ATIF，按其声明版本识别；专家 AutoResearch 轨迹仍采用八字段，不互套 schema。artifacts 配置存在不等于文件已成功移交；已有运行记录应检查实际移交结果。源码参考解、私有轨迹不应成为 Agent 前置上下文。

报告保留21行，QA17 汇总 H01–H06；细项与路径证据写入 report.json.harbor。已有记录一致只称“已有 NOP 自检记录支持当前版本构建与运行可用，非独立复跑”；无材料显示 not_run，并将 H06 与 QA17 标为 fail。

只有用户另行要求动态验证时，才依据目标 Harbor CLI help、provider、隔离环境和授权范围制定命令。在授权的可丢弃环境运行并保留配置、任务版本、日志、reward 与 Trial 结果；不在宿主机直接执行未知 test.sh/solve.sh 或导入候选模块。未实际运行不得声称动态验证完成。
