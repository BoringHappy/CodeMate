# Use uv for Python Development

Use `uv` for Python development in this repository.

**Guidance:**
- Run Python commands through `uv run`, for example `uv run python -m compileall src`.
- Use `uv sync` to install or refresh dependencies from `pyproject.toml` and `uv.lock`.
- Use `uv add` and `uv remove` when changing Python dependencies.
- Do not use `pip install`, `python -m pip`, `poetry`, or `pipenv` for repo dependency management unless the user explicitly asks for it.

