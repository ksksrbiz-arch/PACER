# Contributing to PACER

Thank you for your interest in contributing to PACER!

## Development Setup

1. Clone the repository:
```bash
git clone https://github.com/ksksrbiz-arch/PACER.git
cd PACER
```

2. Install Poetry:
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

3. Install dependencies:
```bash
poetry install
```

4. Run tests to verify setup:
```bash
poetry run pytest
```

## Development Workflow

1. Create a feature branch:
```bash
git checkout -b feature/your-feature-name
```

2. Make your changes and ensure:
   - Code follows style guidelines (run `poetry run black src tests`)
   - Linting passes (run `poetry run ruff check src tests`)
   - Type checking passes (run `poetry run mypy src`)
   - All tests pass (run `poetry run pytest`)
   - New features have tests

3. Commit your changes:
```bash
git add .
git commit -m "Description of changes"
```

4. Push and create a pull request:
```bash
git push origin feature/your-feature-name
```

## Code Style

- Follow PEP 8 style guidelines
- Use Black for code formatting
- Use type hints for all functions
- Write docstrings for all public APIs
- Keep functions focused and concise

## Testing

- Write tests for all new features
- Maintain or improve code coverage
- Use descriptive test names
- Test edge cases and error conditions

## Pull Request Process

1. Update documentation if needed
2. Add tests for new functionality
3. Ensure all CI checks pass
4. Request review from maintainers
5. Address review feedback

## Reporting Issues

When reporting issues, include:
- Clear description of the problem
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Python version)
- Error messages and stack traces

## Questions?

Feel free to open an issue for questions or discussion.
