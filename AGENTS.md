# Repository Guidelines

## Architecture Overview

Tencent IMA Copilot MCP Server is built on FastMCP v2, providing a Model Context Protocol (MCP) interface to encapsulate IMA Copilot web capabilities for external agents, with modular configuration using environment variables (`.env`).

**Core technologies:**
- FastMCP v2 for protocol logic (`ima_server_simple.py`)
- Tenacity for robust retries and token refresh
- Loguru for structured, modern logging
- `.env` and Pydantic for configuration management

**Key modules:**
- `ima_server_simple.py`: Server entry, resource/tool definitions
- `src/config.py`: Settings loader (validation, auto-generates defaults)
- `src/ima_client.py`: Handles IMA API, auth, token refresh
- `src/models.py`: Data models, SSE content

## Project Structure & Module Organization

- **Source Code**: Core in `src/`
- **Tests**: Root-level tests (e.g., `test_sync.py`)
- **Config**: `.env`, `.env.example`
- **Logs**: `logs/`
- **Database/Assets**: `chromadb/`, `data/`

## Build, Test, and Development Commands

- **Install dependencies:**  
  `pip install -r requirements.txt`
- **Run server:**  
  `python ima_server_simple.py`
- **Run tests:**  
  `python test_sync.py`
- **Docker Compose (recommended):**  
  `docker-compose up -d`
- **View logs:**  
  `docker-compose logs -f` or `docker logs -f <container>`

## Configuration Example

Duplicate `.env.example` as `.env` and fill authentication fields:
```bash
IMA_X_IMA_COOKIE="your_x_ima_cookie"
IMA_X_IMA_BKN="your_x_ima_bkn"
IMA_KNOWLEDGE_BASE_CATALOG_FILE="/app/data/.ima_knowledge_bases.json"
```
All credentials must be valid. Never commit secrets.

## Agent Usage & Endpoints

- **Main endpoint:** MCP HTTP at `/mcp` (default port 8081).
- **Inspector tool:** Connect via `http://127.0.0.1:8081/mcp` with MCP Inspector or compatible agent.
- **Ask tool:** Main QA interface; provides text and structured references.
- **Resource access:** Tools like `ima://config` and `ima://help` available for diagnostics.

## Coding Style & Naming Conventions

- Python 3, 4-space indent, no tabs.
- `snake_case` for variables/functions, `PascalCase` for classes.
- One import per line, no wildcards.
- Format/lint with `black .` and `isort .` before merging.

## Testing Guidelines

- Standard `unittest`/`asyncio` patterns, expand to `pytest` as needed.
- Place per-feature or global in root (`test_*.py`).
- Cover new features and edge cases.
- Run as: `python test_sync.py`

## Commit & Pull Request Guidelines

- **Commits:** Short, clear, imperative titles (≤72 chars). Example:  
  `Fix token auto-refresh in MCP handler`
- **PRs:**
  - Describe change, motivation, related issues (e.g., "Closes #12")
  - Show outputs/screenshots if UI or behavior changes
  - Pass all tests/linters before review

## Troubleshooting & Tips

- For token/auth failures, re-copy `IMA_X_IMA_COOKIE` and `IMA_X_IMA_BKN` as described above.
- Set correct knowledge base via `IMA_KNOWLEDGE_BASE_ID` in `.env`.
- For concurrency errors (`Code=3`), reduce concurrent requests, server will retry with backoff.
- Check and follow logs in `logs/`.
- Consult README.md for full FAQ.

## Security
- Store credentials in `.env` only. Do not share sensitive values.
- Do not hardcode secrets or tokens in code.

