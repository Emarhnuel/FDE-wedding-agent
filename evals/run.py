"""Run the supervisor on the LangSmith inquiry dataset and score it (one experiment per run).

Run: uv run python -m evals.run [--limit N] [--no-upload]

Each example runs the real inquiry path (runtime.build_start -> supervisor) for a throwaway eval
company. AUTO_SEND_ROUTINE is forced off, so every send pauses for review and no email leaves; the
target ends at that pause and reports what the send policy would have decided. Costs model tokens.
"""

import argparse
import os
from itertools import islice
from pathlib import Path
from uuid import uuid4

os.environ["AUTO_SEND_ROUTINE"] = "false"  # before src.middleware reads it

from langchain_core.messages import AIMessage, ToolMessage
from langsmith import Client, evaluate

from src import database, rag
from src.agents import build_supervisor
from src.middleware import requires_human_review
from src.models import model1, model4
from src.runtime import build_start
from src.tools.inquiry_tools import HUBSPOT_BASE_URL, _hubspot_headers, find_hubspot_contact_by_email

from .dataset import DATASET_NAME, SOURCE
from .evaluators import EVALUATORS

COMPANY_ID = "eval-juniper-hollow"
SOURCE_VERSION = "3.0"
PDF_PATH = Path(__file__).resolve().parent.parent / "juniper-hollow-knowledge-pack.pdf"

agent = build_supervisor()


def prepare_knowledge() -> None:
    """Publish the knowledge pack for the eval company once; later runs reuse it."""
    database.init_schema()
    if database.query_records("documents", company_id=f"eq.{COMPANY_ID}", status="eq.published"):
        return
    rag.index_document(
        PDF_PATH, company_id=COMPANY_ID, source_id=SOURCE, version=SOURCE_VERSION,
        title="Juniper Hollow Company Knowledge Pack",
    )
    database.insert_record(
        "documents", {"company_id": COMPANY_ID, "source_id": SOURCE, "version": SOURCE_VERSION, "status": "published"}
    )


def cleanup(emails: list[str]) -> None:
    """Delete this run's drafts, audit rows and HubSpot contacts; the published knowledge stays."""
    for table in ("email_drafts", "audit_logs"):
        database.run_raw_sql(f"DELETE FROM {table} WHERE company_id = $1;", [COMPANY_ID])
    for email in emails:
        if contact := find_hubspot_contact_by_email(email):
            database.http.delete(f"{HUBSPOT_BASE_URL}/crm/v3/objects/contacts/{contact['id']}", headers=_hubspot_headers())


def run_inquiry(inputs: dict) -> dict:
    """Target: one customer inquiry through the supervisor, stopping at the first review pause."""
    thread_id = f"eval:{uuid4().hex[:12]}"
    job = {"kind": "inquiry", "company_id": COMPANY_ID, "thread_id": thread_id, "payload": {"source": "gmail", **inputs}}
    context, message = build_start(job)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 150}

    # subgraphs=True also streams the subagents, where the search and draft tools actually run.
    tool_calls, subagents, knowledge, sent = [], [], [], False
    for _namespace, update in agent.stream(
        {"messages": [{"role": "user", "content": message}]},
        config=config, context=context, stream_mode="updates", subgraphs=True,
    ):
        for delta in update.values():
            msgs = delta.get("messages") if isinstance(delta, dict) else None
            # Some middleware wraps the list in Overwrite(value=[...]).
            for msg in getattr(msgs, "value", msgs) or []:
                if isinstance(msg, AIMessage):
                    for call in msg.tool_calls:
                        tool_calls.append(call["name"])
                        if call["name"] == "task":
                            subagents.append(call["args"].get("subagent_type", ""))
                elif isinstance(msg, ToolMessage):
                    if msg.name == "search_company_knowledge":
                        knowledge.append(str(msg.content))
                    sent |= msg.name in {"send_customer_email", "send_retention_email"} and str(msg.content).startswith("Sent ")

    state = agent.get_state(config)
    paused_at = [r["name"] for i in state.interrupts for r in i.value.get("action_requests", [])]
    drafts = database.query_records(
        "email_drafts", company_id=f"eq.{COMPANY_ID}", thread_id=f"eq.{thread_id}", order="created_at.desc"
    )
    draft = drafts[0] if drafts else None

    # The send policy (middleware.should_interrupt_delivery) as it would decide with auto-send on.
    auto_send_eligible = bool(
        draft and not draft["requires_review"] and draft.get("sources")
        and not requires_human_review(f"{message}\n{draft['subject']}\n{draft['body']}")
    )
    final = state.values.get("messages", [])
    return {
        "draft": draft and {k: draft[k] for k in ("subject", "body", "status", "requires_review", "review_reasons", "sources")},
        "auto_send_eligible": auto_send_eligible,
        "held_for_staff": not auto_send_eligible,
        "sent": sent,
        "paused_at": paused_at,
        "tool_calls": tool_calls,
        "subagents": subagents,
        "knowledge": "\n\n".join(knowledge),
        "final_response": str(final[-1].content) if final else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="only run the first N examples (smoke test)")
    parser.add_argument("--category", help="only run one category, e.g. faq or injection")
    parser.add_argument("--no-upload", action="store_true", help="score locally without recording an experiment")
    args = parser.parse_args()

    client = Client()
    filters = {"metadata": {"category": args.category}} if args.category else {}
    examples = list(islice(client.list_examples(dataset_name=DATASET_NAME, **filters), args.limit))
    prepare_knowledge()
    try:
        results = evaluate(
            run_inquiry,
            data=examples,
            evaluators=EVALUATORS,
            experiment_prefix="supervisor",
            description="Inquiry routing, citations, style, tool path and groundedness.",
            metadata={
                "models": [model4.model_name, model1.model_name],
                "judge": "anthropic/claude-sonnet-5.5",
                "auto_send_routine": False,
            },
            max_concurrency=2,
            upload_results=not args.no_upload,
        )
        scores: dict[str, list[float]] = {}
        for row in results:
            for result in row["evaluation_results"]["results"]:
                scores.setdefault(result.key, []).append(float(result.score or 0))
                if (result.score or 0) < 1:
                    print(f"FAIL {row['example'].inputs['subject']!r} {result.key}: {result.comment}")
        for key, values in scores.items():
            print(f"{key:22} {sum(values) / len(values):.2f}  ({len(values)} examples)")
    finally:
        cleanup([e.inputs["email"] for e in examples])


if __name__ == "__main__":
    main()
