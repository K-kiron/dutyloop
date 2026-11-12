<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/hero-light.svg">
    <img src="docs/assets/hero-light.svg" alt="DutyLoop — 面向仓库开发的 super-agent，协调你现有的编程工具" width="1200">
  </picture>
</p>

<p align="center">
  <strong>面向仓库开发的 super-agent，协调你现有的编程工具。</strong><br>
  持续规划、实现、验证并跟进仓库任务<br>
  运行预算明确，关键决定由你掌握。
</p>

<p align="center">
  <a href="https://github.com/K-kiron/dutyloop/actions/workflows/ci.yml"><img src="https://github.com/K-kiron/dutyloop/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11+"></a>
  <a href="#项目状态"><img src="https://img.shields.io/badge/status-alpha-D7A34B" alt="项目状态：Alpha"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3B8175" alt="MIT 许可"></a>
</p>

<p align="center">
  <a href="README.md">English</a> · <strong>简体中文</strong><br>
  <a href="#快速开始">快速开始</a> ·
  <a href="#使用当前桌面-agent">桌面 agent</a> ·
  <a href="#运行项目任务">运行项目任务</a> ·
  <a href="#处理-github-issue">处理 issue</a> ·
  <a href="#持续维护仓库">持续维护仓库</a> ·
  <a href="docs/getting-started.md">文档</a> ·
  <a href="https://github.com/K-kiron/dutyloop/issues/new/choose">报告问题</a>
</p>

---

DutyLoop 是基于现有编程工具的**本地、持久化仓库开发 super-agent**。给它一个仓库、一份持续职责和验收命令，它会检查项目，规划有证据支持的工作，并在你设定的范围和运行上限内，协调实施、验证和单独复核。

通过验收的改动会积累在**独立的本地 Git 分支**上，并保留任务依据、检查结果和复核记录。 每项任务通过后，DutyLoop 会继续发现工作，直到达到运行上限、遇到阻塞，或收到你的停止指令。

已有待办 issue 时，可以使用 [issue 模式](#处理-github-issue)：结合仓库当前阶段分析捕获的开放 issue，形成分阶段路线图，再详细规划并实施下一项可执行工作。默认产出本地分支；也可以明确配置创建 PR、等待 CI 后合并，以及验收后关闭 issue。

需要持续负责仓库时，使用[仓库维护模式](docs/repository.md)：增量接收新 issue，
持续跟进由 DutyLoop 创建的 PR 的 CI 和评审，将需要人决定的事项集中显示。默认先观察和规划，
检查后可明确开启自动实施；所有尝试共用原有预算。

## 工作流程

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/assets/workflow-mobile-dark.svg">
  <source media="(max-width: 600px) and (prefers-color-scheme: light)" srcset="docs/assets/workflow-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/workflow-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/workflow-light.svg">
  <img src="docs/assets/workflow-light.svg" alt="发现有证据支持的工作，在 worktree 中实施，运行操作者指定的检查，单独调用复核，再提交通过验收的改动并继续发现工作" width="1200">
</picture>

**按阶段协调多个 agent：**1–3 个发现 agent 可以并行检查项目；实施、操作者指定的检查和单独复核随后依次进行，每次推进一项改动。验收通过后，结果保存在持久状态中，并继续发现下一项工作。

你一次性定义职责和验收命令。DutyLoop 管理 worktree、阶段额度预留、暂存快照检查、复核和持久状态。可以通过 MCP 使用当前本地桌面开发 agent，也可以选择原生开发 CLI 适配器。

| 能力 | 带来的作用 |
| --- | --- |
| **持续发现工作** | 1–3 个发现 agent 检查当前证据，提出带有验收标准的候选任务。 |
| **持续协调仓库** | 增量接收 issue、持久化输入记录、维护由 DutyLoop 创建的 PR，并集中显示待人工决定的事项。 |
| **专注交付** | 实施和接纳保持串行，每次尝试使用受管理的 worktree。 |
| **由你掌握的检查** | 将暂存文件还原为可运行的快照后执行你的命令；检查期间发生改动时，不予接纳。 |
| **可审阅的结果** | 已接纳的提交、经检查的 Git 树 ID、任务反馈，以及保留的拒绝或中断记录。 |
| **持久控制** | SQLite 状态、审计事件、后台运行、显式停止和需确认的中断恢复。 |
| **有上限的执行** | 限制调用次数和经过时间，并为实施及必需的复核预留调用额度。 |

持续职责模式产出本地分支，不会 push、创建 PR、合并或部署。issue 模式同样默认保留本地产出，并提供单独配置的 GitHub 交付流程。

## 快速开始

**环境要求：**Python 3.11+、Git，以及已配置的 Git 作者身份。 建议使用虚拟环境；[安装说明](docs/getting-started.md#prerequisites)涵盖 Windows、macOS 和 Linux。 目前从源码仓库安装。

```console
git clone https://github.com/K-kiron/dutyloop.git
cd dutyloop
python -m pip install -e .
dutyloop demo --directory ../dutyloop-demo
```

使用一个不存在或为空的演示目录，无需模型账号或 API key。 **脚本演示**使用真实的编排引擎、Git worktree 和测试命令：接纳两个预设缺陷的修复， 继续发现工作，并在七次调用后停止。驱动器及其判断均由脚本模拟。

返回的 JSON 包含以下内容：

```json
{
  "simulated": true,
  "accepted_tasks": 2,
  "original_unchanged": true,
  "accepted_tests_passed": true
}
```

这里只展示演示结果的一部分。完整输出还包含任务快照和产物位置。 [验证记录](docs/validation.md)说明了离线检查与已认证宿主运行之间的区别。

## 使用当前桌面 agent

设置 `driver: {"name": "desktop"}`，让当前本地 Codex 或 Claude Code 桌面对话执行工作。DutyLoop 通过 MCP 提供各阶段提示，并负责验证、提交及已配置的 issue 交付。它不选择模型，也不启动另一个需要模型 CLI 登录的进程。

```console
python -m pip install -e ".[mcp]"
python -m dutyloop desktop-config --client codex --root /absolute/path/to/dutyloop-states --allow-project /absolute/path/to/project
```

Claude 使用 `--client claude` 输出 JSON 配置。命令只打印配置，由你合并到宿主的本地 MCP 设置；不会修改宿主设置或开始工作。将[桌面 issue 配置](examples/desktop-issues.json)或[持续任务配置](examples/desktop-mission.json)复制到目标仓库之外，替换路径和检查命令，再按[桌面交接指南](docs/desktop.md)连接并使用其中可直接粘贴的宿主提示。

宿主领取每个阶段，在 DutyLoop 指定的受管理 checkout 中执行，并按返回的 schema 提交结果。复核必须使用新的子 agent 或对话上下文。DutyLoop 无法终止桌面对话启动的 shell 或子 agent；提交或取消前必须完成或停止这些工作。目前尚未在实际 Codex/Claude 桌面应用中验证完整执行；仅连接 MCP 不会唤醒空闲对话。

## 运行项目任务

一份有用的职责说明，需要明确负责什么，以及哪些行为必须保持：

> 维护 CSV 导入和导出的可靠性。针对文档约定的行为，修复可复现的缺陷并补充回归测试。
> 保持公开 API 和文件格式不变。没有证据支持的工作时，等待。

通过[受支持开发 CLI](#宿主适配器)自身的流程完成安装和认证。 选择一个已有提交 `HEAD`、且检查能在新 worktree 中运行的仓库。 把下面的内容保存为 `mission.json`，放在**目标仓库之外**：

```json
{
  "mission": "Maintain CSV import/export reliability. Fix evidenced failures with regression tests. Preserve the public API and file format. Wait when there is no supported work.",
  "project": "/absolute/path/to/project",
  "allowed_paths": ["src", "tests"],
  "verification": [
    ["python", "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
  ],
  "driver": {"name": "codex"},
  "limits": {"max_invocations": 12, "max_wall_seconds": 1800}
}
```

按你的项目替换项目路径、允许改动的路径和检查命令。 Windows 路径可以使用正斜杠，例如 `C:/work/my-project`。 [完整示例](examples/mission.json)还提供约束、工作类型和重试设置； [配置参考](docs/configuration.md)介绍全部字段。

检查环境和配置，然后初始化并运行：

```console
dutyloop doctor --driver codex --project /absolute/path/to/project
dutyloop validate --spec mission.json --state ../mission-state
dutyloop init --spec mission.json --state ../mission-state
dutyloop run --state ../mission-state
```

`doctor` 检查安装和版本，不验证认证状态，也不调用模型。 `init` 从已提交的 `HEAD` 创建职责分支和持久状态；未提交的改动不会复制到任务中。 状态目录应放在目标仓库之外，并确保新 worktree 能使用项目所需的依赖。

### 监控与控制

使用 `start` **替代 `run`**，启动后台进程：

```console
dutyloop start --state ../mission-state
dutyloop status --state ../mission-state --human
dutyloop events --state ../mission-state --limit 20
dutyloop stop --state ../mission-state
```

状态中可以看到职责分支、已接纳的提交、调用额度使用情况和任务结果。 停止是异步操作，需要再次查看状态确认完成。已经记录的停止状态不能通过重启清除。 [运行与排错](docs/operations.md)介绍阻塞、运行上限和中断处理。

## 处理 GitHub issue

准备已有代码库、已认证的开发 CLI，以及已认证的 GitHub CLI（`gh`）。将 [issue 配置示例](examples/issues.json)复制到目标仓库之外，替换项目路径、GitHub 仓库、允许修改的路径和检查命令；保留 `github.delivery: "local"`，先获取本地结果：

```console
dutyloop issues init --spec issues.json --state ../issue-state
dutyloop issues run --state ../issue-state --plan-only
dutyloop issues status --state ../issue-state --human
dutyloop issues run --state ../issue-state
```

初始化会捕获当前开放 issue 及其评论，不调用模型，也不修改 GitHub。`--plan-only` 分析整个已捕获待办，只生成全局分阶段路线图，不逐项生成详细实施计划。通过 `queue.planning_context` 提供当前里程碑或发布目标，`queue.planning_max_bytes` 限制每次请求大小。较大的输入按有界且完整的分块分析；存在未覆盖 issue 时，规划保持不完整。状态会显示覆盖情况、各阶段目标、进入与退出条件、人工验收门槛、待答问题和推迟理由。

执行新工作前必须有完整且有效的路线图。执行遵循阶段顺序和依赖，只针对当前基线上的下一项可执行 issue 详细规划，再依次实施、运行操作者检查并单独复核。前一阶段未完成时不能跳到后一阶段。`queue.batch_size` 限制单次实施的 issue 数量；分析、规划、重试和执行共用跨运行保留的调用与时间额度。

将 `github.delivery` 设为 `pull_request` 可发布 PR；设为 `merge` 并明确指定 `required_checks`，可等待 CI 后合并。启用 `close_issues: true` 后，合并模式会核对合并后的 Git 树并重新运行你的检查，再关闭需求未变化的 issue。运行或启动批次，即授权执行配置中选定的交付操作。结果不明的操作需要检查；一轮完成也可能仍有未解决的 issue。

自动合并还要求能够读取仓库已有的分支保护规则：必需检查要求分支保持最新，且对管理员同样生效。DutyLoop 不会修改这项策略；本地和 PR 模式不要求该规则。

[issue 工作流指南](docs/issues.md)介绍完整配置、后台运行、人工决定、中断恢复和交付边界。目前覆盖来自离线验证，尚未验证已认证的真实项目 issue 批次。

## 持续维护仓库

复制[仓库配置](examples/repository.json)到目标仓库之外，填写路径、GitHub 仓库、
验收命令和执行器。默认采用观察模式和本地交付：

```console
dutyloop repository init --spec repository.json --state ../repository-state
dutyloop repository run --state ../repository-state
dutyloop repository status --state ../repository-state --human
dutyloop repository attention --state ../repository-state
```

检查路线图后，用 `repository set-mode --state ../repository-state --mode auto`
明确开启实施，再用 `repository start --state ../repository-state` 后台运行。
观察模式分析全部已捕获来源并形成分阶段路线图，不实施改动，也不批准候选 issue 进入执行队列；空闲且输入未变化时不调用模型。
新输入和决定会使路线图对新工作失效；重新规划未完成时，已有 PR 的交付与反馈跟进仍可继续。
等待、重试和重启共用原有调用与时间额度。

PR 维护与合并权限分别控制：`delivery: pull_request` 也可跟进 CI 和可信评审，
重新规划并修复同一个 PR。范围变化和缺失的业务决定会留在人工待办中。
桌面执行仍需活跃会话领取阶段任务。详见[持续维护指南](docs/repository.md)。

## 适合哪些工作

| 持续职责 | 有用的输入 | 产出 |
| --- | --- | --- |
| **库或 CLI 维护** | 源码、测试、文档约定、可复现的缺陷 | 范围明确的修复和回归测试 |
| **产品的指定区域** | 已提交的反馈、可复现的用户流程、明确的产品约束 | 带有验收标准的改动 |
| **文档维护** | 当前实现、示例和文档 | 经检查的修正和可运行示例 |

提供所选 agent 判断工作所需的证据和业务方向。证据不足时可以等待； 缺少产品决定时可以报告阻塞。 与其他编排工具的源码对照见[同类方案](docs/alternatives.md)。

## 宿主适配器

DutyLoop 使用现有的宿主安装和认证。通过 `driver.name` 选择适配器； 可选的 MCP 控制接口使用同一个任务引擎。

| 集成 | 适配方式 | 验证覆盖 |
| --- | --- | --- |
| **当前本地桌面 agent** | `driver.name: desktop`，通过 MCP 交接阶段 | 离线约定检查；实际 Codex/Claude 桌面执行尚未验证 |
| **Codex CLI** | 原生结构化输出 | 离线协议测试，以及有明确上限的已认证 mission 和仓库协调测试 |
| **Claude Code CLI** | 原生结构化输出 | 离线协议测试 |
| **OpenCode 2.2.5** | 固定版本的原生 JSON 事件 | 固定版本源码检查和离线协议测试 |
| **DeepSeek Harness 0.2.2-rc.1** | 固定版本的原生无交互执行 | 固定版本源码检查和离线协议测试 |
| **自定义命令** | 操作者提供的 JSON 包装器 | 由你验证包装器及其宿主 |
| **MCP 客户端** | 可选的本地 stdio 控制 | 官方 SDK 协议测试；客户端需手动配置 |

OpenCode 和 DeepSeek Harness 会在启动模型工作前拒绝不支持的版本。 DeepSeek Harness 使用已有的 headless 配置，应省略 `driver.model`。 Claude、OpenCode 和 DeepSeek Harness 尚无已认证的完整任务运行记录。 OpenHands 等其他宿主目前需要自定义包装器。

<details>
<summary><strong>使用可选的 MCP 控制</strong></summary>

```console
python -m pip install -e ".[mcp]"
dutyloop mcp --root ../mission-states --allow-project /absolute/path/to/project
```

工具支持持续任务及 issue 批次的创建、启动、状态、事件、停止和显式恢复；issue 决策使用 `issues_resolve`。桌面 worker 通过 `desktop_next`、`desktop_submit` 和 `desktop_cancel` 领取并返回阶段结果。创建任务不会开始工作。按照 [MCP 配置](docs/mcp.md)设置客户端和项目白名单；使用当前对话时参照[桌面指南](docs/desktop.md)。MCP 能连接并不能证明宿主能够完整执行工作。

</details>

## 项目状态

**Alpha 阶段，运行边界明确。**离线行为测试、脚本端到端演示和已记录的宿主检查， 覆盖了特定的控制约定。长期有效性和维护者工作量的改善仍需在真实项目中评估； 尚无对照结果证明其效果优于其他方案。

离线测试覆盖了仓库协调与 GitHub 交付。一项有明确上限的已认证 Codex CLI 测试，完成了本地 issue 修复，并规划了随后引入的 issue；其中 GitHub 输入由模拟提供。真实 GitHub PR 修复、合并和 issue 关闭仍未完成端到端验证，具体范围见[验证记录](docs/validation.md)。

原生 Claude Code 适配器的各阶段使用新进程，可读取已提交的项目指令。桌面驱动器将阶段交给当前对话；实际桌面执行、复核隔离及特定安装环境的记忆行为仍未验证。

- **运行上限：**调用次数统计外层驱动器调用，不逐一统计宿主请求或子 agent。
  桌面模式统计阶段交接，模型用量保持未知。时间上限包括首次运行后的等待和离线时间。
- **费用：**不支持美元硬上限。无法获得的用量或费用保持未知。
- **权限：**允许路径约束的是改动接纳。worktree 仍使用宿主的文件系统、网络、凭据和 Git 访问权限，不构成沙箱。
- **验收：**使用可信且不修改受检内容的检查命令。agent 能改写的测试和单独一次模型复核，不能构成独立的正确性依据。

请将任务状态和执行记录保密保存。选择无人值守运行的范围前，阅读 [运行限制与宿主支持](docs/limits-and-hosts.md)和[验证记录](docs/validation.md)。

## 文档

| 指南 | 适用场景 |
| --- | --- |
| [上手指南](docs/getting-started.md) | 准备项目、运行任务并检查结果分支 |
| [桌面 agent](docs/desktop.md) | 通过 MCP 使用当前本地开发对话 |
| [配置参考](docs/configuration.md) | 设置路径、检查、约束、驱动器和运行上限 |
| [GitHub issue](docs/issues.md) | 规划待办、处理人工决定，配置 PR 与合并交付 |
| [示例](examples/README.md) | 调整维护或文档任务配置 |
| [持续维护仓库](docs/repository.md) | 增量接收 issue、维护由 DutyLoop 创建的 PR，并集中处理人工决定 |
| [运行与排错](docs/operations.md) | 监控事件、停止工作和处理恢复 |
| [适配协议](docs/adapter-protocol.md) | 编写宿主包装器或了解原生适配约定 |
| [架构](docs/architecture.md) | 了解发现、接纳和状态持久化 |
| [更新记录](CHANGELOG.md) | 查看项目变化 |

## 参与贡献

可复现的问题报告、适配器协议测试和经过检查的示例，都很有帮助。 欢迎携带脱敏后的复现材料或具体工作流程[提交 issue](https://github.com/K-kiron/dutyloop/issues/new/choose)， 也可以阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，了解开发环境和必需的检查。 默认测试使用本地测试项目和模拟驱动器；已认证的宿主实验应单独标明。

## 许可

[MIT](LICENSE) · Copyright © 2026 Wenhao XU.
