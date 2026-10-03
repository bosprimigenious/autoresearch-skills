# Docker 与运行路径契约

这是通用的静态交付检查。目标是避免把错误的构建上下文和绝对路径提交到平台；静态通过不代表镜像已构建或平台适配已部署。

## 依据和真实附件缺口

`teaching-task-root-v1` 表示一种历史任务根布局，其中 `workspace/harbor_task/` 包含 `environment/Dockerfile`、`task.toml`、`tests/test.sh` 与可选 `solution/solve.sh`。典型旧式 Dockerfile 包含：

```dockerfile
ENV EVFI_TASK_ROOT=/workspace
COPY environment/requirements.txt /tmp/requirements.txt
WORKDIR /workspace
COPY instruction.md task.toml ./
COPY environment/starter ./environment/starter
COPY environment/public_assets ./environment/public_assets
COPY solution ./solution
COPY tests ./tests
```

该 profile 要求 **任务根目录**作为 build context；Dockerfile 放在 `environment/` 不代表 context 也在 `environment/`。当前独立 Verifier 契约要求采用可验证的 Agent 与 Verifier 双镜像构建；旧示例中的 `COPY tests ./tests` 会把私有评分材料带入 Agent 镜像，不能沿用。

布局示例只能证明路径约定。自定义配置不自动证明目标 Harbor 版本识别这些字段；只执行单元测试而未写 reward 的 `tests/test.sh` 也不构成完整 Harness 接口。不能把静态示例称为已经通过平台构建验证的模板。

## 两种受支持 profile

下表路径均不包含 ZIP 外包装；`T` 是 `task.toml` 所在任务根。教学完整包推荐 `T=workspace/harbor_task`。直接送审独立任务目录时，`T=.`，目录职责与完整包结构另由格式检查判定。

| 项目 | `teaching-task-root-v1`（历史示例） | `harbor-environment-v1`（当前默认） |
|---|---|---|
| Agent Dockerfile | `T/environment/Dockerfile` | `T/environment/Dockerfile` |
| Agent build context | `T`，必须证明不会复制私有评分材料 | `T/environment` |
| Verifier Dockerfile / context | `T/tests/Dockerfile` / `T/tests`，须有平台适配证据 | `T/tests/Dockerfile` / `T/tests` |
| COPY 来源 | 相对 `T`，例如 `environment/starter` | 相对 `T/environment`，例如 `starter` |
| 工作目录与任务根 | 最终 `WORKDIR /workspace`；声明的 `TASK_ROOT`/`*_TASK_ROOT` 与其一致 | 由实际镜像/版本明确指定，不能假设 `/workspace` |
| Agent 提交面 | `/workspace/solution` | 依据任务接口；原生 Oracle 的 `/solution` 不能直接当 Agent 可写工作区 |
| 测试入口 | 由适配器在独立 Verifier 镜像中提供 `/tests/test.sh` | Verifier 镜像自行提供 `/tests/test.sh`；启动 cwd 不保证是 `/tests` |
| 源包入口 | `T/tests/test.sh`、`T/solution/solve.sh`；其他配置入口须真实存在 | `T/tests/test.sh`；`solution/solve.sh` 作为 Oracle 按需存在 |

历史教学 profile 的等价 Agent 构建命令（只用于描述，不由静态 QA 执行）：

```bash
docker build -f workspace/harbor_task/environment/Dockerfile workspace/harbor_task
```

禁止把 context 改成整个提交包后将 Reference、专家私有证据或最终私有评分材料复制给 Agent。公开 Dev 评分代码及数据必须保留供 Agent 使用，不能一概禁止“评分实现”或所有名为 tests 的文件。Verifier 必须具备正式入口、私有评测材料及独立依赖；Hidden 可用其他目录或有证据的生成/安全注入。禁止依赖专家本机绝对路径或 `../` 越出 context。构建命令、provider、COPY、WORKDIR、环境变量和入口必须形成一致路径链。

原生 profile 的依据见 [Harbor 规范](harbor-harness.md)，复核时说明目标版本。不能仅为规避路径错误改选 profile。教学布局须有真实适配证据，且不能暴露私有 tests 内容。必交一次当前题包版本的 NOP 自检记录；未提供时 H06 与 QA17 fail、运行状态记 not_run，但不得把缺记录误写成已证明构建失败。

## 自动检查与人工边界

`scripts/docker_paths.py` 只读处理常见 Docker shell/JSON COPY 与 ADD、默认反斜线续行、WORKDIR、TASK_ROOT 环境变量、CMD/ENTRYPOINT 的显式脚本路径、简单 dockerignore，以及 TOML 中静态入口。分别解析 Agent 和 Verifier 的 Dockerfile，检查各自 context 内来源、复制映射、忽略规则、越界链接和入口位置；Verifier 子结果及带 verifier_ 前缀的 findings 汇总到路径结论。Dockerfile 相邻的 `Dockerfile.dockerignore` 优先于 context 的 `.dockerignore`。

确定缺 Agent/Verifier Dockerfile、混用 context、Agent 复制已识别私有 Hidden/Reference 材料、来源不存在、显式 WORKDIR/TASK_ROOT 错误、简单 ignore 排除必需来源、入口不存在、静态无法映射且没有生成步骤等，标 `fail`。H03 自动不通过，不能用手填 pass 或 `manual_resolution` 覆盖。

变量、远程 ADD、ADD 解包、多阶段及 COPY --from、路径相关扩展 flags、复杂 dockerignore、内嵌 shell、RUN 生成文件等不能完整解析时标 `manual`。不把阶段内来源当成本地文件，也不将“不支持解析”写成“平台一定构建失败”。须阅读调用链后补充真实证据。检查器不模拟镜像基础文件系统，不证明构建可达性、依赖安装成功、权限有效或任意 shell 命令正确；这些仍是 H03 的人工语义复核范围。

## review 与输出字段

最终 `review.harbor.path_contract` 必须显式填写 profile 和来源，旧 review 不能默认冒充新契约已通过：

```json
{
  "profile": "harbor-environment-v1",
  "profile_basis": "目标 Harbor 版本的独立 Verifier 环境与双 Dockerfile 构建契约"
}
```

可选 `dockerfile`、`build_context`、`runtime_task_root` 用来显式声明路径，前两种 profile 必须与计算结果一致（原生 profile 的 runtime task root 可按真实镜像声明）。`adapter_evidence` 是真实包内适配文件引用数组，不能只填口头声明。

仅对已标 manual 的动态解析项，可填写：

```json
{
  "manual_resolution": {
    "summary": "说明每个未解析项的最终路径和解析依据，最多 500 字。",
    "evidence": ["workspace/harbor_task/environment/Dockerfile:12", "workspace/harbor_task/build.sh:4"]
  }
}
```

字段是合并到同一个 `path_contract` 对象，并非另建 review。所有引用必须存在于送检包内。可采用 `custom` profile，但需填写三项路径和适配证据；它用于已有平台适配，不是任意忽略教学路径约束的豁免。无充分适配说明维持 manual。

`report.json.harbor.path_contract` 保存 `profile/profile_basis/sources/task_root/dockerfile/build_context/runtime_task_root/test_entry/observed_workdir/copy_operations/findings/status`，以及 Verifier 子结果、适配证据和人工复核说明。每条 finding 含 `code/status/message/evidence`。当前契约要求双 Dockerfile；只有预构建镜像不满足该独立构建交付要求。任何路径 fail 都强制 H03 与 QA17 不通过；manual 无补充复核时 H03 不能通过。所有 profile 均不允许由静态结果声称动态构建成功。

退回建议示例：

> Agent 构建包含了私有测试材料。请将公开 Dev 部分提供给 Agent，并把最终私有评测放入以 tests/ 为上下文的独立 Verifier；核对 /tests/test.sh、依赖、Hidden 的准备/调用与 Agent 产物移交。静态路径一致不代表已经完成镜像构建或评分运行。
