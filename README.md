# docx-agent

A LangGraph agent that edits existing Microsoft Word (`.docx`) documents from natural-language
instructions (insert, replace, remove text), changing only what the instruction requires.

> Work in progress — full architecture, decisions, limitations and testing write-up will follow.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 automatically).

```bash
uv sync                 # create .venv and install locked dependencies
cp .env.example .env    # add your ANTHROPIC_API_KEY
uv run docx-agent --help
make check              # lint + strict types + tests with coverage (same gate as CI)
```
