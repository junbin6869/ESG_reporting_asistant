import json
import os
import sqlite3
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock, Thread, current_thread
from time import monotonic
from typing import Any, TypedDict

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.core.agent_audit import log_agent_event
from app.core.config import settings
from app.core.llm import get_chat_model
from app.core.report_format import report_contract, validate_report_content
from app.db.database import db_session, decode_json, encode_json, utc_now
from app.schemas import AgentRun, AgentStep, GenerateReportRequest, GeneratedReport, InvoiceRow
from app.services.invoice_service import list_invoices
from app.services.rag_service import retrieve_guidelines
from app.services.report_service import (
    build_reviewed_invoice_summary,
    get_report,
    save_report,
)


_runs: dict[str, AgentRun] = {}
_lock = Lock()
_thread_lock = Lock()
_report_threads: dict[str, Thread] = {}
_checkpoint_connection: sqlite3.Connection | None = None
_persist_run_updates: ContextVar[bool] = ContextVar(
    "persist_report_agent_run_updates",
    default=True,
)

MAX_SEARCH_CALLS = 8
MAX_AGENT_LOOPS = 16
MAX_TOOL_RESULTS = 5
SEARCH_TOOL_NAME = "search_esg_guidelines"

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": SEARCH_TOOL_NAME,
        "description": (
            "Search the local ESG guideline vector database for evidence. "
            "Use focused, distinct queries until the reviewed invoice themes have "
            "enough evidence to write the report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A focused ESG disclosure, metric, or evidence query.",
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TOOL_RESULTS,
                    "description": "Number of guideline chunks to retrieve.",
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "A concise, auditable reason why this search is needed based "
                        "on the current evidence coverage."
                    ),
                },
                "evidence_gaps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific evidence gaps this search should address.",
                },
            },
            "required": ["query", "rationale", "evidence_gaps"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class ToolRequest:
    id: str
    name: str
    arguments: str


@dataclass
class AgentDecision:
    content: str
    tool_requests: list[ToolRequest]
    assistant_message: dict[str, Any]


class ReportGraphState(TypedDict, total=False):
    run_id: str
    request: dict[str, str]
    invoice_summary: str
    reviewed_invoice_count: int
    messages: list[dict[str, Any]]
    seen_chunk_ids: list[str]
    search_calls: int
    loop_index: int
    pending_tool_requests: list[dict[str, str]]
    report_content: str
    report: dict[str, Any]


def start_report_agent(payload: GenerateReportRequest) -> AgentRun:
    run_id = str(uuid.uuid4())
    now = utc_now()
    run = AgentRun(
        id=run_id,
        status="queued",
        request=payload,
        steps=[
            AgentStep(
                id="prepare_context",
                label="Preparing reviewed invoices",
                status="queued",
            )
        ],
        created_at=now,
        updated_at=now,
    )
    _store_run(run)
    _start_report_thread(run_id)
    return run


def get_report_agent_run(run_id: str) -> AgentRun | None:
    persisted = _load_run(run_id)
    if persisted:
        with _lock:
            _runs[run_id] = persisted
        return persisted
    with _lock:
        return _runs.get(run_id)


def resume_incomplete_report_runs() -> None:
    """Resume durable queued/running report graphs after an API restart."""
    try:
        with db_session() as db:
            db.execute(
                """
                UPDATE report_agent_runs
                SET status = 'queued', updated_at = ?
                WHERE status = 'running'
                """,
                (utc_now(),),
            )
            rows = db.execute(
                "SELECT id FROM report_agent_runs WHERE status = 'queued'"
            ).fetchall()
    except sqlite3.OperationalError:
        return

    for row in rows:
        _start_report_thread(row["id"])


def _start_report_thread(run_id: str) -> Thread:
    with _thread_lock:
        existing = _report_threads.get(run_id)
        if existing and existing.is_alive():
            return existing

        def target() -> None:
            try:
                _execute_report_agent(run_id)
            finally:
                with _thread_lock:
                    if _report_threads.get(run_id) is current_thread():
                        _report_threads.pop(run_id, None)

        thread = Thread(
            target=target,
            name=f"report-agent-{run_id[:8]}",
            daemon=True,
        )
        _report_threads[run_id] = thread
        thread.start()
        return thread


def _execute_report_agent(run_id: str) -> None:
    if not _claim_run(run_id):
        return
    run = _require_run(run_id)
    _update_run(run_id, status="running", error=None)
    log_agent_event(
        run_id,
        "run_started",
        title=run.request.title,
        period_start=run.request.period_start,
        period_end=run.request.period_end,
    )

    try:
        report = _run_autonomous_agent(run_id, run.request, persist=True)
        _update_run(run_id, status="completed", report=report, error=None)
        log_agent_event(run_id, "run_completed", report_id=report.id)
    except Exception as exc:
        _fail_current_step(run_id, str(exc))
        _update_run(run_id, status="failed", error=str(exc))
        log_agent_event(run_id, "run_failed", error=str(exc))


def _run_autonomous_agent(
    run_id: str,
    request: GenerateReportRequest,
    *,
    persist: bool = False,
) -> GeneratedReport:
    initial_state: ReportGraphState = {
        "run_id": run_id,
        "request": request.model_dump(),
        "seen_chunk_ids": [],
        "search_calls": 0,
        "loop_index": 0,
        "pending_tool_requests": [],
    }
    config = {
        "configurable": {"thread_id": run_id},
        "recursion_limit": (MAX_AGENT_LOOPS * 3) + 8,
    }

    persistence_token = _persist_run_updates.set(persist)
    try:
        if persist and _run_is_persisted(run_id):
            graph = _get_persistent_graph()
            snapshot = graph.get_state(config)
            if snapshot.values and snapshot.next:
                result = graph.invoke(None, config=config)
            elif snapshot.values and snapshot.values.get("report"):
                result = snapshot.values
            else:
                result = graph.invoke(initial_state, config=config)
        else:
            # Unit tests and direct internal calls can run without durable checkpoint tables.
            result = _build_report_graph().invoke(initial_state, config=config)
    finally:
        _persist_run_updates.reset(persistence_token)

    report_data = result.get("report")
    if not report_data:
        raise ValueError("Report graph completed without a saved report.")
    return GeneratedReport.model_validate(report_data)


def _build_report_graph(*, checkpointer=None):
    builder = StateGraph(ReportGraphState)
    builder.add_node("prepare_context", _prepare_context_node)
    builder.add_node("agent_decision", _agent_decision_node)
    builder.add_node("search_guidelines", _search_guidelines_node)
    builder.add_node("validate_report", _validate_report_node)
    builder.add_node("save_report", _save_report_node)

    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", "agent_decision")
    builder.add_conditional_edges(
        "agent_decision",
        _route_after_agent_decision,
        {
            "search_guidelines": "search_guidelines",
            "validate_report": "validate_report",
        },
    )
    builder.add_edge("search_guidelines", "agent_decision")
    builder.add_edge("validate_report", "save_report")
    builder.add_edge("save_report", END)
    return builder.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def _get_persistent_graph():
    from langgraph.checkpoint.sqlite import SqliteSaver

    global _checkpoint_connection
    checkpoint_path = settings.langgraph_checkpoint_path
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    _checkpoint_connection = connection
    checkpointer = SqliteSaver(connection)
    return _build_report_graph(checkpointer=checkpointer)


def close_report_graph(timeout: float = 5.0) -> bool:
    """Wait briefly for report runs, then safely release the checkpoint DB."""
    global _checkpoint_connection
    deadline = monotonic() + max(0.0, timeout)
    with _thread_lock:
        threads = list(_report_threads.values())
    for thread in threads:
        if thread is current_thread() or not thread.is_alive():
            continue
        thread.join(timeout=max(0.0, deadline - monotonic()))

    with _thread_lock:
        if any(thread.is_alive() for thread in _report_threads.values()):
            # The process is already shutting down. Leaving the connection open is
            # safer than closing it underneath a node that is writing a checkpoint.
            return False

    _get_persistent_graph.cache_clear()
    if _checkpoint_connection is not None:
        _checkpoint_connection.close()
        _checkpoint_connection = None
    return True


def _prepare_context_node(state: ReportGraphState) -> dict[str, Any]:
    if state.get("messages"):
        return {}

    run_id = state["run_id"]
    request = GenerateReportRequest.model_validate(state["request"])
    _set_step(run_id, "prepare_context", status="running", detail=None)
    invoices = _get_reviewed_invoices(request.period_start, request.period_end)
    invoice_summary = build_reviewed_invoice_summary(invoices)
    _set_step(
        run_id,
        "prepare_context",
        status="completed",
        detail=f"Loaded {len(invoices)} reviewed invoices for the reporting period.",
    )
    log_agent_event(
        run_id,
        "invoices_prepared",
        reviewed_invoice_count=len(invoices),
    )
    return {
        "invoice_summary": invoice_summary,
        "reviewed_invoice_count": len(invoices),
        "messages": [
            {"role": "system", "content": _build_agent_system_prompt()},
            {
                "role": "user",
                "content": _build_agent_request(request, invoice_summary),
            },
        ],
    }


def _agent_decision_node(state: ReportGraphState) -> dict[str, Any]:
    run_id = state["run_id"]
    loop_index = int(state.get("loop_index", 0)) + 1
    search_calls = int(state.get("search_calls", 0))
    if loop_index > MAX_AGENT_LOOPS:
        raise ValueError("Agent did not produce a report within the reasoning loop limit.")

    allow_search = search_calls < MAX_SEARCH_CALLS and loop_index < MAX_AGENT_LOOPS
    messages = list(state.get("messages", []))
    if not allow_search:
        messages.append(
            {
                "role": "user",
                "content": (
                    "No more ESG search calls are available. Produce the complete "
                    "report now and clearly state all remaining evidence limitations."
                ),
            }
        )

    step_id = f"decision_{loop_index}"
    _append_step(
        run_id,
        AgentStep(
            id=step_id,
            label=f"Agent evidence decision {loop_index}",
            status="running",
            detail=(
                f"Evaluating evidence with {search_calls} of "
                f"{MAX_SEARCH_CALLS} searches used."
            ),
        ),
    )
    decision = _request_agent_decision(messages, allow_search=allow_search)
    messages.append(decision.assistant_message)

    if not decision.tool_requests:
        content = decision.content.strip()
        if not content:
            raise ValueError("Agent returned neither a tool call nor report content.")
        _set_step(
            run_id,
            step_id,
            status="completed",
            detail="Agent determined the available evidence is ready for reporting.",
        )
        log_agent_event(
            run_id,
            "agent_decision",
            loop_index=loop_index,
            decision="generate_report",
            rationale="Agent returned the report instead of requesting more evidence.",
            search_calls_used=search_calls,
        )
        return {
            "messages": messages,
            "loop_index": loop_index,
            "pending_tool_requests": [],
            "report_content": content,
        }

    _set_step(
        run_id,
        step_id,
        status="completed",
        detail=f"Agent requested {len(decision.tool_requests)} local ESG search call(s).",
    )
    log_agent_event(
        run_id,
        "agent_decision",
        loop_index=loop_index,
        decision="search_more",
        requested_search_count=len(decision.tool_requests),
        search_calls_used=search_calls,
    )
    return {
        "messages": messages,
        "loop_index": loop_index,
        "pending_tool_requests": [request.__dict__ for request in decision.tool_requests],
    }


def _route_after_agent_decision(state: ReportGraphState) -> str:
    if state.get("pending_tool_requests"):
        return "search_guidelines"
    return "validate_report"


def _search_guidelines_node(state: ReportGraphState) -> dict[str, Any]:
    run_id = state["run_id"]
    messages = list(state.get("messages", []))
    search_calls = int(state.get("search_calls", 0))
    seen_chunk_ids = set(state.get("seen_chunk_ids", []))
    loop_index = int(state.get("loop_index", 0))

    for tool_index, raw_request in enumerate(
        state.get("pending_tool_requests", []), start=1
    ):
        tool_request = ToolRequest(**raw_request)
        step_id = f"search_{search_calls + 1}_{loop_index}_{tool_index}"
        _append_step(
            run_id,
            AgentStep(
                id=step_id,
                label="Searching local ESG guidelines",
                status="running",
            ),
        )
        audit_fields = _get_tool_audit_fields(tool_request)
        log_agent_event(
            run_id,
            "esg_search_started",
            loop_index=loop_index,
            search_number=search_calls + 1,
            **audit_fields,
        )
        result, detail, executed = _execute_search_tool(
            tool_request,
            seen_chunk_ids=seen_chunk_ids,
            search_calls=search_calls,
        )
        if executed:
            search_calls += 1
        _set_step(run_id, step_id, status="completed", detail=detail)
        log_agent_event(
            run_id,
            "esg_search_completed",
            loop_index=loop_index,
            executed=executed,
            query=result.get("query", audit_fields.get("query", "")),
            new_evidence_count=result.get("new_evidence_count", 0),
            duplicate_count=result.get("duplicate_count", 0),
            searches_remaining=result.get(
                "searches_remaining", MAX_SEARCH_CALLS - search_calls
            ),
            error=result.get("error"),
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_request.id,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

    return {
        "messages": messages,
        "search_calls": search_calls,
        "seen_chunk_ids": sorted(seen_chunk_ids),
        "pending_tool_requests": [],
    }


def _validate_report_node(state: ReportGraphState) -> dict[str, Any]:
    run_id = state["run_id"]
    content = state.get("report_content", "").strip()
    if int(state.get("search_calls", 0)) >= MAX_SEARCH_CALLS:
        content = _add_search_limit_disclosure(content)

    step_id = "validate_report"
    _append_step(
        run_id,
        AgentStep(
            id=step_id,
            label="Validating ESG report contract",
            status="running",
        ),
    )
    validate_report_content(content)
    _set_step(
        run_id,
        step_id,
        status="completed",
        detail="Required report sections, disclosures, and evidence markers are present.",
    )
    return {"report_content": content}


def _save_report_node(state: ReportGraphState) -> dict[str, Any]:
    run_id = state["run_id"]
    request = GenerateReportRequest.model_validate(state["request"])
    search_calls = int(state.get("search_calls", 0))
    step_id = "save_report"
    _append_step(
        run_id,
        AgentStep(
            id=step_id,
            label="Saving autonomous ESG report",
            status="running",
            detail=f"Agent completed research after {search_calls} local ESG search call(s).",
        ),
    )
    report = save_report(
        request,
        state["report_content"],
        source_run_id=run_id,
    )
    _set_step(
        run_id,
        step_id,
        status="completed",
        detail=f"Saved report {report.id} after {search_calls} local ESG search call(s).",
    )
    _set_report_id(run_id, report.id)
    log_agent_event(
        run_id,
        "report_saved",
        report_id=report.id,
        search_calls_used=search_calls,
    )
    return {"report": report.model_dump()}


def _request_agent_decision(
    messages: list[dict[str, Any]],
    allow_search: bool,
) -> AgentDecision:
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured")

    model = get_chat_model(temperature=0)
    if allow_search:
        model = model.bind_tools([SEARCH_TOOL], tool_choice="auto")
    message = model.invoke([_to_langchain_message(item) for item in messages])
    tool_requests = [
        ToolRequest(
            id=str(call.get("id") or uuid.uuid4()),
            name=str(call.get("name") or ""),
            arguments=json.dumps(call.get("args") or {}, ensure_ascii=False),
        )
        for call in getattr(message, "tool_calls", [])
    ]
    content = _message_text(message)
    assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_requests:
        assistant_message["tool_calls"] = [
            {
                "id": request.id,
                "type": "function",
                "function": {
                    "name": request.name,
                    "arguments": request.arguments,
                },
            }
            for request in tool_requests
        ]
    return AgentDecision(
        content=content,
        tool_requests=tool_requests,
        assistant_message=assistant_message,
    )


def _to_langchain_message(message: dict[str, Any]) -> BaseMessage:
    role = message.get("role")
    content = message.get("content") or ""
    if role == "system":
        return SystemMessage(content=content)
    if role == "user":
        return HumanMessage(content=content)
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=str(message.get("tool_call_id") or "missing-tool-call"),
        )
    tool_calls = []
    for call in message.get("tool_calls", []):
        function = call.get("function", {})
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        tool_calls.append(
            {
                "id": str(call.get("id") or uuid.uuid4()),
                "name": str(function.get("name") or ""),
                "args": arguments,
                "type": "tool_call",
            }
        )
    return AIMessage(content=content, tool_calls=tool_calls)


def _message_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    text_parts: list[str] = []
    for block in message.content:
        if isinstance(block, str):
            text_parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            text_parts.append(str(block.get("text") or ""))
    return "\n".join(part for part in text_parts if part).strip()


def _execute_search_tool(
    tool_request: ToolRequest,
    seen_chunk_ids: set[str],
    search_calls: int,
) -> tuple[dict[str, Any], str, bool]:
    if tool_request.name != SEARCH_TOOL_NAME:
        result = {"error": f"Unknown tool: {tool_request.name}"}
        return result, result["error"], False
    if search_calls >= MAX_SEARCH_CALLS:
        result = {
            "error": (
                f"Local ESG search limit of {MAX_SEARCH_CALLS} calls reached. "
                "Complete the report and clearly state evidence limitations."
            )
        }
        return result, result["error"], False

    try:
        arguments = json.loads(tool_request.arguments or "{}")
    except json.JSONDecodeError:
        result = {"error": "Tool arguments must be valid JSON."}
        return result, result["error"], False

    query = str(arguments.get("query") or "").strip()
    if not query:
        result = {"error": "A non-empty query is required."}
        return result, result["error"], False
    rationale = str(arguments.get("rationale") or "").strip()
    evidence_gaps = arguments.get("evidence_gaps")
    if not rationale or not isinstance(evidence_gaps, list):
        result = {"error": "Search calls require a rationale and an evidence_gaps list."}
        return result, result["error"], False

    try:
        top_k = int(arguments.get("top_k", 3))
    except (TypeError, ValueError):
        top_k = 3
    top_k = max(1, min(top_k, MAX_TOOL_RESULTS))
    evidence = retrieve_guidelines(query, top_k=top_k)
    new_items = []
    duplicate_count = 0
    for item in evidence:
        if item.chunk_id in seen_chunk_ids:
            duplicate_count += 1
            continue
        seen_chunk_ids.add(item.chunk_id)
        new_items.append(item.model_dump())

    result = {
        "query": query,
        "new_evidence": new_items,
        "new_evidence_count": len(new_items),
        "duplicate_count": duplicate_count,
        "searches_remaining": MAX_SEARCH_CALLS - search_calls - 1,
    }
    detail = (
        f'Query "{query}" returned {len(new_items)} new guideline chunk(s)'
        f" and {duplicate_count} duplicate(s). Reason: {rationale[:300]}"
    )
    return result, detail, True


def _get_tool_audit_fields(tool_request: ToolRequest) -> dict[str, Any]:
    try:
        arguments = json.loads(tool_request.arguments or "{}")
    except json.JSONDecodeError:
        return {"tool_name": tool_request.name, "arguments_valid": False}
    gaps = arguments.get("evidence_gaps", [])
    if not isinstance(gaps, list):
        gaps = []
    return {
        "tool_name": tool_request.name,
        "arguments_valid": True,
        "query": str(arguments.get("query") or "")[:500],
        "top_k": arguments.get("top_k", 3),
        "rationale": str(arguments.get("rationale") or "")[:1000],
        "evidence_gaps": [str(gap)[:300] for gap in gaps[:10]],
    }


def _get_reviewed_invoices(period_start: str, period_end: str) -> list[InvoiceRow]:
    return [
        invoice
        for invoice in list_invoices()
        if period_start <= invoice.invoice_date <= period_end
        and invoice.classification
        and invoice.classification.status == "Reviewed"
    ]


def _add_search_limit_disclosure(content: str) -> str:
    heading = "8. Data Quality, Limitations and Missing KPIs"
    disclosure = (
        f"\nLocal ESG research limit: The agent used all {MAX_SEARCH_CALLS} available "
        "guideline searches. Any remaining unsupported claims require additional "
        "source documents or human review.\n"
    )
    if disclosure.strip() in content:
        return content
    if heading in content:
        return content.replace(heading, heading + disclosure, 1)
    return content


def _validate_report_content(content: str) -> None:
    validate_report_content(content)


def _build_agent_system_prompt() -> str:
    return f"""
You are an autonomous ESG reporting research agent.

You receive all reviewed invoices for the requested reporting period up front.
Your only available research tool searches a local ESG guideline database.
Decide which focused guideline queries to run, whether the evidence is sufficient,
and whether another search is useful.

Rules:
- Use only the reviewed invoice summary and evidence returned by the local tool.
- Do not invent company activities, impacts, metrics, or guideline requirements.
- Search different ESG topics when invoices span multiple themes.
- Stop searching when additional searches are unlikely to improve the report.
- You may use at most {MAX_SEARCH_CALLS} local ESG searches.
- Every search call must include a concise rationale and the evidence gaps addressed.
- If evidence remains incomplete, still produce the report and state the limitation.
- When ready, respond with the complete report and do not call a tool.

{report_contract()}
""".strip()


def _build_agent_request(
    request: GenerateReportRequest,
    invoice_summary: str,
) -> str:
    return f"""
Create an evidence-led ESG draft report.

Report title: {request.title}
Reporting period: {request.period_start} to {request.period_end}

Reviewed invoice summary:
{invoice_summary}

First assess the invoice themes and current evidence needs. Use the local ESG
search tool as many times as useful within the allowed limit. When the evidence
is sufficient, write the complete report.
""".strip()


def _require_run(run_id: str) -> AgentRun:
    with _lock:
        run = _runs.get(run_id)
    if run:
        return run
    run = _load_run(run_id)
    if not run:
        raise ValueError(f"Agent run not found: {run_id}")
    with _lock:
        _runs[run_id] = run
    return run


def _store_run(run: AgentRun) -> None:
    with _lock:
        _runs[run.id] = run
    _persist_run(run)


def _update_run(run_id: str, **patch: Any) -> None:
    run = _require_run(run_id)
    data = run.model_dump()
    data.update(patch)
    data["updated_at"] = utc_now()
    _store_run(AgentRun(**data))


def _append_step(run_id: str, step: AgentStep) -> None:
    run = _require_run(run_id)
    existing = {item.id: item for item in run.steps}
    existing[step.id] = step
    ordered_ids = [item.id for item in run.steps]
    if step.id not in ordered_ids:
        ordered_ids.append(step.id)
    _update_run(run_id, steps=[existing[step_id] for step_id in ordered_ids])


def _set_step(run_id: str, step_id: str, status: str, detail: str | None) -> None:
    run = _require_run(run_id)
    steps = [
        AgentStep(
            id=step.id,
            label=step.label,
            status=status if step.id == step_id else step.status,
            detail=detail if step.id == step_id else step.detail,
        )
        for step in run.steps
    ]
    _update_run(run_id, steps=steps)


def _fail_current_step(run_id: str, detail: str) -> None:
    run = _require_run(run_id)
    running = next((step for step in reversed(run.steps) if step.status == "running"), None)
    if running:
        _set_step(run_id, running.id, status="failed", detail=detail)


def _persist_run(run: AgentRun) -> None:
    if not _persist_run_updates.get():
        return

    try:
        with db_session() as db:
            db.execute(
                """
                INSERT INTO report_agent_runs (
                    id, status, request_json, report_id, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    request_json = excluded.request_json,
                    report_id = COALESCE(excluded.report_id, report_agent_runs.report_id),
                    error = excluded.error,
                    updated_at = excluded.updated_at
                """,
                (
                    run.id,
                    run.status,
                    encode_json(run.request.model_dump()),
                    run.report.id if run.report else None,
                    run.error,
                    run.created_at,
                    run.updated_at,
                ),
            )
            for sequence, step in enumerate(run.steps):
                db.execute(
                    """
                    INSERT INTO report_agent_steps (
                        run_id, id, label, status, detail, sequence, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, id) DO UPDATE SET
                        label = excluded.label,
                        status = excluded.status,
                        detail = excluded.detail,
                        sequence = excluded.sequence,
                        updated_at = excluded.updated_at
                    """,
                    (
                        run.id,
                        step.id,
                        step.label,
                        step.status,
                        step.detail,
                        sequence,
                        run.updated_at,
                    ),
                )
    except sqlite3.OperationalError:
        # init_db may not have run in isolated unit tests.
        return


def _load_run(run_id: str) -> AgentRun | None:
    try:
        with db_session() as db:
            row = db.execute(
                "SELECT * FROM report_agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if not row:
                return None
            step_rows = db.execute(
                """
                SELECT * FROM report_agent_steps
                WHERE run_id = ?
                ORDER BY sequence ASC
                """,
                (run_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return None

    report = get_report(row["report_id"]) if row["report_id"] else None
    return AgentRun(
        id=row["id"],
        status=row["status"],
        request=GenerateReportRequest.model_validate(decode_json(row["request_json"], {})),
        steps=[
            AgentStep(
                id=step["id"],
                label=step["label"],
                status=step["status"],
                detail=step["detail"],
            )
            for step in step_rows
        ],
        report=report,
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_is_persisted(run_id: str) -> bool:
    try:
        with db_session() as db:
            return (
                db.execute(
                    "SELECT 1 FROM report_agent_runs WHERE id = ?", (run_id,)
                ).fetchone()
                is not None
            )
    except sqlite3.OperationalError:
        return False


def _claim_run(run_id: str) -> bool:
    if not _run_is_persisted(run_id):
        return True
    with db_session() as db:
        cursor = db.execute(
            """
            UPDATE report_agent_runs
            SET status = 'running', updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (utc_now(), run_id),
        )
    return cursor.rowcount == 1


def _set_report_id(run_id: str, report_id: str) -> None:
    if not _persist_run_updates.get():
        return

    try:
        with db_session() as db:
            db.execute(
                "UPDATE report_agent_runs SET report_id = ?, updated_at = ? WHERE id = ?",
                (report_id, utc_now(), run_id),
            )
    except sqlite3.OperationalError:
        return
