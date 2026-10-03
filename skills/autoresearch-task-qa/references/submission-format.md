# 新版提交格式与优化面证据检查

本规范用于检查专家提交包是否接近当前推荐格式。格式偏离与业务实现缺陷分开判断：缺少完成质检所必需的材料可使相关 QA 项失败或未完成；多交文件、采用等价内部布局或保留有用附件本身不算错误，但必须在报告中列出并给出精简建议。

## 1. 推荐总结构

```text
submission_root/
├── workspace/
│   ├── harbor_task/                 # 完整平台任务包，不是全部交给 Agent
│   │   ├── instruction.md
│   │   ├── task.toml
│   │   ├── environment/
│   │   │   ├── Dockerfile            # Agent 镜像
│   │   │   ├── requirements.txt
│   │   │   ├── public_assets/
│   │   │   ├── public_eval/          # 公开 Dev 入口，目录名可等价
│   │   │   └── starter/
│   │   ├── solution/                # 可选 Oracle；不是 Agent 运行时提交目录
│   │   └── tests/                   # 仅进入独立 Verifier 镜像
│   │       ├── Dockerfile            # Verifier 镜像
│   │       ├── grader.py / test.sh
│   │       └── hidden_assets/       # 私有材料示例；也可其他目录/有依据的生成或注入
│   └── reference/                   # 出题方参考实现，Agent 不可见
├── expert_evidence/                 # 专家轨迹、最终方法与过程说明
└── optimization_evidence/           # Baseline/Reference 优化面证据
```

Dockerfile 位置、构建上下文和入口按 docker-path-contract.md 核对。`task.toml` 须显式设置 `[verifier] environment_mode = "separate"`。公开 Dev 入口及其数据必须进入 Agent；若原来与私有评分混在 tests/，先拆分公开部分，再由 Agent 构建或平台交付。上图 tests/ 指拆分后的私有 Verifier 内容。Hidden 材料必需，但不以目录名、是否训练或固定文件数量判断；预置、生成、安全注入的实际来源与调用关系在 hidden_review 中复核。Verifier 依赖须在自身镜像准备，不能依赖 Agent 临时安装包、缓存或未移交文件。

当前推荐结构不设置 `environment/trusted/` 或 `tests/runtime/` 中间层。发现这些旧目录时列为格式对齐建议，说明可将最终私有评分实现归入 `tests/`，公开 Dev 仍留在 Agent 可见部分；只有调用链确实失效或暴露内容时，才映射到 QA 失败。

Agent 运行时提交目录以题面为准，本教程采用 `/workspace/solution`，由 Starter 初始化。结束前恢复 Dev 最佳候选及必要模型/配置；平台按 artifacts 声明移交，在 Verifier 中保持原绝对路径。源码 `harbor_task/solution/solve.sh` 是可选 Oracle，缺少它不等于缺少运行时提交物。

### 两条专家轨迹

推荐放在 `expert_evidence/trajectory_codex.json`、`trajectory_seed.json`，等价命名应在 overview.source_path 中明确映射。每轮必含 `round`、`policy_name`、`method_summary`、`status`、`score`、`failure_reason`、`retained_best`、`time`。成功须记录有限数值分数；失败保留真实状态与失败原因，分数未知用 null；最佳标记为布尔值，时间须可解析。轮次与顺序、分数方向、最佳候选和 run_summary 相互一致，不补造记录。自动解析不能代替任务身份、实际有效时长或探索质量复核。

必交一次当前题包版本的 NOP 自检，可复用平台已有同版本记录。保留同一次 Trial 的配置、结果、reward、日志及任务版本对应依据，确认构建和独立 Verifier 链路正常。无需新增一条长时 Agent 轨迹，详见 harbor-harness.md。

## 2. optimization_evidence 最小结构

```text
optimization_evidence/
├── README.md                       # 推荐
├── 训练证据说明.md                 # 必需
├── baseline_runs/
│   ├── seed_<真实seed>/
│   │   ├── result.json
│   │   ├── run.log
│   │   └── model/                 # 仅训练型任务存在
│   │       ├── model.<实际格式>
│   │       ├── artifact.json
│   │       └── reload.log
│   └── ...
├── reference_runs/
│   ├── seed_<真实seed>/
│   │   ├── result.json
│   │   ├── run.log
│   │   └── model/                 # 仅训练型任务存在
│   └── ...
└── comparison_summary.json
```

其目的不是记录 Agent 搜索过程，而是在数据、训练、评测、资源和随机性口径固定时，证明 Reference 相比 Baseline 存在真实、稳定、可复现的改善。Reference 不等于标准答案，也无需是最优方法。

### Seed 规则

- 数量以正式协议声明为准，不固定为三组；用了五个 seed 就完整提交五组 Baseline 与五组 Reference。
- 两者使用完全相同的正式真实 seed 集，逐 seed 配对。训练型任务按协议从各自初始化独立运行；合法共享的预训练起点须声明，不能复制同一完成训练的模型冒充多轮。随机评估同一模型的重复测量另外标明evaluation_replicate，不冒充training_seed。
- 不抽样、不重命名、不挑最好结果，不把同一 checkpoint 当作多轮训练。
- 失败运行保留原始日志并标为 `INVALID`。制作期可标 `NOT_RUN`，但不能用占位分数、假日志或空模型计入正式结论。

### 每轮 result.json

至少检查这些顶层字段：

```text
schema_version, status, role, seed, task_type,
method, protocol, training, execution, metrics,
quality_gate, artifacts
```

- `role` 与所在目录一致；`seed` 与目录名一致。
- `method` 记录源码路径、源码 SHA-256 和必要固定依赖哈希。
- `protocol` 记录数据边界、训练/评测口径、指标方向、质量门和资源限制。
- `execution` 记录真实命令、时间、退出码和耗时；历史材料确实缺失时填 `null` 并解释，不能反推。
- `metrics`、`quality_gate` 与日志和汇总一致；`artifacts` 使用相对提交包根目录的路径并可追溯。

### run.log

应为原始 stdout/stderr，包含 warning 与失败信息，不能只是人工整理后的最终分数。静态质检只能检查存在、非空及明显摘要化迹象；不能把日志存在写成真实性认证。

### 模型产物

训练型任务的每个 seed 应保存真实 checkpoint、`artifact.json` 和 `reload.log`。核对模型格式、大小、SHA-256、role、seed、method hash、来源 result、独立重载状态及复评分差值。模型缺失、损坏、哈希不一致或无法重载时，该轮不是完整训练证据。

非训练型任务不创建空 `model/`；在 `result.json` 中标记 `NOT_APPLICABLE`。若产生其他核心产物，在 `artifacts` 中记录路径和哈希。

### comparison_summary.json

至少核对：完整 seed 列表、逐 seed B/R 值与配对改善、均值、样本标准差、绝对/相对改善、显著性规则、聚合质量门、模型完整性和源 result 路径。

```text
越低越好：paired_improvement = baseline - reference
越高越好：paired_improvement = reference - baseline
```

正数统一表示 Reference 更好。只有正式协议要求的全部成对运行均完成且有效、必要模型验证成功时，汇总才能为 `COMPLETE`。若提供原始值，应独立复算关键聚合量，不只信任 `passed: true`。

## 3. 非标准内容的处理

以下材料不再是轻量版 `optimization_evidence/` 的最小必交项：

- `data_manifest.json`、`experiment_plan.json`、`surface_validation.json`、`surface_diff.patch`、`integrity_manifest.json`；
- `train_config.yaml`、分开的 `run_config.json` / `metrics.json`；
- `ablation/`、额外的 `runs/` 中间层；
- GPT/Seed Agent 候选结果、Agent 轨迹、best method 与专家过程材料。

发现时不要仅因“多交”判错，按以下类型列出：

- `extra_allowed`：有用的额外说明或审计附件，可以保留；
- `merge_candidate`：与 `result.json` / `训练证据说明.md` 重复，建议合并以减轻包体；
- `misplaced`：内容有价值但目录职责不对，例如 Agent 候选复测或消融应移到 `expert_evidence/`；
- `obsolete_layout`：旧中间层或旧命名，建议按当前树扁平化。

格式建议与结论分开。只有偏离导致必需证据缺失、路径不可追溯、隔离错误或实际调用失败时，才在对应 QA 项中判失败。

## 4. 报告前置总览

最终 `report.txt` 和 `report.md` 在质检结论之前依次写：

1. **优化面及方法介绍**：允许修改什么、固定什么、主要指标方向和质量门；分别介绍Baseline/Reference实际方法和差异，不只罗列数字。
2. **Baseline 与 Reference 跑分**：列全部正式 seed 的成对值、均值/样本标准差、质量门、归一化分数和显著性结论；缺少原始数据时明确不可复算。
3. **G01–G03内容门结论**：研究优化面、Baseline合理性、Reference提升充分性；按research-quality.md复核。随后列**两条轨迹迭代概况**：分别说明 Agent 模型、打包轨迹轮数、声明有效轮数/时长、主要探索阶段、采用与放弃方向、最终结果及证据缺口。
4. **格式对齐建议**：列缺失项与所有超出推荐结构的项目；额外项明确标注“建议，不单独扣错”。之后完成21项表与可复制的专家退回说明；两条轨迹默认各10h，只有不涉及训练且单轮迭代很短、例外证据完整时可各7h。

“Seed 2.1 Turbo High”中的 Seed 是 Agent 模型名，`seed_20260911` 等才是训练随机种子；报告中建议分别使用 `agent_model` 与 `training_seed`。
