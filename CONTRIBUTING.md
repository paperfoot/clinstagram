# Contributing to Clinstagram

Thanks for your interest in contributing. Here is how to get started.

## Setup

```bash
git clone https://github.com/paperfoot/clinstagram.git
cd clinstagram
pip install -e ".[dev]"
```

## Running Tests

```bash
uv run --extra dev pytest -q   # pyproject points tests at src/, no PYTHONPATH needed
```

All tests (468 on 4 Nov 2026) should pass before you submit a PR.

## How to Contribute

1. Fork the repo and create a branch from `main`.
2. Make your changes. Add tests if you are adding new functionality.
3. Run the test suite and confirm everything passes.
4. Open a pull request with a clear description of what you changed and why.

## What We Are Looking For

- Bug fixes with a test that reproduces the issue.
- New backend capabilities or command improvements.
- Documentation improvements.
- Performance optimizations.

## Code Style

- Follow the existing patterns in the codebase.
- Use type hints.
- Keep functions focused and small.

## Reporting Issues

Open a GitHub issue with:
- What you expected to happen.
- What actually happened.
- Steps to reproduce.
- Your Python version and OS.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
