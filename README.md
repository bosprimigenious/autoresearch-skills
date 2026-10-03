# AutoResearch Skills

面向研究型 Coding Agent 的可复用工作流：从论文发现、题目设计和可信 Baseline，到双轨运行、成本控制、质量验收与可执行交接。

[![CI](https://github.com/bosprimigenious/autoresearch-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/bosprimigenious/autoresearch-skills/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-8-6d28d9.svg)](skills)
[![Privacy Gate](https://img.shields.io/badge/privacy-fail--closed-0f766e.svg)](scripts/privacy_scan.py)

AutoResearch Skills 不是一组需要整段复制的 prompt。每个 skill 都是带清晰触发条件、执行边界、参考资料和可选脚本的独立能力单元；Agent 在任务匹配时读取相应 `SKILL.md`，而不是一次加载整个仓库。

## Quickstart

### 使用跨 Agent 安装器

先查看仓库中可安装的 skill：

```sh
npx -y skills add bosprimigenious/autoresearch-skills --list
```

安装一个 skill：

```sh
npx -y skills add bosprimigenious/autoresearch-skills \
  --skill autoresearch-task-qa \
  --agent codex \
  --global \
  --yes
```

将 `codex` 替换为安装器支持的目标 Agent。也可以一次安装整套：

```sh
npx -y skills add bosprimigenious/autoresearch-skills --all
```

安装器是可选依赖；使用前应确认其来源、目标目录和即将安装的文件。安装或更新后，重新启动 Agent 或按宿主工具的方式重新加载 skills。

### 手动安装

克隆仓库，并把需要的 skill 软链到目标 Agent 的 skill 目录：

```sh
git clone https://github.com/bosprimigenious/autoresearch-skills.git
cd autoresearch-skills

ln -s "$PWD/skills/autoresearch-task-qa" \
  ~/.codex/skills/autoresearch-task-qa
```

推荐使用软链保持单一事实源。若目标环境不支持软链，再复制完整 skill 目录；后续更新时必须重新同步，避免旧副本继续生效。

## 如何使用

安装后可以直接用自然语言描述目标，宿主 Agent 根据 `name` 和 `description` 自动选择相关 skill。需要明确指定时，可使用宿主支持的显式调用形式，例如：

```text
使用 $autoresearch-paper-discovery，从公开论文中筛选三个可转化为 AutoResearch 任务的候选；先查许可、评测、资源上界和重复题状态。

使用 $autoresearch-run-isolation，为两条独立 Agent 轨迹设计 Docker、GPU 租赁、恢复和证据合同；先完成低成本 pilot，不要直接启动长跑。

使用 $autoresearch-task-qa，只读审查这个提交 ZIP。静态结论、已有运行证据和未复跑项必须分开报告。

使用 $autoresearch-feishu-three-table，在当前已授权的飞书资源中核对当期题号，再填写领题、提交/验收或组长初检记录；权限不足时只生成待填草稿，不猜字段或绕过权限。
```

Skill 提供的是决策规则和工作流，不会扩大任务授权。安装 QA skill 不等于允许它运行不受信容器；安装运行隔离 skill 也不等于允许购买 GPU、调用付费 API 或修改远端平台状态。

## Skill Catalog

| Skill | 何时使用 | 主要产出 | 关键停止条件 |
|---|---|---|---|
| [`autoresearch-paper-discovery`](skills/autoresearch-paper-discovery) | 从零找论文、维护候选池、专家自主选题或池内快选 | 去重候选账本、许可与权威查重证据、推荐/补证/拒绝结论 | 任一硬门槛失败即拒题；未知项不能写成通过 |
| [`autoresearch-optimization-surface`](skills/autoresearch-optimization-surface) | 判断论文或代码是否存在方法级优化空间 | 可修改面、冻结面、反例和最小验证合同 | 只有超参数变化、无法隔离或不可复算时停止 |
| [`autoresearch-baseline-quality`](skills/autoresearch-baseline-quality) | 审查正式 Baseline 是否合理、公平、可复现 | Baseline 质量结论、偏差来源与修复清单 | 预算、seed、实现或评测口径不公平时不得进入正式比较 |
| [`autoresearch-task-authoring`](skills/autoresearch-task-authoring) | 将论文与代码仓转化为可交付研究题 | Starter、Baseline、Reference、评分器、冻结合同和五阶段 QA 证据 | selection、pilot、container、long-run、release 任一累计门失败即暂停下游工作 |
| [`autoresearch-run-isolation`](skills/autoresearch-run-isolation) | 设计双轨运行、Docker、GPU/API 成本、恢复与血缘 | 运行合同、容量计划、隔离拓扑、快照与 lineage | pilot 未过、容量风险不可接受或恢复证据缺失时停止消费 |
| [`autoresearch-task-qa`](skills/autoresearch-task-qa) | 对任务目录或 ZIP 做独立只读审查 | TXT、Markdown、JSON 报告及可执行整改项 | 研究质量、实现、Harbor、隐私或证据门失败即 `NOT READY` |
| [`autoresearch-feishu-three-table`](skills/autoresearch-feishu-three-table) | 在授权的飞书多维表格中流转领题、完成提交/验收和组长初检 | 当期题号核验、最小字段变更、权限交接草稿与写后回读 | schema/权限不可验证、跨表身份冲突或回读不一致时停止 |
| [`autoresearch-conversation-handoff`](skills/autoresearch-conversation-handoff) | 长会话收尾、切换 Agent、恢复中断工作 | 已验证事实、失败、阻塞、文件入口和下一步命令 | 对话陈述与现场证据不一致时，以现场为准并保留差异 |

## 推荐应用方式

### 1. 从零创建一项 AutoResearch 任务

```text
paper-discovery
  → optimization-surface
  → baseline-quality
  → task-authoring
  → run-isolation
  → task-qa
  → feishu-three-table
  → conversation-handoff
```

推荐顺序：

1. 用 `paper-discovery` 在候选池或专家自选模式下建立证据账本。
2. 用 `optimization-surface` 排除纯调参、不可隔离或不可复算方向。
3. 用 `baseline-quality` 冻结公平的正式对照。
4. 用 `task-authoring` 依次通过 selection、pilot、container、long-run 和 release 门。
5. 在需要双轨迹、付费 GPU 或服务器 Docker 时使用 `run-isolation`；先小规模验证，再购买连续容量。
6. 交付前由 `task-qa` 独立检查。作者自测不能替代独立 QA。
7. 需要登记外部状态时，用 `feishu-three-table` 从当期权威题号开始，分别处理领题、提交/验收和组长初检；QA 通过不自动等于外部表已回填。
8. 会话中断或更换 Agent 时，用 `conversation-handoff` 保存可继续执行的状态。

### 2. 已有题目，只想避免昂贵返修

最小组合：

```text
optimization-surface → baseline-quality → task-authoring → task-qa
```

先检查题目是否成立，再检查证据是否成立。不要先跑十小时轨迹，最后才发现优化面过窄、效应低于噪声、Baseline 不公平或 Verifier 路径不通。

### 3. 只做 GPU、Docker 与双 Agent 长跑

使用 `autoresearch-run-isolation`。推荐资源策略是：

```text
本地/小卡功能验证
  → 目标 GPU 按小时端到端 pilot
  → 比较小时、包日与混合窗口
  → 两条独立 lane 正式运行
  → 机外快照与新容器恢复
```

服务器“预装 Docker”只减少安装工作，不证明 Agent/Verifier 双镜像、GPU runtime、Hidden 隔离或目标 Harness 已通过。使用内置的宿主预检后，仍需真实构建和 trial receipt。

### 4. 只验收一个现成交付包

使用 `autoresearch-task-qa`，保持只读：

```sh
python3 skills/autoresearch-task-qa/scripts/audit_task.py \
  /path/to/artifact.zip \
  --out-dir /path/to/qa-report
```

该命令生成静态报告，不会运行任务代码、构建 Docker 镜像或独立复现实验。最终报告必须把静态结论、包内已有证据和本次未验证的运行状态分开。

## 设计原则

- **硬门槛不做总分补偿。** 低成本、无需 GPU 或论文热门，不能抵消许可证、评测、效应、重复题或交付失败。
- **QA 前移。** selection、pilot、container、long-run、release 逐级累积，修复最早失败的门后再继续。
- **运行血缘不可拼接。** 一个正式结果只绑定一个真实 trial；源码、配置、seed、receipt、artifact 和 checkpoint 必须一致。
- **静态与动态证据分离。** 文件存在、Dockerfile 可解析和容器能启动，分别不能证明完整训练或目标 Harness 通过。
- **成本服从证据。** GPU 小时/包日、API 套餐/按量都在 pilot 后决策；追加预算不能修复错误协议。
- **公开发布默认去隐私。** 主包、自检、轨迹和证据附件逐件扫描，任何附件失败都阻止发布。

## Repository Layout

```text
autoresearch-skills/
├── skills/
│   └── <skill>/
│       ├── SKILL.md         # 唯一指令源
│       ├── AGENTS.md        # 由 SKILL.md 生成
│       ├── CLAUDE.md        # 由 SKILL.md 生成
│       ├── agents/          # UI 元数据与调用策略
│       ├── references/      # 按需读取的规范与协议
│       ├── scripts/         # 确定性检查和回归测试
│       └── assets/          # 需要复制到输出的通用资源
├── scripts/
│   ├── validate_skills.py
│   ├── sync_formats.py
│   └── privacy_scan.py
└── .github/workflows/ci.yml
```

`SKILL.md` 保留共享工作流与路由；条件性细节进入 `references/`；重复、脆弱或必须确定执行的逻辑进入 `scripts/`。不要手工分别维护 `AGENTS.md` 与 `CLAUDE.md`。

## 验证与开发

仓库要求 Python 3.12 兼容。提交前运行与 CI 一致的聚合门禁：

```sh
export PYTHONDONTWRITEBYTECODE=1

python3 scripts/validate_skills.py
python3 scripts/sync_formats.py --check
python3 scripts/privacy_scan.py

python3 -m unittest discover \
  -s skills/autoresearch-task-qa/scripts -p 'test_*.py'
python3 -m unittest discover \
  -s skills/autoresearch-paper-discovery/scripts -p 'test_*.py'
python3 -m unittest discover \
  -s skills/autoresearch-run-isolation/scripts -p 'test_*.py'
python3 -m unittest discover \
  -s skills/autoresearch-task-authoring/scripts -p 'test_*.py'
python3 -m unittest discover -s scripts -p 'test_*.py'
```

修改 `SKILL.md` 后，先重新生成兼容格式，再复核差异：

```sh
python3 scripts/sync_formats.py
git diff --check
```

新增或修改 skill 时遵循以下规则：

1. `name` 与目录名一致，`description` 明确说明适用场景与边界。
2. 只加入会改变 Agent 决策的内容；通用知识和维护过程不进入 skill 正文。
3. 参考资料必须从 `SKILL.md` 路由到具体使用条件，避免一次加载全部上下文。
4. 新脚本必须有行为测试；测试覆盖真实不变量，而不是只匹配文案。
5. 失败记录、未知状态和未运行项必须保留，不能为了 README 或 CI 变绿而软化。
6. 开源前运行严格隐私门禁，不提交对话原文、个人路径、凭据、私有端点或未授权附件。

## Security and Evidence Boundary

Skill 是可执行工作流的一部分，也是 Agent 的信任边界。只安装已审查的版本，并在启用第三方 skill 前阅读其 `SKILL.md`、脚本和权限要求。

本仓库能够验证 skill 结构、派生格式、静态规则、隐私门禁和回归测试。它不会把下列事项表述为已经完成：

- Docker 镜像已在目标服务器成功构建并运行；
- 外部平台、模型 API、GPU 库存或 Harbor backend 当前可用；
- 真实训练达到目标指标或两条轨迹满足有效时长；
- 第三方论文、代码、数据、模型或附件具有公开再分发许可；
- 静态扫描已经替代人工隐私、版权或商业秘密复核。

运行时成功必须由真实命令、日志、receipt 和产物证明。详细隐私边界见 [`privacy-and-portability.md`](skills/autoresearch-task-qa/references/privacy-and-portability.md)。

## Ecosystem References

本仓库的组织方式参考公开 Agent Skills 生态中的通用做法，同时保留自己的 fail-closed 研究门禁：

- [Agent Skills open specification](https://agentskills.io/specification)
- [OpenAI: Build skills](https://developers.openai.com/plugins/build/skills)
- [Vercel skills CLI](https://github.com/vercel-labs/skills)
- [NVIDIA Agent Skills](https://github.com/NVIDIA/skills)
- [Anthropic Skills](https://github.com/anthropics/skills)

这些链接只作为格式、安装和安全设计参考，不表示兼容性或功能经过对方认证。

## License

[MIT](LICENSE)
