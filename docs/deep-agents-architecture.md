# Deep Agents Architecture and Deployment Plan

This plan records the decisions for the wedding inquiry and retention course described in `scenario-job-description.md`. Use Python Deep Agents, HubSpot Free, a shared Gmail inbox, and AWS. Host the frontend on AWS Amplify Hosting at its AWS-provided default HTTPS domain (no custom domain or CloudFront; the frontend lives in this repository's `frontend/` folder and is deployed by the same scripts and GitHub Actions workflows as the backend), the web API on Lambda with API Gateway, and agent execution on Amazon Bedrock AgentCore Runtime. LangSmith is selected for tracing and evaluation, not deployment hosting.

The application framework is **Deep Agents**: build agents with `create_deep_agent` and fixed subagents, not custom `StateGraph` nodes or edges. Deep Agents uses LangChain and LangGraph internally; PostgreSQL checkpoint/store adapters and approval-resume helpers are supporting internals, not a separate LangGraph application or course track.

The `src` directory now has working implementations of the models, prompts, schemas, middleware, database, RAG, agent definitions, all agent tools, `security/`, `api.py`, `worker.py`, `runtime.py`, and `cron.py`, including the staff, review, and document upload, publish, and rollback endpoints. The `frontend/` folder holds a static site (plain HTML, CSS, and JavaScript): the public pages, the contact form, staff login, a staff portal with the pending-review inbox (Approve, Approve with edits, Reject), knowledge documents (upload, publish, roll back), and staff management, and a change-password page, all connected to the API. The Terraform, Docker, deployment-script, and GitHub Actions files exist as empty placeholders, and no cloud resources are deployed. The earlier real-estate code is not part of this project. AWS replaces the earlier DigitalOcean deployment plan.

## 1. Agent Code Structure

Keep the supervisor and all subagent definitions in **one file: `agents.py`**. Keep model setup separate, use one tools file per agent, and keep cron logic in its own file. Three earlier planned modules are merged rather than kept as separate files: workflow coordination lives in `api.py`, Deep Agents persistence and backend setup lives in `agents.py`, and authorized document lifecycle handling lives in `rag.py`.

```text
src/
├── __init__.py
├── agents.py
├── models.py
├── config.py
├── schemas.py
├── prompts.py
├── database.py
├── rag.py
├── middleware.py
├── cron.py
├── api.py
├── worker.py
├── runtime.py
├── security/
│   ├── __init__.py
│   ├── auth.py
│   └── permissions.py
└── tools/
    ├── __init__.py
    ├── supervisor_tools.py
    ├── inquiry_tools.py
    ├── knowledge_tools.py
    └── retention_tools.py
```

| File | Responsibility |
|---|---|
| `agents.py` | Build the supervisor and register all three subagents, their tools, middleware, and approval settings; also house the built-in Deep Agents checkpointer, store, and backend setup helpers used by `runtime.py` at startup. |
| `models.py` | Define the `model1`–`model4` chat models (`ChatOpenRouter`, streaming, reasoning, provider routing) shared by the supervisor and subagents. |
| `config.py` | Thin `.env` accessor retained for now; each module reads its own environment settings directly, so this file is being phased out. |
| `schemas.py` | Typed, server-verified run context passed through `context_schema`, inquiry inputs, draft outputs, and review decisions. |
| `prompts.py` | All agent instructions, tone rules, and source-grounding requirements. |
| `database.py` | Application records in the InsForge-managed PostgreSQL database: staff membership, messages, drafts, approvals, jobs, delivery status, outreach eligibility, document metadata, and audit records. |
| `rag.py` | Document ingestion, indexing, retrieval, and source metadata, plus authorized document upload, versioning, publish, and rollback. These remain distinct functions, and the authorized paths still require role and company checks. |
| `middleware.py` | Safety checks, conditional review policy, model/tool limits, and bounded retries. |
| `cron.py` | Daily retention selection and scheduled maintenance, invoked by EventBridge Scheduler through Lambda; enqueue work and exit. |
| `api.py` | Thin FastAPI web API on Lambda, adapted through a Lambda-compatible ASGI handler. Receive public forms; serve authenticated staff review and document-management requests; coordinate request, record, and job handling previously planned in `workflow.py`, including conversation-thread mapping and recording approved decisions. |
| `worker.py` | SQS-triggered Lambda dispatcher: load durable jobs and invoke AgentCore without waiting for the entire agent run. |
| `runtime.py` | AgentCore entrypoint and tracked background execution; call the agent factory and persistence setup helpers in `agents.py`, manage resource connections at startup and shutdown, and do not redefine agents. |
| `security/__init__.py` | Package marker for staff authentication and authorization modules. |
| `security/auth.py` | Confirm staff identity by calling `GET /api/auth/sessions/current` on the fixed, trusted InsForge backend URL with the presented token, resolve the active staff membership, company, and roles in our application database, and return 401 for absent or invalid tokens; auth-service failures fail closed. |
| `security/permissions.py` | Role-to-action rules for `email_approver` and `knowledge_manager`, company-scope checks, and 403 decisions for wrong roles. |
| `tools/` | Thin agent-facing wrappers; reuse shared integration helpers instead of duplicating Gmail or HubSpot logic. |

Merging those modules does not change the execution boundary: HTTP handlers validate, persist, enqueue or dispatch resume jobs, and return promptly; only the AgentCore runtime performs long agent execution, resumption, and guarded delivery. Lambda API handlers must not make synchronous long agent calls, and every approval, dedupe, and delivery safeguard remains. No extra module is needed for this coordination.

Staff access uses managed InsForge accounts. Trusted administrators provision individual staff accounts, and the portal exposes no staff self-signup; regardless of any provider signup setting, a new login cannot gain staff rights without an administrator-granted active membership. Two independent roles control access:

| Role | Allowed actions | Not allowed by this role alone |
|---|---|---|
| `email_approver` | View inquiries, drafts, and source citations; Approve & Send, Edit, and Reject email drafts. | Uploading, replacing, publishing, or rolling back company knowledge documents. |
| `knowledge_manager` | Upload, replace, publish, and roll back company knowledge documents; view document versions and ingestion status. | Approving, editing, or rejecting customer emails. |
| `staff_admin` | List staff; add staff by name, email, starting password, and roles (the API creates the InsForge login with the password the admin chose, and the admin shares the login details with the member); deactivate, reactivate, or change roles. Cannot deactivate themselves or remove their own `staff_admin` role. | Reviewing emails or managing documents. |

Document uploads (`POST /documents`) are checked (type, real PDF bytes or UTF-8 text, at most 4 MB because API Gateway hands the file to Lambda base64-encoded within Lambda's 6 MB request limit), then the original is stored privately by `src/storage.py`: in the versioned S3 knowledge bucket named by `KNOWLEDGE_BUCKET` when deployed, under `.data/uploads` locally. The version is recorded as `processing` and an `index` job is queued; the runtime downloads the original, indexes it into `knowledge_chunks`, and marks it `ready`, or `failed` with an `error` message (its partial chunks removed, so the version can be uploaded again). Publish or rollback switches the live version in the `documents` table in one SQL statement, and search only returns chunks of the published version.

A department head can hold both roles, but assigning one never implies the other. Ordinary customers use the public inquiry form without login or email the shared inbox. Gmail integration uses OAuth and is polled on a schedule (no public webhook), and workers use IAM roles. Staff API calls send `Authorization: Bearer <access_token>`. The API confirms identity by calling `GET /api/auth/sessions/current` with that token against the fixed, trusted InsForge backend URL; it does not merely decode a JWT, does not trust browser-provided roles or editable profile fields, and never uses an anonymous key as staff login. It then resolves the active staff membership, company, and roles from our own application database on the server. It never trusts user, role, or company IDs from request payloads or UI visibility. Missing or invalid tokens return 401; a valid identity lacking access or the required role returns 403; InsForge auth-service failures fail closed rather than granting access. Deactivated staff are denied by the membership check even when their token has not yet expired. No passwords, provider admin keys, or shared credentials are stored in the application database, repository, frontend bundle, or logs. Each verified InsForge user ID maps to one company-scoped staff record with roles and active status. Audit records store the actor identity and the document or draft version.

Staff sign in through the API (`POST /auth/login`), which exchanges the email and password with InsForge and returns the InsForge access token; the browser keeps it for the session and never talks to InsForge directly. `GET /me` returns the signed-in member's name and roles so the portal shows only the sections those roles allow; the server still checks every action. Access tokens last about 15 minutes, after which staff sign in again. Any staff member can change or reset their password with InsForge's code method: `POST /auth/password/code` asks InsForge to email a 6-digit code (the reply never reveals whether the email has a login), and `POST /auth/password/reset` exchanges the code for a one-time reset token and sets the new password.

All modules above are implemented; the document-management endpoints in `api.py` and `rag.py` are still to be added. These filenames are an organization choice, not mandated by Deep Agents; a module that grows too large can be split later.

The customer inquiry form and the protected staff portal live outside this Python package, in the repository's `frontend/` folder. Dependency files, the lockfile, tests, and deployment configuration live at project level; see [Repository Layout for Deployment](#repository-layout-for-deployment).

## 2. Supervisor and Subagents

| Name | Job | Access |
|---|---|---|
| `supervisor` | Coordinate retrieval and drafting, then request delivery through the guarded tool. | Approved workflow tools; only this agent receives the delivery tool. |
| `inquiry_agent` | Understand the inquiry, use supplied evidence and conversation history, and prepare a reply. | Customer-history reads and draft creation; no direct email sending. |
| `knowledge_retriever` | Retrieve relevant, approved company documents and return a compact result with source IDs. | Read-only retrieval tools; no customer updates or sending. |
| `retention_agent` | Write anniversary and referral drafts for customers already selected by the daily job. | Approved customer details and draft creation; no eligibility overrides or sending. |

Use fixed, synchronous subagents registered through `subagents=`. Set an explicit `tools` allowlist on every custom subagent: inquiry history/drafting tools for `inquiry_agent`, read-only retrieval for `knowledge_retriever`, and retention history/drafting tools for `retention_agent`. Omitting `tools` can inherit the supervisor's tools; an explicit list replaces the inherited custom tools, and `tools=[]` allows none of those custom tools. Never include the delivery tool in any subagent list.

Disable the unused default general-purpose subagent through `GeneralPurposeSubagentProfile(enabled=False)` on the active harness profile; it must not become another route to inherited delivery tools. Keep the three named subagents enabled. Custom tool allowlists do not remove built-in filesystem tools: separately restrict those through the supported `FilesystemMiddleware` tool allowlist and filesystem permissions, and do not expose shell execution. Test the effective tool registrations, not just the prompt instructions.

Subagents do not automatically remember previous delegations. Pass the relevant history and evidence into each task. Do not run all three for every request: inquiry work uses retrieval and inquiry drafting; retention work uses retention drafting and retrieval only when needed.

A separate retrieval subagent is useful for larger document searches, but is not mandatory for a small FAQ. If its overhead is not justified, use the same retrieval tool directly without changing the safety policy.

## 3. Deep Agents Features We Need

- **`create_deep_agent` and custom tools:** connect the model to our knowledge base, customer records, and controlled actions.
- **Custom subagents:** isolate document search and drafting context.
- **RAG/retrieval:** look up approved company facts instead of relying on the model's general knowledge.
- **Human-in-the-loop:** use `interrupt_on` and a persistent checkpointer to pause sensitive delivery operations.
- **Runtime context and structured output:** carry server-verified company/contact IDs and return validated draft data.
- **Middleware:** bounded model/tool calls, transient-error retries, and policy checks at the action boundary.
- **Backends and memory:** thread-scoped working files plus carefully scoped persistent memory.
- **Built-in context management:** keep tool results compact and allow the harness to manage growing histories.
- **Filesystem permissions:** make approved policies read-only when exposed through built-in filesystem tools. These permissions do not protect custom Gmail, HubSpot, or database tools; those need their own authorization.

Not needed initially: multimodal inputs, voice calls, browser scraping, dynamic subagents, remote asynchronous subagents, shell execution, sandbox backends, or runtime rubric-grading loops. Rubric grading can be considered later; it is not a substitute for enforced approval rules. Skills and explicit task planning are optional, not requirements for these short workflows.

AgentCore's tracked background execution is hosting infrastructure. It does not require Deep Agents' remote async-subagent feature. AgentCore can host the chosen Deep Agents harness, so we can keep Deep Agents without switching to Strands.

## 4. Models and Dependencies

Chat-model candidates are `mistralai/mistral-large-4-0`, `anthropic/claude-sonnet-5.5`, `xiaomi/mimo-v2.6-pro`, and `google/gemini-4-argon`. These are user-supplied IDs, not verified availability claims. Confirm provider access, tool calling, structured output, and cost before smoke tests; compare the supported candidates through LangSmith evaluation before selecting the final chat model. Within each experiment, keep one configured chat model shared by the supervisor and subagents, without changing their tools or safety policy.

The selected embedding model is `google/gemini-embedding-2` through OpenRouter ([model page](https://openrouter.ai/google/gemini-embedding-2)), and the selected vector store is **pgvector in the InsForge Postgres database** (table `knowledge_chunks`, `vector(768)`), replacing the earlier local ChromaDB files, which the separate Lambda and AgentCore machines could not share. Search joins the `documents` table, so only chunks of a company's `published` version are returned and publish/rollback is a single atomic update. Use the same embedding model and dimension settings for indexing and query search, and rebuild the index if they change. The embedding model is not a subagent.

Configure reasonable output limits, request timeouts, and low-temperature drafting where supported. Keep model IDs configurable. AgentCore does not require the model itself to be hosted on Bedrock; external model providers remain an option. Use Terraform for the dev, test, and prod infrastructure definitions.

Planned dependencies include Deep Agents, the chosen model-provider adapter and tool integrations, PostgreSQL persistence adapters (`langgraph-checkpoint-postgres` and `psycopg` with pool support), Pydantic, FastAPI with a Lambda-compatible ASGI adapter, the Bedrock AgentCore SDK, `boto3`, the Gmail/HubSpot integration libraries we choose, and an HTTP client used to verify staff sessions through the InsForge REST endpoint. Deep Agents depends internally on LangChain and LangGraph; the lockfile pins those compatible versions, but we do not write our own LangGraph graphs. Pin compatible, vetted versions in the project lockfile. Conditional `interrupt_on` predicates currently require `langchain>=1.3.3`; confirm the installed version supports the selected APIs.

## 5. RAG and Company Knowledge

Index approved FAQs, service descriptions, venue policies, and current price sheets. Extract text, split documents into useful sections, and retain source IDs, venue names, versions, and effective dates.

Retrieve only relevant, current sections and supply them to the drafting agent. Include source references in the internal review view, not a dump of the entire knowledge base in every prompt. Conflicting or missing evidence triggers staff handoff.

Price information may support an internal draft, but **every pricing inquiry requires human review**. Updating documents must update the index and trigger regression tests before new material is trusted for automatic replies. Customer messages and retrieved text must not override system policy.

### Staff Upload and Policy Updates

Only staff with `knowledge_manager` permission can upload, replace, publish, or roll back company knowledge files from the protected frontend. Email approval permission alone does not allow knowledge changes. Keep originals in a separate private, versioned S3 knowledge bucket, never alongside the public frontend assets or at a public URL.

1. The authenticated API checks the staff role and company scope, validates the requested file type and configured size limit, creates a document version record, and issues a short-lived upload URL scoped to that exact object. Support PDF, TXT, and Markdown initially; reject unsupported or unsafe files.
2. On upload completion, the backend verifies the actual object, size, and content type, scans the untrusted file before bounded text extraction, and queues a durable indexing job. Return an ingestion job ID so the frontend can show uploaded, processing, ready, failed, and published states. Retry with the same document/version identity; duplicate completion events must not create duplicate versions or indexes.
3. Extract text, split, embed, and index into a staged document version. Persist company, document/source ID, venue, version, effective date, content hash, and uploader identity. Retrieval remains on the previous published version while ingestion is running. Document contents are reference data, not agent instructions; uploading a file cannot change the sending guardrails.
4. Run retrieval and safety regression checks before the knowledge manager explicitly publishes a ready version. Publish by atomically changing the active version in the application metadata; retrieval must filter by the authoritative active version, company, venue, and effective date. A failed ingestion or failed check must leave the previous version active. Retain old objects and metadata for audit and rollback, but exclude superseded versions from live retrieval. Rolling back also uses the checked publication path.
5. Record who uploaded, published, replaced, or rolled back each version and when. Pending unsent drafts citing superseded versions become stale and require regeneration or review against current sources before sending; the delivery code checks source-version validity. The frontend shows the active version and ingestion errors without exposing secrets or raw internal logs.

## 6. Memory Without LangSmith Hosting

We use Deep Agents' existing persistence and memory features, not a new custom memory engine. The checkpointer, store, and backend setup helpers live in `agents.py`, and `runtime.py` manages the resource connections through its startup and shutdown lifecycle. Use persistent PostgreSQL, not runtime-local files, for deployed state. Amazon RDS for PostgreSQL is the recommended AWS database; choose and validate its size, connection limits, networking, and cost during implementation.

| Data | Mechanism | Purpose |
|---|---|---|
| Conversation and paused agent state | `PostgresSaver`, or `AsyncPostgresSaver` for async execution | Continue email threads and resume approvals after restarts. |
| Optional approved facts across threads | `PostgresStore`, or `AsyncPostgresStore` | Scoped long-term memory. |
| Working files within a conversation | `StateBackend` | Keep intermediate context tied to the persisted thread. |
| Persistent virtual memory files | `StoreBackend` routed through `CompositeBackend` | Expose approved cross-thread memory through `/memories/`. |
| Business and audit records | Application database tables | Store drafts, review decisions, consent, jobs, and delivery/outreach history. |

Provide `checkpointer=` and `store=` explicitly when creating the agent inside AgentCore. Initialize their database schemas before use and manage connections through application startup/shutdown. AgentCore hosting and its runtime session ID do not automatically provide Deep Agents persisted checkpoints or approval records. AgentCore Memory is a separate optional service, not our selected checkpoint backend.

Scope memory by server-verified company and contact IDs. Define their typed shape in `schemas.py`, pass it as `context_schema=` to `create_deep_agent`, and supply the resolved values through `context=` on every `invoke` or `ainvoke`, including approval resumes. The API and worker resolve company, contact, mailbox, and conversation ownership from trusted configuration and database records; staff actor identity and permissions remain separate from the customer's memory identity. Do not accept memory-owner IDs from model tool arguments or unverified frontend fields.

Inside tools and the store namespace callback, read those IDs from the injected runtime context (`runtime.context` or `rt.context`) and namespace customer memory by company and contact, with a stable application prefix. Do not assume AgentCore supplies the LangSmith/Agent Server identity fields used by documentation examples such as `rt.server_info.user.identity`; our AgentCore adapter supplies the context explicitly. Reconstruct and revalidate it for every new invocation and resume rather than assuming it is saved with checkpoints. Missing identity must fail safely, never fall back to a shared customer-memory bucket. Keep permissions, consent, pricing approval, and delivery status in structured application records, not model-written memory files.

**Retention:** conversation data older than **120 days** is deleted by the daily maintenance job: inbound email content (job payloads), finished jobs, staff review rows, and the agent's saved conversation state (checkpoints) for threads with no activity in that period. `email_drafts` and `audit_logs` are kept as the business record of what was sent and who approved it.

Map each email conversation to a stable Deep Agents conversation `thread_id`, scoped to its company and mailbox. Bind website inquiries to the Gmail thread created by the first outgoing email. Reuse that mapping for customer replies and approval resumption. Keep a separate mapping to AgentCore's `runtimeSessionId`; do not assume a live runtime session lasts as long as the conversation. Cross-thread contact facts belong in the store, not in an unrelated conversation's checkpoint.

AgentCore microVM sessions have a default idle timeout of 15 minutes and a maximum lifetime of up to 8 hours. Staff may approve a message the next day. Persist the interrupt, let processing stop, and restore the same persisted Deep Agents conversation from PostgreSQL in a later runtime invocation rather than keeping compute alive waiting for staff.

Use a backend configuration supported by the pinned Deep Agents version and supply its runtime context correctly. In-memory storage is acceptable for local experiments but loses data on restart. Runtime filesystem persistence, if configured, is not a substitute for our shared database and application audit records.

## 7. Inquiry and Reply Flow

```text
Website form or shared Gmail inbox
    -> API Gateway and Lambda validate and durably record the inquiry
    -> A durable job is queued in SQS
    -> Lambda dispatcher invokes AgentCore and receives a job acknowledgement
    -> AgentCore runs the workflow in a tracked background task
    -> Integration creates or updates a HubSpot Contact
    -> Supervisor obtains relevant knowledge and a draft
    -> Software checks the inquiry and proposed reply
        -> Routine, source-backed answer with AUTO_SEND_ROUTINE on: send automatically
        -> Restricted topic: persist a review interrupt and notify staff
        -> Unsupported or unsafe request: hand off to staff
    -> Record delivery and sync the outcome to HubSpot
```

Connect Gmail using OAuth with limited permissions (`gmail.readonly` to read the inbox and `gmail.send` to reply). Ingest email by **polling**, not Pub/Sub push: an EventBridge Scheduler schedule runs every 8 minutes (`rate(8 minutes)`, input `{"task": "poll_gmail"}`) and invokes the `cron.py` Lambda. Nobody expects a reply within seconds, and polling avoids a public webhook, Pub/Sub, and the 7-day watch renewal. Each poll reads `users.history.list` from the stored history position (`sync_state` table), fetches new inbox messages, skips our own, automated, and bulk mail, and queues one `inquiry` job per message with dedupe key `gmail:{messageId}`. It advances the stored position only after every message is queued. If Gmail returns 404 because the position expired, it falls back to a full sync of the last 7 days; dedupe keys prevent reprocessing. Each Gmail thread maps to agent thread `gmail:{threadId}`, and replies are sent into that thread with `threadId`, `In-Reply-To`/`References`, and a matching `Re:` subject.

Each new customer reply triggers the same policy using the existing conversation history. Ignore our own outgoing messages, spam, bounces, and automatic responses such as out-of-office emails to avoid loops. Queue work per thread; do not run overlapping conversations against the same paused state. New replies must invalidate stale drafts when appropriate.

## 8. Human Approval and Guardrails

Automatic replies are limited to tested routine topics such as services, hours, capacity, and approved FAQs. Require staff review for pricing, discounts, booking/date commitments, contracts, cancellations, refunds, complaints, and mixed messages containing any restricted topic. All retention messages also require review.

Inquiry replies have **one review point, at sending**. Saving an inquiry draft does not pause; retention drafts still pause both when saved and when sent. A reply is sent without review only when **all** of these hold, and every check is code, not the model's judgment:

1. The `AUTO_SEND_ROUTINE` setting is `true`. It defaults to `false`, so every reply is reviewed until the routine topics pass evaluation; turning it on needs no code change.
2. The job is an inquiry, never retention.
3. The stored draft cites at least one approved source and is not flagged `requires_review` (no evidence gaps).
4. Neither the customer's message nor the outgoing subject and body contains a restricted topic or a specific date. Retrieved knowledge passages are not scanned, because they mention prices for many routine questions.

Anything else, including a failed draft lookup, pauses for staff approve, edit, or reject.

Use a conditional `interrupt_on` gate on the supervisor's guarded delivery tool, with a `when` predicate in its `InterruptOnConfig` and `allowed_decisions` limited to approve, edit, and reject. The predicate must use trusted policy results and stored draft data, not an LLM-provided `requires_approval` flag. The sending code independently enforces that policy even if the agent tries another route. If policy checks are unavailable, do not send.

Staff review happens in the authenticated staff portal. HubSpot login alone does not authorize access to this app. Reviewers see the original inquiry, proposed reply, sources, review reason, and draft version, with a pending-review list. Only authenticated `email_approver` staff can view or act on reviews, and every API call is checked on the server. The card does not expose AWS, email, or database credentials.

Support **Approve & Send**, **Edit**, and **Reject**. Approval is bound to the exact draft version, recipient, and conversation, and records the authenticated staff actor. Editing invalidates the old approval and requires explicit approval of the revised content. After the decision is recorded, resume the paused Deep Agents run inside AgentCore on the same persisted conversation thread, using the framework-supported resume helper. In our code this is `Command(resume=...)`, an internal supporting helper from the LangGraph layer that Deep Agents uses; it is not a deepagents export. A rejected draft must remain blocked, even if the model proposes sending it again.

Use finite model/tool budgets and bounded retries for idempotent reads. Do not blindly retry an email send after a timeout: it may already have succeeded. Track delivery attempts, enforce unique processing keys, and reconcile uncertain outcomes before another send.

## 9. Daily Retention Cron Job

Use **Amazon EventBridge Scheduler** to invoke a small Lambda function containing the daily selection and maintenance logic in `cron.py`. This replaces the earlier managed cron container and avoids embedding APScheduler in API or AgentCore processes.

EventBridge Scheduler uses AWS schedule expressions rather than ordinary five-field Unix cron. For example, `cron(0 9 * * ? *)` runs daily at 09:00. Set the scheduler's IANA timezone explicitly, such as `America/Chicago` for the Nashville venue, so daylight-saving changes are handled correctly. Disable flexible delivery windows if the job should run close to the scheduled minute.

The scheduled Lambda queries structured records, applies eligibility/suppression rules, queues selected customers, and exits. The dispatcher invokes AgentCore, which runs `retention_agent` to write drafts. The daily maintenance also re-queues jobs that were dispatched but never claimed, fails runs that outlived the 8-hour AgentCore session (never retried automatically, since they may already have sent email), and applies the 120-day conversation retention rule. Configure retry limits, failure alerts, and a dead-letter queue; make retries safe through unique job keys and transactional claims.

The daily job selects two occasions from `couples.wedding_date`:
+ **Anniversary:** the wedding anniversary falls within the next 7 days. One per couple per year.
+ **Referral:** about 3 months after the wedding (the wedding date was 90 days ago or slightly more, within a short catch-up window for missed runs). Sent once per couple, ever: a warm, optional request to recommend Juniper Hollow.

Use unique job keys (`retention:{contact_id}:anniversary:{year}` and `retention:{contact_id}:referral`) so repeated runs never create duplicate work.

Track `last_outreach_at`, `next_eligible_outreach_at`, occasion/reason, and queued or pending drafts. Use the discussed **90-day cooldown** as a configurable course default. Update the last-sent timestamp after successful delivery, not just after a check or draft creation.

Skip customers without marketing consent, do-not-contact or relationship-change flags, open complaints, bounced addresses, recent outreach, or existing work for the same occasion. Use unique job/occasion keys and transactional claims to stop overlapping scheduled runs from generating duplicate drafts. Recheck eligibility before sending approved outreach.

Checking eligibility uses ordinary database queries, not an LLM, so ineligible customers consume no AI tokens. Do not skip a customer for 90 days merely because they were checked; a valid anniversary may become due tomorrow.

An optional alternative to EventBridge Scheduler is [InsForge scheduling](https://docs.insforge.dev/core-concepts/functions/schedules): standard five-field cron where pg_cron issues HTTP requests to a configured target URL and headers, with a minimum interval of one minute. Failed runs are logged but not automatically retried. If adopted, a schedule would call only a protected, short job-dispatch API route — never run the long Deep Agents work inside the timer — secured with a dedicated trusted machine credential rather than public signup or anonymous credentials. The dedupe, retry/reconciliation, and alerting rules above still apply, and timezone requirements must be verified before switching. The current plan remains EventBridge Scheduler; no schedules are created by this document.

## 10. AWS Deployment

### Recommended Components

| Component | Deployment |
|---|---|
| Frontend: customer inquiry form and protected staff portal | Static build hosted on AWS Amplify Hosting at its AWS-provided default HTTPS domain (`*.amplifyapp.com`; no custom domain, no CloudFront). S3 website endpoints are HTTP-only, so they are not used. Built from `frontend/` and uploaded by the deploy script, one Amplify app per environment. Hosts the public inquiry form and the login-protected staff portal. Add that domain to InsForge `allowedRedirectUrls` and the API's CORS origins once known. |
| Staff identity | InsForge-managed staff accounts, individually provisioned by trusted administrators with no staff self-signup in the portal. The API confirms tokens through InsForge's REST session endpoint; application records map each verified InsForge user ID to company, roles, and active status. |
| Document originals | Separate private, versioned S3 knowledge bucket with public access blocked; never alongside public frontend assets or at a public URL. |
| Web API | API Gateway HTTP API with one catch-all route to a Lambda running the thin FastAPI application through Mangum; CORS restricted to the Amplify domain. |
| Durable dispatch | SQS FIFO jobs queue with a dead-letter queue, plus a Lambda dispatcher triggered by the queue with partial batch failure reporting; persist job status and recover incomplete work. |
| Deep Agents execution | Amazon Bedrock AgentCore Runtime, with the supervisor and subagents defined in `agents.py`, from an ARM64 container image in ECR. |
| Lambda packaging | One container image in ECR shared by the `api`, `worker`, and `cron` Lambdas, each with its own handler command; avoids the 250 MB zip limit. |
| Gmail polling and daily retention/maintenance | Two EventBridge Scheduler schedules invoke `cron.py`: `rate(8 minutes)` for Gmail polling and `cron(0 9 * * ? *)` in `America/Chicago` for retention and maintenance, with a standard-queue dead-letter queue. |
| Secrets | AWS Secrets Manager for the HubSpot, Gmail OAuth, InsForge, and OpenRouter credentials; never in `.tfvars`, the repository, or Lambda configuration in plain text. |
| Memory and application data | PostgreSQL with persistent checkpoints, stores, drafts, and audit records. RDS for PostgreSQL is the default; InsForge's own Postgres is being evaluated as a cheaper alternative, because RDS needs a VPC and a NAT gateway that cost money while idle. |
| Retrieval index | pgvector in the InsForge Postgres database (`knowledge_chunks`), shared by the API, indexing, and AgentCore with no extra service to host. |

Both the frontend and backend must be hosted somewhere. Giving the frontend an API URL connects it to an already hosted backend; it does not deploy that backend. This plan hosts both on AWS, with separate responsibilities rather than one frontend/backend container.

### Development Bootstrap Before Cloud Deployment

Phase 1 in `project-tasks.md` selects the AWS region and initializes Terraform, sets budget alerts, and defines separate dev, test, and prod configuration before creating cloud resources. Bootstrap a development InsForge project for staff authentication, plus a separate private, versioned, encrypted S3 knowledge bucket and an ingestion SQS queue with a dead-letter queue. Use test data only. These are prerequisites for staff-authentication testing in Phase 3 and upload/indexing testing in Phase 4, not a full AgentCore deployment.

Run PostgreSQL and the application locally for early phases. A local queue consumer exercises the development ingestion queue, file checks, and indexing without requiring the agent runtime or deployed Lambda workers. Create individual InsForge test accounts and their application memberships in Phase 3; exercise the minimal document and review APIs locally before the frontend exists. Phase 12 extends the same infrastructure definitions for production roles, queues, workers, RDS, API Gateway/Lambda, and AgentCore. Phase 13 tests the deployed frontend's login, logout, session refresh, allowed origins, and any required email-verification or OAuth redirects. No cloud resources are provisioned by writing this plan.

Other planning documents, including `docs/project-tasks.md` and `docs/scenario-job-description.md`, are not updated by this task. Align their relevant module and authentication references with this architecture before implementation.

### Why Lambda and AgentCore Are Used Together

Lambda handles short work: web requests (forms, staff approvals, job-status reads) and scheduled tasks (Gmail polling, daily retention and maintenance). Standard Lambda execution is limited to 15 minutes; API Gateway HTTP APIs have a 30-second integration timeout. The API durably accepts work and returns a job ID promptly, not after the entire agent workflow completes. Other API Gateway types have different timeout rules, so the 30-second limit here applies specifically to the selected HTTP API.

AgentCore hosts the agent workflow, including retrieval, tool calls, and tracked background work. MicroVM sessions can run for up to 8 hours. AgentCore is not a drop-in host for every arbitrary FastAPI route: adapt the agent to its runtime interface, using `/invocations` and `/ping` or the AgentCore SDK. Lambda-only hosting can work for short, bounded agent jobs, but AgentCore is the selected runtime for this course.

The SQS dispatcher must not wait for the final agent response. Configure AgentCore to acknowledge accepted work and track background tasks using its SDK task lifecycle or equivalent health reporting. Persist job state, recover abandoned work, and avoid concurrent processing of the same thread. Do not assume a background task is durable merely because it is running in a session.

### Terraform Support

Terraform remains the selected infrastructure-as-code tool for AWS resources. The official `hashicorp/aws` provider supports AgentCore through [`aws_bedrockagentcore_agent_runtime`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/bedrockagentcore_agent_runtime) and [`aws_bedrockagentcore_agent_runtime_endpoint`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/bedrockagentcore_agent_runtime_endpoint). The runtime resource references a separately provisioned IAM execution role and configures the container artifact URI from ECR, networking, and environment settings. Terraform can also provision the IAM role and ECR repository through their own AWS resources. We keep the existing container deployment plan; building and pushing the ARM64 image to ECR is a separate build step, not a Terraform resource. Pin an appropriate tested AWS provider version during infrastructure implementation; no minimum version has been verified here. Terraform is used only for AWS infrastructure; do not assume provider support for non-AWS services. No resources are applied by writing this plan.

All main configuration lives in one `terraform/` folder, split into one file per service. Environments follow the course pattern:

- **Workspaces:** `dev`, `test`, and `prod` are Terraform workspaces over the same configuration. Resource names use `name_prefix = "<project>-<environment>"`, and bucket names end in the AWS account ID so they are globally unique. Every resource is tagged with `Project`, `Environment`, and `ManagedBy = "terraform"`.
- **Variables:** `terraform.tfvars` holds dev/test values and `prod.tfvars` holds prod values. Both are committed because they contain no secrets.
- **State:** remote state in a private, versioned, encrypted S3 bucket, one state key per environment, using S3's built-in lock file (`use_lockfile = true`, Terraform 1.10 or newer) instead of a DynamoDB lock table, which current Terraform deprecates. `backend.tf` declares an empty `s3` backend; the deploy and destroy scripts pass the bucket, key, and region with `-backend-config`. A stale lock is cleared with `terraform force-unlock`.
- **Bootstrap:** `terraform/bootstrap/` is a separate, one-time configuration with its own local state. It creates the state bucket and the GitHub Actions OIDC provider and deploy role. Unlike the course, which deletes its setup files after applying them, we keep this folder so the setup stays reproducible.
- **Lock file:** commit `.terraform.lock.hcl` so every machine and CI run uses the same provider versions.
- **IAM:** roles use AWS-managed full-access policies for this course, matching the course material. The GitHub Actions deploy role needs them for Lambda, S3, API Gateway, IAM, ECR, SQS, EventBridge Scheduler, Bedrock AgentCore, Amplify, Secrets Manager, CloudWatch Logs, and RDS if used. Tightening to least privilege is a later hardening step.

### Repository Layout for Deployment

```text
.github/workflows/
├── deploy.yml           # push to main → dev; manual run → test or prod
└── destroy.yml          # manual run; type the environment name to confirm
scripts/
├── deploy.ps1 / deploy.sh
└── destroy.ps1 / destroy.sh
terraform/
├── bootstrap/           # one-time: state bucket + GitHub OIDC deploy role
│   ├── versions.tf
│   ├── variables.tf
│   ├── main.tf
│   └── outputs.tf
├── versions.tf          # Terraform and AWS provider versions
├── backend.tf           # empty S3 backend, configured by the scripts
├── variables.tf
├── terraform.tfvars     # dev/test values
├── prod.tfvars          # prod values
├── main.tf              # name_prefix, tags, account ID
├── ecr.tf               # Lambda image + ARM64 AgentCore image repositories
├── s3.tf                # private, versioned knowledge bucket
├── sqs.tf               # FIFO jobs queue + dead-letter queues
├── secrets.tf           # Secrets Manager entries (values set outside Terraform)
├── iam.tf               # Lambda, AgentCore, and Scheduler roles
├── lambda.tf            # api, worker, cron + SQS trigger
├── api_gateway.tf
├── agentcore.tf
├── scheduler.tf         # Gmail polling + daily retention schedules
├── database.tf          # PostgreSQL, once RDS vs InsForge Postgres is decided
├── amplify.tf           # frontend hosting
└── outputs.tf
Dockerfile               # api / worker / cron Lambda image
Dockerfile.agentcore     # ARM64 runtime.py image
.dockerignore
.env.example             # names of required variables, no values
frontend/                # customer inquiry form + staff portal
src/  tests/
```

### Deploy Scripts and CI/CD

The deploy script runs the same steps locally and in CI:

1. Build and push the Lambda image and the ARM64 AgentCore image to ECR (`docker buildx --platform linux/arm64` for AgentCore).
2. Run `terraform init` with the environment's backend settings, select or create the workspace, and `terraform apply` (with `prod.tfvars` for prod).
3. Read the API URL and Amplify app details from `terraform output`, write the API URL into the frontend's build environment, build `frontend/`, and upload the build to Amplify Hosting.

The destroy script empties the environment's buckets and ECR repositories and runs `terraform destroy`. It never touches the bootstrap state bucket or deploy role.

GitHub Actions authenticates to AWS with OIDC through `aws-actions/configure-aws-credentials`, so no long-lived AWS keys are stored in GitHub. The repository holds three secrets: `AWS_ROLE_ARN`, `DEFAULT_AWS_REGION`, and `AWS_ACCOUNT_ID`. `deploy.yml` deploys `dev` on every push to `main` and deploys `test` or `prod` from a manual run; each job uses a GitHub environment of the same name, so prod can require approval. `destroy.yml` runs only manually and requires typing the environment name to confirm. Since GitHub's July 2026 OIDC change, the token's `sub` claim includes immutable owner and repository IDs (`repo:<owner>@<id>/<repo>@<id>:environment:<env>`), so the deploy role's trust policy must match that format; inspect the real `sub` from a workflow run before writing it.

### Security, Networking, and Costs

The form calls our API (`POST /inquiries`) over HTTPS; it never receives AWS keys or model credentials. It sends name, email, and message, plus optional phone, preferred venue, guest count, and preferred date; the runtime adds the optional fields to the agent's task message. A preferred date that names a year or month counts as a date/availability question, so that reply always goes to staff review, even with `AUTO_SEND_ROUTINE` on. Validate submissions, rate-limit public endpoints, and restrict CORS to approved frontend origins. Staff endpoints require an InsForge access token sent as `Authorization: Bearer <access_token>`. The API confirms the session by calling `GET /api/auth/sessions/current` on the fixed, trusted InsForge backend URL, then enforces active membership, the required `email_approver` or `knowledge_manager` role, and company scope on the server. Missing or invalid tokens return 401 and wrong roles return 403, before any side effect; auth-service failures fail closed. Staff browsers hold no AWS keys or client secrets. Public form validation, rate limits, and CORS remain in place; Gmail is polled with OAuth, so there is no public Gmail webhook to verify; workers use IAM. IAM roles start with AWS-managed full-access policies for this course (see Terraform Support); narrow the dispatcher to AgentCore invocation only when hardening.

Use IAM execution roles and managed secrets instead of embedded credentials. For a container-based AgentCore deployment, build for the required ARM64 platform and port 8080 runtime contract. Configure database access and controlled outbound connectivity for Gmail, HubSpot, and the selected model provider. Keep API and agent versions compatible with the database schema and approval payloads.

AWS is pay-as-you-go, not a guarantee of free hosting. Budget separately for AgentCore compute, Lambda/API Gateway, InsForge, SQS, S3, frontend hosting, PostgreSQL, logs, networking, and model/embedding usage. Set budget alerts and validate region availability and quotas before deploying. Do not carry over DigitalOcean's earlier prices or static-site allowances.

## 11. HubSpot and LangSmith Plans

**HubSpot Free:** suitable for a course dataset of up to **2 users and 1,000 contacts**. Use Contacts instead of the separate Leads object. Keep drafts and job logic in our application instead of relying on paid workflows or custom objects.

If limits require an upgrade, Starter is the entry paid option. Its advertised normal price is **$20/month per seat**; current new-customer offers advertise **$7/month billed annually** or **$10/month billed monthly**. Check offer eligibility, term, and renewal pricing before buying.

**LangSmith Developer:** cloud tracing with **1 seat and 5,000 included base traces/month**, then usage charges. It does not include managed deployment. Plus is advertised at **$39/seat/month**, including one small serverless deployment, but we do not need that hosting plan for this design.

LangSmith tracing and evaluation are required for this project: set `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT`, and run the evaluation suite as a regression check. Redact sensitive customer data and credentials from traces before they leave the application. Free tracing does not make model calls, evaluation calls, or excess usage free. AWS CloudWatch and AgentCore observability are also available; configure what data each system receives.

Develop and test the agent locally with Python and Deep Agents. Deploy the open-source agent inside our AgentCore runtime. This is different from self-hosting the licensed LangSmith platform or production Agent Server; do not assume those products are free merely because they run in Docker. Local Python runs are for development; the production target is AgentCore.

## 12. Verification Before Automatic Sending

Test grounded FAQ answers, pricing and mixed-question routing, injection attempts, review/edit/reject behavior, restart-safe memory, company/contact isolation, customer follow-up replies, duplicate events, uncertain delivery outcomes, and retention exclusions/cooldowns. Also test API acknowledgement timing, SQS redelivery, scheduled-job retries, AgentCore session expiry, and resuming approvals in a new runtime session.

Test authentication and permissions: missing, invalid, and expired tokens; InsForge auth-service failures failing closed; inactive staff; each role separately; company isolation; and direct unauthorized approval, upload, or publish requests denied before any side effect. Test the upload lifecycle: unsupported or oversized files, short-lived object-scoped upload URLs, duplicate completion events, failed-index recovery, explicit publication and rollback, and stale-draft invalidation.

Inspect effective tool registrations to confirm no subagent receives delivery capabilities and the default general-purpose subagent is disabled. Test runtime-context propagation through tools, namespaces, subagent delegation, and approval resumes; verify company/contact isolation, fail-closed missing context, and revalidated context after a process or AgentCore session restart.

Start with every reply reviewed. Enable automatic sending only for the routine topics that pass the agreed evaluation set. Keep audit records and alert staff on failures. pgvector in InsForge Postgres, `google/gemini-embedding-2` through OpenRouter, Terraform, GitHub Actions, and Amplify Hosting are selected; final chat-model selection, provider compatibility, the PostgreSQL choice for checkpoints (RDS or InsForge Postgres) and sizing, runtime configuration, and networking still require validation. These are planned choices, not already deployed resources.

## Official References

### Deep Agents and Middleware

- [Customization](https://docs.langchain.com/oss/python/deepagents/customization)
- [Tools](https://docs.langchain.com/oss/python/deepagents/tools)
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Permissions](https://docs.langchain.com/oss/python/deepagents/permissions)
- [Memory](https://docs.langchain.com/oss/python/deepagents/memory)
- [Retrieval](https://docs.langchain.com/oss/python/deepagents/retrieval)
- [RAG](https://docs.langchain.com/oss/python/deepagents/rag)
- [Context engineering](https://docs.langchain.com/oss/python/deepagents/context-engineering)
- [Subagents](https://docs.langchain.com/oss/python/deepagents/subagents)
- [Dynamic subagents](https://docs.langchain.com/oss/python/deepagents/dynamic-subagents)
- [Async subagents](https://docs.langchain.com/oss/python/deepagents/async-subagents)
- [Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)
- [Multimodal](https://docs.langchain.com/oss/python/deepagents/multimodal)
- [Grading rubrics](https://docs.langchain.com/oss/python/deepagents/rubric)
- [Built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
- [Custom middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom)
- [PostgreSQL memory and checkpoints](https://docs.langchain.com/oss/python/langgraph/add-memory) (supporting persistence used by Deep Agents)

### AWS Deployment and Integrations

- [AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)
- [AgentCore HTTP contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html)
- [AgentCore background tasks](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html)
- [AgentCore session lifecycle](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html)
- [Lambda execution timeout](https://docs.aws.amazon.com/lambda/latest/dg/configuration-timeout.html)
- [API Gateway HTTP API limits](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-quotas.html)
- [EventBridge Scheduler expressions and timezones](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html)
- [Scheduled Lambda invocations](https://docs.aws.amazon.com/lambda/latest/dg/with-eventbridge-scheduler.html)
- [Terraform aws_bedrockagentcore_agent_runtime](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/bedrockagentcore_agent_runtime)
- [Terraform aws_bedrockagentcore_agent_runtime_endpoint](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/bedrockagentcore_agent_runtime_endpoint)
- [Terraform S3 backend and state locking](https://developer.hashicorp.com/terraform/language/backend/s3)
- [Terraform workspaces](https://developer.hashicorp.com/terraform/language/state/workspaces)
- [AWS Amplify Hosting](https://docs.aws.amazon.com/amplify/latest/userguide/welcome.html)
- [GitHub Actions OIDC with AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [configure-aws-credentials action](https://github.com/aws-actions/configure-aws-credentials)
- [InsForge schedules](https://docs.insforge.dev/core-concepts/functions/schedules)
- [InsForge documentation](https://docs.insforge.dev)
- [Gmail sync with users.history.list](https://developers.google.com/workspace/gmail/api/guides/sync)
- [Gmail threads](https://developers.google.com/workspace/gmail/api/guides/threads)
- [HubSpot Free limits](https://www.hubspot.com/pricing/crm)
- [HubSpot Starter offer](https://www.hubspot.com/products/crm/starter)
- [LangSmith pricing](https://www.langchain.com/pricing-langsmith)

Pricing and platform limits can change; confirm them in the linked documentation before deployment.
