# CodeMate - Docker environment for Claude Code and Codex
# Uses the base image which contains system packages and development tools
ARG BASE_IMAGE=ghcr.io/boringhappy/codemate-base:latest
FROM ${BASE_IMAGE}

# Switch to root for setup operations
USER root

# Copy setup scripts as root (755 allows agent user to read/execute)
COPY --chmod=755 setup /usr/local/bin/setup

# Install tmux configuration for the agent user.
# Enables mouse scroll, focus events, and Kitty Keyboard Protocol passthrough
# so Shift+Enter and similar modified keys work in Claude Code from any
# CSI-u-capable host terminal (iTerm2, Ghostty, WezTerm, Kitty, etc.).
COPY --chown=agent:agent --chmod=644 tmux.conf /home/agent/.tmux.conf

# Environment variables for plugin configuration
ENV CODEMATE_DEFAULT_MARKETPLACES="BoringHappy/CodeMate"
ENV CODEMATE_DEFAULT_PLUGINS="git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate"

# Switch to agent user and install both supported coding agents.
USER agent
# Prefer npm-managed tools over any persisted standalone symlink in ~/.local/bin.
ENV PATH="/usr/local/share/npm-global/bin:/home/agent/.local/bin:${PATH}"
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && npm install -g @cometix/ccline @openai/codex@latest \
    && codex --version

ENTRYPOINT ["/usr/local/bin/setup/setup.sh"]
CMD ["/usr/local/bin/setup/run.sh"]
