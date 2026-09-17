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
- 直接启动 Claude/Codex 会话（原生 initial prompt），以及基于 Stop hook 的 PR 评论监控

## 快速开始

### 前置要求

- Docker
- GitHub CLI (`gh`) 已认证
- Anthropic API key

运行 `codemate --setup` 创建所需的配置文件（全局配置在 `CODEMATE_HOME`，默认 `~/.codemate/`，项目 `.env`）

#### Mac 用户

在 macOS 上，你需要一个 Docker 运行时，因为 Docker 不能原生运行。选择其中之一：

- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** - 官方 Docker GUI 应用
- **[Colima](https://github.com/abiosoft/colima)** - 轻量级 Docker runtime（推荐 CLI 用户使用）

### 安装

#### 全局安装（推荐）

使用 `uv` 全局安装或升级 Python CLI：

```bash
# 推荐方式
uv tool install --upgrade git+https://github.com/BoringHappy/CodeMate.git
```

如果 CodeMate 已经安装，也可以通过包名升级：

```bash
uv tool upgrade codemate-cli
```

卸载 CLI：

```bash
uv tool uninstall codemate-cli
```

如果你使用 `pipx`：

```bash
pipx install git+https://github.com/BoringHappy/CodeMate.git
```

然后执行一次全局设置：

```bash
# 一次性全局设置
codemate --setup
```

`codemate` 命令由 `src/` 中的 Python CLI 包提供。

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
codemate --branch feature/your-branch --agent codex

# 使用现有 PR 运行
codemate --pr 123

# 使用 GitHub issue 运行（创建分支 issue-NUMBER）
codemate --issue 456

# Fork 工作流（用于开源贡献）
codemate --repo https://github.com/yourname/project.git --upstream https://github.com/maintainer/project.git --branch fix-bug
codemate --repo https://github.com/yourname/project.git --upstream https://github.com/maintainer/project.git --issue 789

# 跳过新分支的 PR 创建（适用于 fork 或草稿工作）
codemate --branch feature/xyz --no-pr

# Chat 模式会跳过 PR 创建和 CodeMate system prompt 注入
codemate --branch feature/xyz --chat

# 不启动 agent，直接在容器里打开一个交互式 zsh
codemate --branch feature/xyz --shell

# Pure 模式：只挂载本地 CodeMate home 与当前目录的纯 zsh 容器，
# 不做仓库初始化、不涉及 GitHub、不安装插件
codemate --pure

# 使用自定义卷挂载运行（可选）
codemate --branch feature/xyz --mount ~/data:/data

# 使用初始查询运行 Claude
codemate --branch feature/xyz --query "请审查代码并修复任何问题"

# 从本地 Dockerfile 构建并运行
codemate --build --branch feature/xyz

# 使用自定义 Dockerfile 路径和标签构建
codemate --build -f ./custom/Dockerfile --tag my-codemate:v1 --branch feature/xyz

# 中国用户：从镜像仓库（mirror）拉取内置镜像
codemate --branch feature/xyz --image-registry ghcr.m.daocloud.io

# 启动时跳过镜像拉取（本地已有镜像时直接使用）
codemate --branch feature/xyz --skip-pull

# 使用指定的 Docker 网络（Linux 上默认为 host）
codemate --branch feature/xyz --network bridge

# 使用指定时区运行容器（默认为 UTC）
codemate --branch feature/xyz --tz Asia/Shanghai
```

设置命令将：
1. 在 `CODEMATE_HOME` 创建全局配置（默认 `~/.codemate/`；Claude 配置和设置）
2. 在当前目录创建项目特定的 `.env` 文件
3. 提示你输入 Anthropic API token 和其他设置

**配置结构：**
- **全局配置**：`CODEMATE_HOME`（默认 `~/.codemate/`）- 共享 home 状态；其中每个顶层文件或目录都会用相同名称挂载到 `$HOME`。可通过 `CODEMATE_HOME` 环境变量覆盖位置（例如 `export CODEMATE_HOME=/data/codemate`），将其存放在任意磁盘路径，而不绑定 `~/.codemate`
- **项目配置**：每个项目目录中的 `.env` - 项目特定的密钥和设置

**仓库 URL 解析**：CLI 按以下优先级确定仓库 URL：
1. `--repo` 命令行参数（最高优先级）
2. `CODEMATE_GIT_REPO_URL` 环境变量或 `.env` 文件
3. 当前目录的 git remote origin URL（自动检测）
4. 如果都不可用，则报错

##### 自定义 volume 挂载

使用 `--mount <主机路径>:<容器路径>` 挂载额外的目录或文件。适用于与容器共享数据、配置或凭证。可以指定多个 `--mount` 选项。

##### Pure 模式

`codemate --pure` 使用 pure 镜像（`ghcr.io/boringhappy/codemate-pure:latest`）：一个只安装了 Claude Code 和 Codex 的纯 zsh 容器，不做仓库初始化，不需要 GitHub 认证，不安装插件，也不注入 prompt、不创建 PR、不监控 PR。

```bash
# 启动 zsh，并挂载本地 CodeMate home 与当前目录
codemate --pure

# 本地构建 pure 镜像并运行（默认：-f docker/Dockerfile.pure --tag codemate-pure:local）
codemate --pure --build

# 与常规 Docker 参数组合使用
codemate --pure --network bridge --mount ~/data:/data --tz Asia/Shanghai
```

Pure 模式会做：

- 只把它的 home（`CODEMATE_PURE_HOME`，默认 `~/.codemate-pure`）中的顶层条目按同名路径挂载到 `$HOME`（`.claude`、`.claude.json`、`.codex`），不挂载 pure home 目录本身。pure home 会在首次运行时创建并预置这些条目，所以在容器里登录一次即可持久保存
- 把当前工作目录按它在宿主机 home 下的相对路径挂载（`~/code/projecta` → `/home/agent/code/projecta`）并作为工作目录；不在 home 下的目录则回退为 `/home/agent/<目录名>`
- 执行镜像的默认命令 `zsh`
- 仍然支持 `--mount`、`--docker-param`、`--network`、`--image`、`--env`、`--env-file`、`--tz`、`--skip-pull`、`--dry-run`

Pure 模式会跳过：

- 不需要 `--branch`、`--pr`、`--issue` 目标；来自 `.env`、环境变量或命令行的目标值都会被忽略
- 不需要 GitHub token、git 身份和仓库 URL
- 宿主机只需要 `docker`（不再需要 `git` 和 `gh`）
- 不能同时使用 `--query` 和 `--shell`：容器本身就会打开 zsh，且没有 agent 启动器来接收查询

注意：

- Pure 模式完全不会读写标准的 `CODEMATE_HOME`（`~/.codemate`）：两者是不同路径，pure 会话拥有自己的 Claude/Codex 凭证与配置。可用 `CODEMATE_PURE_HOME` 指定其他位置；默认是标准 home 加 `-pure` 后缀，因此 `CODEMATE_HOME=/data/codemate` 对应 `/data/codemate-pure`
- Pure 模式下用 `--image` 和 `--build` 选择镜像；`.env` 或环境变量里的 `CODEMATE_IMAGE` 会被忽略，避免标准镜像设置泄漏到 pure 模式。pure 的默认镜像同样遵循 `CODEMATE_IMAGE_REGISTRY`
- 使用 `--network <mode>` 指定 Docker 网络模式。使用 `--docker-param` 时，请把参数和取值放在同一个引号字符串中：`--docker-param "--network bridge"` 可用，`--docker-param --network bridge` 不可用

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
- `-f, --dockerfile PATH` - Dockerfile 路径（默认：`docker/Dockerfile`；使用 `--pure` 时为 `docker/Dockerfile.pure`）
- `--tag TAG` - 本地构建的 image tag（默认：`codemate:local`；使用 `--pure` 时为 `codemate-pure:local`）
  - **注意：** 仅与 `--build` 一起使用。要使用预构建 image，请使用 `--image`

当使用 `--build` 时：
1. CLI 从指定的 Dockerfile 构建 Docker image
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

**预装的浏览器自动化工具：**

基础镜像已内置 [Playwright](https://playwright.dev/) CLI，进行浏览器自动化无需再安装工具本身。镜像不包含浏览器，可按需下载所需浏览器：

```bash
playwright --version
playwright install chromium                  # 需要时还可安装 firefox / webkit
playwright screenshot "https://example.com" /tmp/page.png
npx playwright test
```

浏览器会下载到当前用户的缓存目录（`~/.cache/ms-playwright`），无需 root 权限。如果浏览器因缺少系统库而无法启动，可安装一次系统依赖：

```bash
sudo "$(command -v playwright)" install-deps chromium
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

`codemate` CLI 按以下优先级解析配置：

1. 命令行参数，例如 `--repo`、`--branch`、`--agent`、`--mount` 和 `--docker-param`
2. 项目 `.env`
3. 当前 shell 的全局环境变量
4. 命令推导值和内置默认值，例如 `git config user.name`、`gh auth token` 和当前仓库 remote

Docker 会接收按上述优先级生成后的环境变量值；项目 `.env` 不再直接传入容器。

| 变量 | 必需 | 描述 |
|----------|----------|-------------|
| `CODEMATE_GIT_REPO_URL` | 否 | 仓库 URL（默认为当前仓库的 remote） |
| `CODEMATE_UPSTREAM_REPO_URL` | 否 | 上游仓库 URL（用于 fork 工作流） |
| `CODEMATE_GITHUB_TOKEN` | 自动 | GitHub 个人访问令牌（如果未提供，默认为 `gh auth token`） |
| `CODEMATE_GIT_USER_NAME` | 自动 | Git commit author 名称（如果未提供，默认为 `git config user.name`） |
| `CODEMATE_GIT_USER_EMAIL` | 自动 | Git commit author 邮箱（如果未提供，默认为 `git config user.email`） |
| `CODEMATE_CO_AUTHOR_BY` | 否 | Git commit skill 使用的 commit co-author，例如 `Name <email@example.com>` 或 `Co-authored-by: Name <email@example.com>` |
| `CODEMATE_IMAGE` | 否 | 自定义 image（默认：`ghcr.io/boringhappy/codemate:latest`） |
| `CODEMATE_IMAGE_REGISTRY` | 否 | 内置镜像所用的镜像仓库（默认：`ghcr.io`）。设置后无需 `--image` 即可从 mirror 拉取 `codemate`/`codemate-pure`；显式指定的 `--image`、`CODEMATE_IMAGE` 或 `--build` 标签不会被替换 |
| `CODEMATE_SKIP_PULL` | 否 | 启动时跳过 Docker 镜像拉取；仅当本地缺少镜像时才拉取 |
| `CODEMATE_HOME` | 否 | 宿主机上的 CodeMate home 目录；支持 `~` 和 `$VAR` 展开（默认：`~/.codemate`） |
| `CODEMATE_PURE_HOME` | 否 | `--pure` 会话使用的 home 目录；支持 `~` 和 `$VAR` 展开（默认：`CODEMATE_HOME` 路径加 `-pure` 后缀，例如 `~/.codemate-pure`） |
| `CODEMATE_REPO_DIR` | 自动 | 容器内 workspace 路径。默认把宿主机上的 checkout 路径映射到 `$HOME` 下（`~/code/projecta` → `/home/agent/code/projecta`）；checkout 不在 home 下时回退为 `/home/agent/<repo-name>`。设置它可固定使用其它位置 |
| `CODEMATE_AGENT` | 否 | 启动的 runtime：`claude`（默认）或 `codex` |
| `CODEMATE_INSTANCE_ID` | 否 | 区分同一主机或容器内并发 agent 进程的 runtime instance 名称 |
| `CODEMATE_RUNTIME_DIR` | 否 | 覆盖 session 级 hook 状态根目录（默认 `$XDG_RUNTIME_DIR/codemate` 或 `/tmp/codemate-<uid>`） |
| `CODEMATE_TMPDIR` | 否 | 写入容器 env 的每个 agent 专属临时目录（Claude 为 `/home/agent/.claude/tmp`，Codex 为 `/home/agent/.codex/tmp`）；未设置 `CODEMATE_RUNTIME_DIR` 时 hook 会由此派生 runtime root |
| `CODEMATE_NO_PR` | 否 | 跳过 PR 创建和 branch push |
| `CODEMATE_CHAT` | 否 | Chat 模式；会推导出 `CODEMATE_NO_PR=true` 并跳过 CodeMate system prompt 注入 |
| `TZ` | 否 | 容器时区（默认：`UTC`；可通过 `--tz`、`.env` 或当前环境变量覆盖） |
| `SLACK_WEBHOOK` | 否 | Slack Incoming Webhook URL，用于 Claude 停止时的通知 |
| `LARK_WEBHOOK` | 否 | Lark Incoming Webhook URL，用于 Claude 停止时的通知 |
| `ANTHROPIC_AUTH_TOKEN` | 否 | Anthropic API token（用于自定义 API 端点） |
| `ANTHROPIC_BASE_URL` | 否 | Anthropic API 基础 URL（用于自定义 API 端点） |
| `CODEMATE_DEFAULT_MARKETPLACES` | 否 | 逗号分隔的默认插件市场（默认：`BoringHappy/CodeMate`） |
| `CODEMATE_DEFAULT_PLUGINS` | 否 | 逗号分隔的默认插件（默认：`git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate`） |
| `CODEMATE_CUSTOM_MARKETPLACES` | 否 | 逗号分隔的自定义插件市场仓库列表（例如：`username/repo1,org/repo2`） |
| `CODEMATE_CUSTOM_PLUGINS` | 否 | 逗号分隔的要安装的自定义插件列表（例如：`plugin1@marketplace1,plugin2@marketplace2`） |
| `CODEMATE_SOFT_LINKS` | 否 | 逗号分隔的 `source:destination` 软链接配置（例如：`/data/models:/home/agent/models,/data/cache:/home/agent/.cache`） |

`CODEMATE_BRANCH_NAME`、`CODEMATE_PR_NUMBER`、`CODEMATE_PR_TITLE`、`CODEMATE_ISSUE_NUMBER`、`CODEMATE_QUERY`、`CODEMATE_NO_PR`、`CODEMATE_CHAT`、`CODEMATE_SKIP_PULL` 和 `CODEMATE_CO_AUTHOR_BY` 可以通过 CLI 参数、`.env` 或全局环境变量设置。单次运行优先使用 CLI 参数。使用 `codemate --agent claude|codex` 可为单次运行覆盖 `.env` 中的 `CODEMATE_AGENT`；使用 `codemate --chat` 可跳过 PR 创建和 CodeMate system prompt 注入；使用 `codemate --shell` 可执行同样的容器初始化流程，但不启动 agent，而是直接进入交互式 zsh；使用 `codemate --pure` 可完全跳过初始化，直接运行挂载了独立 home（`CODEMATE_PURE_HOME`）的纯 zsh 容器；使用 `codemate --skip-pull` 可跳过启动时的镜像拉取、直接使用本地缓存的镜像；使用 `codemate --co-author-by "Name <email@example.com>"` 可为 Git commit skill 创建的提交添加 co-author。


## 工作原理

CodeMate 使用单独的[基础镜像（`codemate-base`）](https://github.com/BoringHappy/CodeMate/pkgs/container/codemate-base)，每周重建以保持系统包和开发工具的最新状态。

启动时，容器会：
1. 把 repository clone/更新到 workspace：宿主机 checkout 路径映射到 `$HOME` 下的位置（`~/code/projecta` → `/home/agent/code/projecta`），不在 home 下时回退为 `/home/agent/<repo-name>`
2. checkout 指定的 branch 或 PR
3. 如果在新 branch 上工作，则创建 PR（除非使用 `--no-pr`、`--chat` 或 fork 工作流）
4. 直接启动 Claude Code 或 Codex，把初始 query 作为原生 initial prompt 传入；除非启用 chat 模式，否则会附加 CodeMate 指令
5. 如果提供了 `--query`，则向所选 agent 发送初始 query
6. 在 agent 空闲时，通过 workspace 插件的 Stop hook 监控 PR 评论、CI 失败和 review-ready 状态

两种模式都把 workspace 放在宿主机 home 下的相对路径上：`~/code/projecta` 中的 checkout 无论由标准镜像 clone 还是由 `--pure` 挂载，容器内路径都是 `/home/agent/code/projecta`。

使用 `--shell` 时，容器完成上述仓库初始化后直接打开交互式 zsh，不安装 agent 插件，也不启动 Claude Code 或 Codex。

使用 `--pure` 时，上述步骤全部不执行。pure 镜像直接启动 zsh，只挂载它自己的 home（`CODEMATE_PURE_HOME`，默认 `~/.codemate-pure`）和当前目录，因此不需要 GitHub token、git 身份、仓库 URL，也不要求宿主机安装 `git` 或 `gh`。详见 [Pure 模式](#pure-模式)。

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

CodeMate 通过 workspace 插件的原生 `Stop` hook 监控 PR feedback。第一次检查立即运行，后续检查按 10、30、60、120 秒退避，最大间隔保持 120 秒；不再依赖 cron，也不再通过 tmux 注入 prompt。Claude 使用 `asyncRewake` 在后台 polling，保持 UI 可交互；Codex 目前不运行 async command hook，因此使用同步 Stop continuation contract。

每次调用 `gh` 之前，hook 都会确认当前 session 仍为 Stop 状态，并确认当前 worktree/branch 仍有关联的 open PR。如果用户提交了新 prompt，正在运行的 monitor 会退出。检测到反馈时，Claude 通过 `asyncRewake` 被唤醒，Codex 则收到结构化 Stop continuation；两者都会原生创建下一轮处理。

### 状态隔离

- Session 状态按 runtime instance、agent 和 `session_id` 分目录保存；通知 commit baseline 和 retry counter 再按 Git worktree 和 branch 隔离。
- PR 状态通过 `pr` 插件的 query-first `pr-status` 接口实时从 GitHub 解析（GitHub 为事实来源），插件之间不再共享 PR-status 文件；pr 插件只在 runtime root 下保留私有缓存用于消歧，workspace 的 monitor-state 与 lock 文件保存共享 cursor 与可中断 branch lease（同样在 runtime root 下，不再写入 `.git`），保证同一个 PR event 只由一个已 Stop 的 session 处理。
- Docker 容器名包含 runtime agent（`codemate-<agent>-<repo>-<branch>`），因此同一台机器上同一 repo/branch 的 Claude 与 Codex 会话可以并行运行，不会误连到对方的容器。
- 每个 runtime 的可写状态放在各自配置目录下：Claude 的 `CODEMATE_TMPDIR` 及派生出的 hook runtime root 是 `/home/agent/.claude/tmp`，Codex 是 `/home/agent/.codex/tmp`，避免两个 runtime 的临时文件和 session 状态共用一个位置。CodeMate 不会覆盖全局 `TMPDIR`，以免影响容器内其他进程。
- 不再使用 `/tmp/.session_status`、`/tmp/.pr_status`、`/tmp/pr-monitor-state` 等全局共享文件。

### 评论类型

GitHub PR 有两种类型的评论，CodeMate 会监控：

| 类型 | 位置 | API 端点 | 用例 |
|------|----------|--------------|----------|
| **Review Comment** | File Changes | `/pulls/{pr}/comments` | 针对特定行的代码特定反馈 |
| **Issue Comment** | PR Comment | `/issues/{pr}/comments` | 一般讨论、问题、请求 |

### Review Comment Workflow

当有人留下 **review comment**（inline code comment）时：

1. 监控检测到未解决的 review comments
2. 通过 Stop continuation 请求所选 agent 使用 `pr:fix-comments`
3. Agent 使用该 workflow：
   - 读取反馈
   - 进行代码更改
   - commit 并推送
   - 回复 "CodeMate Replied: ..." 标记为已解决

### Issue Comment Workflow

当有人留下 **issue comment**（一般 PR comment）时：

1. 监控检测到没有 👀 reaction 的新 issue comments
2. 通过 Stop continuation 将实际 comment 内容发送给所选 agent
3. Agent 处理请求
4. Agent 使用 `pr:ack-comments` 添加 👀 reaction
5. 未来运行会跳过带有 👀 reaction 的 comments

### Filtering Logic

Comments 在以下情况下会被过滤掉：
- 以 "CodeMate Replied:" 开头（已处理）
- 有 👀 reaction（已确认）
- 由 bot 创建（login 以 `[bot]` 结尾）

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
