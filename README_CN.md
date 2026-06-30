# CodeMate

[English](README.md) | 简体中文

基于 Docker 的 Claude Code 和 Codex 环境，具有自动化 Git/PR 设置功能。

> **⚠️ 安全提示：** 此容器运行所选 agent 时不会请求操作确认。仅在隔离环境中使用受信任的代码仓库。

## 为什么选择 CodeMate？

厌倦了在与 AI 结对编程时批准每一个命令？但又不愿在本地机器上授予完全绕过权限？每次 GitHub 交互都需要手动确认会打断你的工作流程。

CodeMate 通过在隔离的 Docker 容器中运行 Claude Code 来解决这个问题，让它可以自由操作而不会危及你的系统。真正的结对编程从这里开始——让 Claude 专注于编码，而你把握全局方向。

## 功能特性

- 自动化仓库克隆和 PR 管理
- 预装：Go、Node.js、Python、Rust、uv
- 配置 Oh My Zsh 的 zsh
- 持久化 Claude 配置
- 内置 Claude Code Skills 用于 PR 工作流自动化
- Slack 通知（当 Claude 停止时，需配置 `SLACK_WEBHOOK`）
- tmux 会话管理与 PR 评论监控

## 快速开始

### 前置要求

- Docker
- GitHub CLI (`gh`) 已认证
- Anthropic API key

运行 `codemate --setup` 创建所需的配置文件（全局配置在 `~/.codemate/`，项目 `.env`）

#### Mac 用户

在 macOS 上，你需要一个 Docker 运行时，因为 Docker 不能原生运行。选择其中之一：

- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** - 官方 Docker GUI 应用
- **[Colima](https://github.com/abiosoft/colima)** - 轻量级 Docker runtime（推荐 CLI 用户使用）

### 安装

#### 全局安装（推荐）

全局安装 `codemate` 以便在任何地方使用：

```bash
# 直接安装到 /usr/local/bin（需要 sudo）
sudo curl -fsSL https://raw.githubusercontent.com/BoringHappy/CodeMate/main/codemate -o /usr/local/bin/codemate && sudo chmod +x /usr/local/bin/codemate

# 或不使用 sudo 安装到 ~/bin（确保 ~/bin 在你的 PATH 中）
mkdir -p ~/bin && curl -fsSL https://raw.githubusercontent.com/BoringHappy/CodeMate/main/codemate -o ~/bin/codemate && chmod +x ~/bin/codemate

# 一次性全局设置
codemate --setup

# 更新到最新版本
codemate --update
```

### 使用方法

#### 基本命令

```bash
# 首次设置 - 创建全局配置和项目 .env
codemate --setup

# 使用明确的仓库 URL 运行
codemate --repo https://github.com/your-org/your-repo.git --branch feature/xyz

# 使用分支名称运行（自动检测仓库来源：--repo > .env > 当前目录的 git remote）
codemate --branch feature/your-branch

# 使用 Codex（默认运行 Claude）
echo 'CODEMATE_AGENT=codex' >> .env
codemate --branch feature/your-branch

# 使用现有 PR 运行
codemate --pr 123

# 使用 GitHub issue 运行（创建分支 issue-NUMBER）
codemate --issue 456

# Fork 工作流（用于开源贡献）
codemate --repo https://github.com/yourname/project.git --upstream https://github.com/maintainer/project.git --branch fix-bug
codemate --repo https://github.com/yourname/project.git --upstream https://github.com/maintainer/project.git --issue 789

# 使用自定义卷挂载运行（可选）
codemate --branch feature/xyz --mount ~/data:/data

# 使用初始查询运行 Claude
codemate --branch feature/xyz --query "请审查代码并修复任何问题"

# 从本地 Dockerfile 构建并运行
codemate --build --branch feature/xyz

# 使用自定义 Dockerfile 路径和标签构建
codemate --build -f ./custom/Dockerfile --tag my-codemate:v1 --branch feature/xyz

# 中国用户：使用 DaoCloud 镜像加速镜像拉取
codemate --branch feature/xyz --image ghcr.m.daocloud.io/boringhappy/codemate:latest
```

设置命令将：
1. 在 `~/.codemate/` 创建全局配置（Claude 配置和设置）
2. 在当前目录创建项目特定的 `.env` 文件
3. 提示你输入 Anthropic API token 和其他设置

**配置结构：**
- **全局配置**：`~/.codemate/` - 共享 home 状态；其中每个顶层文件或目录都会用相同名称挂载到 `$HOME`
- **项目配置**：每个项目目录中的 `.env` - 项目特定的密钥和设置

**仓库 URL 解析**：脚本按以下优先级确定仓库 URL：
1. `--repo` 命令行参数（最高优先级）
2. `CODEMATE_GIT_REPO_URL` 环境变量或 `.env` 文件
3. 当前目录的 git remote origin URL（自动检测）
4. 如果都不可用，则报错

##### 自定义 volume 挂载

使用 `--mount <主机路径>:<容器路径>` 挂载额外的目录或文件。适用于与容器共享数据、配置或凭证。可以指定多个 `--mount` 选项。

##### 从本地 Dockerfile 构建

对于开发或自定义，你可以从本地 Dockerfile 构建 CodeMate：

```bash
# 从默认的 Claude Dockerfile 构建
codemate --build --branch feature/xyz

# 从自定义 Dockerfile 路径构建
codemate --build -f ./path/to/Dockerfile --branch feature/xyz

# 使用自定义镜像标签构建
codemate --build --tag my-codemate:dev --branch feature/xyz

# 组合所有选项
codemate --build -f ./custom/Dockerfile --tag my-codemate:v1 --branch feature/xyz
```

**选项：**
- `--build` - 运行前从本地 Dockerfile 构建 Docker image
- `-f, --dockerfile PATH` - Dockerfile 路径（默认：`docker/Dockerfile`）
- `--tag TAG` - 本地构建的 image tag（默认：`codemate:local`）
  - **注意：** 仅与 `--build` 一起使用。要使用预构建 image，请使用 `--image`

当使用 `--build` 时：
1. 脚本从指定的 Dockerfile 构建 Docker image
2. 默认 image tag 为 `codemate:local`（除非指定 `--tag`）
3. 使用本地构建的 image 而不是从 registry 拉取
4. 使用 `--build` 时会忽略 `--image` 选项

**添加自定义 toolchain：**

要向容器添加额外的 toolchain 或工具，创建一个扩展基础镜像的自定义 Dockerfile：

```dockerfile
# 带有额外 toolchain 的自定义 Dockerfile
FROM ghcr.io/boringhappy/codemate:latest

# 添加 Java
RUN apt-get update && apt-get install -y openjdk-17-jdk maven

# 添加 PHP
RUN apt-get install -y php php-cli php-mbstring composer

# 添加 Ruby
RUN apt-get install -y ruby-full
RUN gem install bundler

# 添加你需要的任何其他工具
RUN apt-get install -y postgresql-client redis-tools

# 清理
RUN apt-get clean && rm -rf /var/lib/apt/lists/*
```

然后使用自定义 Dockerfile 构建并运行：

```bash
codemate --build -f ./Dockerfile.custom --tag codemate:custom --branch feature/xyz
```

## 基于 Issue 的工作流

CodeMate 支持使用 `--issue` 标志直接从 GitHub issue 开始工作。此工作流会自动：

1. 创建名为 `issue-{NUMBER}` 的分支（如果分支已存在则使用现有分支）
2. 向 Claude 发送初始查询，使用 `/issue:read-issue` skill 读取并处理 issue
3. Claude 分析 issue 详情（标题、描述、标签、评论）
4. Claude 实现请求的更改
5. 当你准备好提交时创建 PR

**示例：**

```bash
# 开始处理 issue #456
codemate --issue 456
```

这等同于：
```bash
codemate --branch issue-456 --query "Please use /issue:read-issue skill to read and address issue #456"
```

**何时使用：**
- 从 GitHub issue 开始新工作
- 实现作为 issue 跟踪的功能请求
- 修复 issue 中记录的 bug

**Fork 工作流：**

对于开源贡献，你可以结合使用 `--issue` 和 `--upstream`：

```bash
# 从 fork 处理上游仓库的 issue
codemate --repo https://github.com/yourname/project.git --upstream https://github.com/maintainer/project.git --issue 789
```

## 环境变量

> **注意：** 使用 `codemate` 时，这些变量通过设置过程自动处理。此参考主要用于高级 Docker 使用或故障排除。

| 变量 | 必需 | 描述 |
|----------|----------|-------------|
| `CODEMATE_GIT_REPO_URL` | 否 | 仓库 URL（默认为当前仓库的 remote） |
| `CODEMATE_UPSTREAM_REPO_URL` | 否 | 上游仓库 URL（用于 fork 工作流） |
| `BRANCH_NAME` | 否 | 要工作的分支 |
| `PR_NUMBER` | 否 | 要工作的现有 PR 编号 |
| `ISSUE_NUMBER` | 否 | GitHub issue 编号（创建分支 `issue-NUMBER` 并使用 `/issue:read-issue` skill） |
| `CODEMATE_GITHUB_TOKEN` | 自动 | GitHub 个人访问令牌（如果未提供，默认为 `gh auth token`） |
| `CODEMATE_GIT_USER_NAME` | 自动 | Git commit author 名称（如果未提供，默认为 `git config user.name`） |
| `CODEMATE_GIT_USER_EMAIL` | 自动 | Git commit author 邮箱（如果未提供，默认为 `git config user.email`） |
| `CODEMATE_IMAGE` | 否 | 自定义 image（默认：`ghcr.io/boringhappy/codemate:latest`） |
| `CODEMATE_AGENT` | 否 | 启动的 runtime：`claude`（默认）或 `codex` |
| `SLACK_WEBHOOK` | 否 | Slack Incoming Webhook URL，用于 Claude 停止时的通知 |
| `ANTHROPIC_AUTH_TOKEN` | 否 | Anthropic API token（用于自定义 API 端点） |
| `ANTHROPIC_BASE_URL` | 否 | Anthropic API 基础 URL（用于自定义 API 端点） |
| `QUERY` | 否 | 启动后发送给所选 agent 的初始 query |
| `CODEMATE_DEFAULT_MARKETPLACES` | 否 | 逗号分隔的默认插件市场（默认：`BoringHappy/CodeMate`） |
| `CODEMATE_DEFAULT_PLUGINS` | 否 | 逗号分隔的默认插件（默认：`git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate`） |
| `CODEMATE_CUSTOM_MARKETPLACES` | 否 | 逗号分隔的自定义插件市场仓库列表（例如：`username/repo1,org/repo2`） |
| `CODEMATE_CUSTOM_PLUGINS` | 否 | 逗号分隔的要安装的自定义插件列表（例如：`plugin1@marketplace1,plugin2@marketplace2`） |


## 工作原理

CodeMate 使用单独的[基础镜像（`codemate-base`）](https://github.com/BoringHappy/CodeMate/pkgs/container/codemate-base)，每周重建以保持系统包和开发工具的最新状态。

启动时，容器会：
1. clone/更新 repository 到 `/home/agent/<repo-name>`
2. checkout 指定的 branch 或 PR
3. 如果在新 branch 上工作，则创建 PR
4. 在对应的 tmux session 中启动 Claude Code 或 Codex
5. 如果提供了 `--query`，则向所选 agent 发送初始 query
6. 运行 cron job 监控 PR 评论（每分钟）

## Skills

[CodeMate](https://github.com/BoringHappy/CodeMate) 预装了 skills，在启动容器时自动可用，为 Git、PR 管理等提供工作流自动化。

### 可用插件

**Git 插件** (`git@codemate`)：
| 命令 | 描述 |
|---------|-------------|
| `/git:commit` | stage 所有更改，创建有意义的 commit 消息，并推送到远程 |

**PR 插件** (`pr@codemate`)：
| 命令 | 描述 |
|---------|-------------|
| `/pr:get-details` | 获取 PR 信息，包括标题、描述、文件更改和 review comments |
| `/pr:fix-comments` | 读取 PR review comments，修复问题，commit 更改并回复 comments |
| `/pr:update` | 更新 PR 标题和摘要。使用 `--skip-title` 仅更新摘要 |
| `/pr:ack-comments` | 通过添加 👀 表情确认 PR issue comments |
| `/pr:read-issue` | ~~已移至 `/issue:read-issue`~~ 读取 GitHub issue 详情，包括标题、描述、标签和评论 |

**Issue 插件** (`issue@codemate`)：
| 命令 | 描述 |
|---------|-------------|
| `/issue:read-issue` | 读取 GitHub issue 详情，包括标题、描述、标签和评论 |
| `/issue:refine-issue` | 重写 issue 正文以匹配模板（计划-执行工作流，需要用户确认） |
| `/issue:triage-issue` | 根据内容分析应用优先级和分类标签 |
| `/issue:classify-issue` | 为不明确的 issue 发布澄清问题并添加 `needs-more-info` 标签 |

### 自定义插件

你可以通过在 `.env` 文件中添加自定义插件来扩展 CodeMate：

```bash
# 覆盖默认市场（可选）
CODEMATE_DEFAULT_MARKETPLACES=BoringHappy/CodeMate

# 覆盖默认插件（可选）
CODEMATE_DEFAULT_PLUGINS=git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate

# 设置为空以禁用所有默认值（可选）
CODEMATE_DEFAULT_MARKETPLACES=
CODEMATE_DEFAULT_PLUGINS=

# 添加自定义插件市场（逗号分隔的 GitHub 仓库路径）
CODEMATE_CUSTOM_MARKETPLACES=username/my-marketplace,org/another-marketplace

# 添加要安装的自定义插件（逗号分隔的插件名称）
CODEMATE_CUSTOM_PLUGINS=my-plugin@my-marketplace,another-plugin@my-marketplace
```

**工作原理：**
1. 默认情况下，CodeMate 会从 `CODEMATE_DEFAULT_MARKETPLACES` 安装市场，从 `CODEMATE_DEFAULT_PLUGINS` 安装插件
2. 你可以通过设置环境变量为不同的值来覆盖这些默认值
3. 你可以通过将它们设置为空字符串来禁用所有默认值
4. 在容器启动期间，自定义市场和插件会在默认值之后添加
5. 所有插件都可作为 skills 使用（例如：`/my-plugin:command`）
6. 设置是幂等的 - 已安装的插件会被跳过

**示例：**

如果你在 `github.com/myorg/my-plugins` 有一个自定义插件市场，其中有一个名为 `example-skill` 的插件，你可以这样配置：

```bash
CODEMATE_CUSTOM_MARKETPLACES=myorg/my-plugins
CODEMATE_CUSTOM_PLUGINS=example-skill@my-plugins
```

然后在 Claude Code 中使用：
```bash
/example-skill:command
```

## PR Comment 监控

CodeMate 自动监控 PR comments，并在新反馈到达时通知 Claude。cron job 每分钟运行一次以检查新 comments。

### 评论类型

GitHub PR 有两种类型的评论，CodeMate 会监控：

| 类型 | 位置 | API 端点 | 用例 |
|------|----------|--------------|----------|
| **Review Comment** | File Changes | `/pulls/{pr}/comments` | 针对特定行的代码特定反馈 |
| **Issue Comment** | PR Comment | `/issues/{pr}/comments` | 一般讨论、问题、请求 |

### Review Comment Workflow

当有人留下 **review comment**（inline code comment）时：

1. 监控检测到未解决的 review comments
2. 向 Claude 发送消息：`"Please Use /fix-comments skill to address comments"`
3. Claude 使用 `/pr:fix-comments` skill：
   - 读取反馈
   - 进行代码更改
   - commit 并推送
   - 回复 "Claude Replied: ..." 标记为已解决

### Issue Comment Workflow

当有人留下 **issue comment**（一般 PR comment）时：

1. 监控检测到没有 👀 reaction 的新 issue comments
2. 将实际 comment 内容发送给 Claude
3. Claude 处理请求
4. Claude 使用 `/pr:ack-comments` skill 添加 👀 reaction
5. 未来运行会跳过带有 👀 reaction 的 comments

### Filtering Logic

Comments 在以下情况下会被过滤掉：
- 以 "Claude Replied:" 开头（已处理）
- 有 👀 reaction（已确认）
- 由 Claude 自己创建

## 最佳实践

### 添加 Pull Request 模板

在目标仓库中创建 `.github/PULL_REQUEST_TEMPLATE.md` 以标准化 PR 描述：

```markdown
## 摘要
<!-- 简要描述更改 -->

## 测试计划
<!-- 如何验证更改 -->

## 检查清单
- [ ] 添加/更新测试
- [ ] 更新文档
```

### 安全建议

- 仅在受信任的仓库上运行 CodeMate
- 使用具有最小范围的短期 GitHub 令牌
- 避免挂载敏感的主机目录
- 在合并 Claude 创建的 PR 之前审查更改

## 许可证

MIT
