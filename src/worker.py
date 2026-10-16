"""Background worker process handling SQS message ingestion.

SQS-triggered Lambda dispatcher: each message carries only a job ID. The worker loads the durable
job, dispatches it to AgentCore Runtime, and returns without waiting for the agent run (the runtime
acknowledges at once and works in a tracked background task). Requires a FIFO queue with the event
source mapping's ReportBatchItemFailures enabled, so only failed messages are retried.
"""

import json
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import boto3

from . import database

AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")

agentcore = boto3.client("bedrock-agentcore")


class RetryLater(Exception):
    """The job cannot be dispatched yet; SQS should redeliver the message."""


def dispatch(job_id: str) -> None:
    """Dispatch one queued job to AgentCore. Returns quietly when there is nothing to do."""
    jobs = database.query_records("jobs", id=f"eq.{job_id}", limit=1)
    if not jobs or jobs[0]["status"] != "queued":
        return  # Unknown, or already dispatched/finished: a duplicate delivery.
    job = jobs[0]

    # One job per conversation thread at a time; FIFO keeps the rest of the thread waiting.
    if database.query_records(
        "jobs", thread_id=f"eq.{job['thread_id']}", status="in.(dispatched,running)", id=f"neq.{job_id}", limit=1
    ):
        raise RetryLater(f"Thread {job['thread_id']} has a job in progress.")

    session_id = str(uuid4())  # AgentCore requires 33-256 characters; one session per job.
    now = datetime.now(UTC).isoformat()
    # Conditional claim: concurrent deliveries cannot both dispatch the job.
    if not database.update_records(
        "jobs",
        {"status": "dispatched", "runtime_session_id": session_id, "updated_at": now},
        id=f"eq.{job_id}",
        status="eq.queued",
    ):
        return

    try:
        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=json.dumps({"job_id": job_id}).encode(),
        )
        ack = json.loads(response["response"].read())
        if ack.get("status") != "accepted":
            raise RuntimeError(f"Runtime did not accept job: {ack}")
    except Exception as exc:
        # Release the claim only if the runtime has not started it, so SQS can retry safely.
        database.update_records(
            "jobs",
            {"status": "queued", "last_error": f"{type(exc).__name__}: {exc}"[:1000], "updated_at": now},
            id=f"eq.{job_id}",
            status="eq.dispatched",
        )
        raise


def handler(event: dict, _context) -> dict:
    """Lambda entry point for SQS batches; returns partial batch failures."""
    if not AGENT_RUNTIME_ARN:
        raise RuntimeError("AGENT_RUNTIME_ARN is not configured.")
    failures = []
    for record in event["Records"]:
        if failures:
            # FIFO: once a message fails, later ones must also be retried to keep their order.
            failures.append({"itemIdentifier": record["messageId"]})
            continue
        try:
            job_id = str(UUID(json.loads(record["body"])["job_id"]))
        except (ValueError, KeyError, TypeError, AttributeError):
            print(f"Dropping malformed message {record['messageId']}")
            continue
        try:
            dispatch(job_id)
        except Exception as exc:
            print(f"Job {job_id} not dispatched: {type(exc).__name__}: {exc}")
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
