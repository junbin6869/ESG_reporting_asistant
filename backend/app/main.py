from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import classifications, guidelines, invoices, reports, summaries
from app.core.config import settings
from app.db.database import init_db
from app.services.classification_job_service import (
    enqueue_unclassified_invoices,
    recover_stale_classification_jobs,
    start_classification_worker,
    stop_classification_worker,
)


app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    recover_stale_classification_jobs()
    enqueue_unclassified_invoices()
    start_classification_worker()
    try:
        from app.agents.report_agent import resume_incomplete_report_runs
    except ImportError:
        # The persistence hook is optional while older report-agent builds run.
        resume_incomplete_report_runs = None
    if resume_incomplete_report_runs:
        resume_incomplete_report_runs()


@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_classification_worker()
    try:
        from app.agents.report_agent import close_report_graph
    except ImportError:
        close_report_graph = None
    if close_report_graph:
        close_report_graph()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(invoices.router)
app.include_router(invoices.batch_router)
app.include_router(classifications.router)
app.include_router(summaries.router)
app.include_router(reports.router)
app.include_router(guidelines.router)
