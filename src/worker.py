"""Background worker process handling SQS message ingestion.

SQS-triggered Lambda: each message carries only a job ID. The worker loads the durable job and runs
it on the supervisor (runtime.run_job). A job must finish within the Lambda timeout (15 minutes at
most); it ends at the first staff-review pause, and the staff decision resumes it as a new job.
Requires a FIFO queue with the event source mapping's ReportBatchItemFailures enabled, so only
failed messages are retried.
"""

import json
from uuid import UUID

from . import database
from .runtime import run_job


class RetryLater(Exception):
    """The job cannot run yet; SQS should redeliver the message."""


def process(job_id: str) -> None:
    """Run one queued job. Returns quietly when there is nothing to do."""
    jobs = database.query_records("jobs", id=f"eq.{job_id}", limit=1)
    if not jobs or jobs[0]["status"] != "queued":
        return  # Unknown, or already running/finished: a duplicate delivery.
    job = jobs[0]

    # One job per conversation thread at a time; FIFO keeps the rest of the thread waiting.
    if database.query_records(
        "jobs", thread_id=f"eq.{job['thread_id']}", status="eq.running", id=f"neq.{job_id}", limit=1
    ):
        raise RetryLater(f"Thread {job['thread_id']} has a job in progress.")

    run_job(job_id)  # Claims the job itself; a failed run is recorded as "failed", never re-run.


def handler(event: dict, _context) -> dict:
    """Lambda entry point for SQS batches; returns partial batch failures."""
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
            process(job_id)
        except Exception as exc:
            print(f"Job {job_id} not run: {type(exc).__name__}: {exc}")
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
