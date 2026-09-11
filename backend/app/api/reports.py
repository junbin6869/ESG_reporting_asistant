from fastapi import APIRouter, HTTPException, Response

from app.agents.report_agent import get_report_agent_run, start_report_agent
from app.schemas import AgentRun, GenerateReportRequest, GeneratedReport
from app.services.report_service import (
    build_report_evidence_register_csv,
    build_report_package,
    build_report_pdf,
    delete_report,
    generate_report,
    list_reports,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/generate", response_model=GeneratedReport)
def post_generate_report(payload: GenerateReportRequest) -> GeneratedReport:
    return generate_report(payload)


@router.get("", response_model=list[GeneratedReport])
def get_reports() -> list[GeneratedReport]:
    return list_reports()


@router.post("/generate-agentic", response_model=AgentRun)
def post_generate_agentic_report(payload: GenerateReportRequest) -> AgentRun:
    return start_report_agent(payload)


@router.get("/agent-runs/{run_id}", response_model=AgentRun)
def get_agent_run(run_id: str) -> AgentRun:
    run = get_report_agent_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@router.delete("/{report_id}")
def delete_report_by_id(report_id: str) -> dict[str, bool]:
    deleted = delete_report(report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Report not found")
    return {"deleted": True}


@router.get("/{report_id}/pdf")
def get_report_pdf(report_id: str) -> Response:
    pdf = build_report_pdf(report_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="esg-report-{report_id}.pdf"'
        },
    )


@router.get("/{report_id}/evidence-register.csv")
def get_report_evidence_register(report_id: str) -> Response:
    csv_content = build_report_evidence_register_csv(report_id)
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="esg-evidence-register-{report_id}.csv"'
            )
        },
    )


@router.get("/{report_id}/package.zip")
def get_report_package(report_id: str) -> Response:
    package = build_report_package(report_id)
    return Response(
        content=package,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="esg-report-package-{report_id}.zip"'
            )
        },
    )
