# ESG Assistant Backend

The backend is a FastAPI service using LangChain for model/RAG components and
LangGraph for the durable report workflow.

See the project [README](../Readme.md) for installation, environment variables,
indexing, batch import, run commands, tests, and MCP configuration.

Quick start:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
