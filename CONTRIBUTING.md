# Contributing to AgentFlow

Thank you for your interest in contributing to AgentFlow!

## Development Setup

```bash
# Clone the repository
git clone https://github.com/rlemke/agentflow.git
cd agentflow

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode with all extras
pip install -e ".[dev,test,dashboard,mcp]"

# Install pre-commit hooks
pre-commit install
```

## Running Tests

```bash
# Full test suite
pytest tests/ examples/ -v

# Stop on first failure
pytest tests/ examples/ -v -x

# With coverage
pytest tests/ examples/ --cov=afl --cov-report=term-missing

# MongoDB integration tests (requires running MongoDB)
pytest tests/runtime/test_mongo_store.py --mongodb -v
```

## Code Style

This project uses automated tooling to enforce consistent style:

- **ruff** for linting and formatting (configured in `pyproject.toml`)
- **mypy** for type checking
- **pre-commit** hooks run automatically on each commit

```bash
# Format code
ruff format .

# Lint
ruff check .

# Type check
mypy afl/
```

### Style Guidelines

- Type hints on all functions
- Docstrings on public API
- No runtime dependencies beyond `lark` (dashboard, MCP deps are optional)
- All grammar constructs must have parser tests
- Error cases must verify line/column reporting

## Pull Request Process

1. Create a feature branch from `main`
2. Make your changes with tests
3. Ensure `pytest tests/ examples/ -v` passes
4. Ensure `ruff check .` and `ruff format --check .` pass
5. Open a pull request against `main`

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
