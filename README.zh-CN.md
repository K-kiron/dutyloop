# DutyLoop

**给开发 agent 一份持续负责的工作，让它在你设定的范围内主动推进项目。**

[English](README.md) · [简体中文](README.zh-CN.md) · [上手指南](docs/getting-started.md) · [配置参考](docs/configuration.md)

[![CI](https://github.com/K-kiron/dutyloop/actions/workflows/ci.yml/badge.svg)](https://github.com/K-kiron/dutyloop/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

DutyLoop 围绕一个项目职责持续运行本地开发 agent：查看当前证据，判断什么值得做，定义任务，实施修改，运行你指定的检查，复核结果，再寻找下一项有价值的改进。完成一个任务后，它会继续工作，直到遇到预设限制、需要处理的阻塞，或者收到停止指令。

例如，你可以交给它“持续维护这个库的解析和导出功能的可靠性”。你一次性给定职责、范围、约束、验收命令和运行上限；DutyLoop 记录判断和执行过程，把通过检查的改动提交到独立的本地分支，供你审阅和采用。

## 可以交给它什么工作

| 持续职责 | 可以使用的证据 | 预期产出 |
| --- | --- | --- |
| 维护库或 CLI 的可靠性 | 当前源码、测试、文档约定、已提交的缺陷复现材料 | 范围明确的修复和回归测试 |
| 改善产品的一个指定区域 | 项目内的用户反馈、可复现的用户流程、明确的产品约束 | 带有验收标准的功能改进 |
| 维护文档可用性 | 当前实现、示例和文档 | 经检查的说明修正和可运行示例 |

职责需要包含足够的产品方向，让 agent 能判断改动是否有用。证据不足时，可以等待；缺少业务决定时，应报告阻塞。DutyLoop 本身没有内置用户反馈采集、竞品爬取或市场分析服务；它能使用哪些材料和工具，取决于你提供的环境和所选 agent 的实际能力。

## 先运行本地演示

需要 **Python 3.11+**、**Git**，以及已配置的 Git 作者身份。当前从源码安装：

```console
git clone https://github.com/K-kiron/dutyloop.git
cd dutyloop
python -m pip install -e .
dutyloop demo --directory ../dutyloop-demo
```

使用一个不存在或为空的演示目录。演示中的**脚本驱动器**会依次发现两个预设缺陷，实际的 supervisor 则负责创建 worktree、运行测试、接纳改动、继续探索，并在七次调用后停止。它不使用模型账号或 API key，验证的是运行流程，不能据此推断模型的判断质量。

## 接入自己的项目

先通过开发工具自己的安装和登录流程，配置一个支持的 CLI。目标项目需要有已提交的 `HEAD`，并且能在新的 worktree 中运行检查。工作副本中尚未提交的改动不会自动成为任务输入。

1. 把 [examples/mission.json](examples/mission.json) 复制到目标仓库之外。
2. 填写项目绝对路径、具体的持续职责、允许改动的路径，以及适合项目的验收命令。
3. 检查环境和配置，再初始化和运行：

```console
dutyloop doctor --driver codex --project /absolute/path/to/project
dutyloop validate --spec mission.json --state ../mission-state
dutyloop init --spec mission.json --state ../mission-state
dutyloop run --state ../mission-state
```

需要后台运行时，使用下面的命令替代 `run`：

```console
dutyloop start --state ../mission-state
dutyloop status --state ../mission-state --human
dutyloop events --state ../mission-state --limit 20
dutyloop stop --state ../mission-state
```

`stop` 发出异步取消请求，需要再次查看状态确认运行进程已经退出。状态目录必须位于目标仓库之外。[上手指南](docs/getting-started.md)还介绍了生成配置、检查结果分支和排查常见问题的方法。

## 已实现的能力

- **持续发现工作：**1–3 个发现 agent 可以并行提出带有证据和验收标准的候选任务；实施和接纳保持串行。
- **完整迭代流程：**实施和复核分别调用 agent，supervisor 把暂存快照还原到工作区后运行操作者指定的检查；检查修改了受跟踪内容或暂存树时，会停止接纳。
- **可检查的 Git 产出：**任务在管理的 worktree 中执行，通过检查的提交进入职责分支；被拒绝或中断的尝试保留证据。
- **持久状态与控制：**本地 SQLite 状态、事件记录、单一运行进程、显式中断恢复和后台运行。
- **调用次数和经过时间限制：**启动调用前预留额度；停止请求会持久保存，并取消管理中的子进程。
- **CLI 和可选 MCP 控制：**终端与手动配置的 MCP 客户端使用同一个执行引擎。

运行时不会自动 push、创建 PR、合并到你的工作分支或部署。你可以通过项目原有流程审阅和采用产生的提交。

## 集成范围和成熟度

| 集成 | 当前支持 | 已验证范围 |
| --- | --- | --- |
| Codex CLI | 原生结构化输出适配器 | 离线协议测试，以及一次已登录的真实小型项目运行 |
| Claude Code CLI | 原生结构化输出适配器 | 离线协议测试；尚无已登录的完整运行记录 |
| OpenCode CLI **2.2.5** | 固定版本的原生 JSON 事件适配器 | 对照源码的启动策略和离线协议测试；尚无已登录的完整运行记录 |
| DeepSeek Harness **0.2.2-rc.1** | 固定版本的原生无交互适配器 | 对照源码的启动策略和离线协议测试；尚无已登录的完整运行记录 |
| 自定义命令 | 已公开的 JSON 包装协议 | 需要你提供并验证宿主包装器 |
| MCP 客户端 | 可选本地 stdio 控制服务 | 官方 SDK 协议测试；需要手动配置客户端 |

一个宿主支持 MCP，不代表它已经获得原生开发编排支持。完整边界见[宿主支持与限制](docs/limits-and-hosts.md)、[适配协议](docs/adapter-protocol.md)和[验证记录](docs/validation.md)。

OpenCode 和 DeepSeek Harness 会在启动模型工作前拒绝不匹配的版本。DeepSeek Harness 使用已有的 headless 配置，应省略 `driver.model`。OpenHands 等其他运行时目前需要操作者自行提供包装器。

DutyLoop 目前处于早期阶段。supervisor 的控制行为有自动化测试覆盖；长期运行的有效性、对维护者工作量的改善，仍需在真实项目中验证。[已有同类工具](docs/alternatives.md)包括 AUTOPILOT、OpenClaw、Paperclip 和 Symphony。DutyLoop 选择小型本地职责配置和可检查状态的实现方式，尚无优于这些工具的对照实验结果。

## 无人值守运行前需要了解的边界

**额度：**`max_invocations` 统计驱动器调用，不单独统计 CLI 内部的模型请求或子 agent。`max_wall_seconds` 包括等待和首次运行之后的离线时间。**不支持美元硬预算**；无法获得的费用保持未知。

**权限：**允许路径是接纳改动时的检查规则。worktree 不会隔离文件系统、网络、凭据或共享 Git 元数据。子进程使用其宿主环境的权限。需要这些隔离时，应另行使用受限账号、容器或虚拟机。

**验收：**你配置的验收命令会执行项目代码。agent 能改写的测试不构成独立的验收依据；额外一次模型复核也不能证明用户价值。尽量把决定性的检查放在不可改写的路径中。状态和日志可能包含私有项目资料，应妥善保存。

详细说明见[运行边界](docs/limits-and-hosts.md)。

## 文档与贡献

- [上手指南](docs/getting-started.md)：首次运行及结果分支检查。
- [配置参考](docs/configuration.md)：全部字段、默认值和示例。
- [运行与排错](docs/operations.md)：状态、事件、限额、中断和恢复。
- [MCP 配置](docs/mcp.md)：客户端手动配置及控制权限。
- [架构](docs/architecture.md)：发现、接纳、持久状态和失败处理。
- [贡献指南](CONTRIBUTING.md)：开发检查和集成要求。

采用 MIT 许可。欢迎通过 [GitHub Issues](https://github.com/K-kiron/dutyloop/issues)报告可复现的问题和具体宿主兼容情况。
