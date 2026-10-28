# Juniper Hollow Agent: Build and Deploy Guide

Every step we took to build the Juniper Hollow agent, from an empty folder to the first AWS deploy.
It is written in order, so it can be followed as the script for the video. It describes what each
file does instead of showing its code.

It covers the build, the first AWS deploy (Part 14), the **LangSmith evaluation** of the agent
(Part 15), testing dev with real emails and fixing what that found (Parts 16 and 17), turning on
auto-send (Part 18), going to production (Part 19), running it day to day (Part 20), and tearing it
down (Part 21).

---

## Part 1. What we are building

Juniper Hollow Weddings & Events is a fictional venue with two spaces. The system:

- reads customer emails from a shared Gmail inbox, and inquiries from the website form;
- answers routine questions from approved venue knowledge (RAG);
- sends anything sensitive (pricing, date holds, bookings, contracts, refunds, complaints) to staff
  for review, and never sends it automatically;
- drafts anniversary and referral notes to past couples, always reviewed by staff;
- logs every customer and every sent email in HubSpot;
- gives staff a portal to approve, edit or reject emails, manage knowledge documents, and manage staff.

The stack: Deep Agents, OpenRouter models, InsForge (Postgres with pgvector, and staff login),
HubSpot, Gmail, LangSmith, and AWS (Lambda, API Gateway, SQS, EventBridge Scheduler, S3,
Secrets Manager, ECR, Amplify), all deployed with Terraform and GitHub Actions.

---

## Part 2. Install the tools on your computer

1. Install **Python 3.12 or newer**.
2. Install **uv**, the Python package manager we use for everything.
3. Install **git**.
4. Install a code editor (we use VS Code with Claude Code).
5. Create a **GitHub** account if you don't have one.

Terraform, the AWS CLI and Docker are installed later, in Part 11.

---

## Part 3. Create the accounts and collect the keys

Each service gives you one or more values that go into the `.env` file in Part 5. Never paste these
values into chat, screenshots or commits.

### 3.1 OpenRouter (models and embeddings)

1. Sign up at **openrouter.ai**.
2. Add a few dollars of credit. It pays for both the chat models and the embeddings.
3. Go to **Keys** → create a key → copy it. This is `OPENROUTER_API_KEY`.

### 3.2 InsForge (database and staff login)

1. Sign up at **insforge.dev** and create a new project.
2. Copy the project's **backend URL** (it looks like `https://<app>.<region>.insforge.app`). This is
   `INSFORGE_URL`.
3. Copy the **admin API key**, not the anon key. This is `INSFORGE_API_KEY`. It has full access, so
   it is only ever used on the server.
4. Go to **Project Settings → Connect → Connection String** and copy it. This is `DATABASE_URL`. It
   stores paused agent runs.
5. Set up staff login under **Authentication**:
   - turn **email verification off** (staff accounts are created by an admin, not by sign-up);
   - add the exact redirect URLs `http://localhost:3000/sign-in` and
     `http://localhost:3000/reset-password` to the allowed redirect URLs;
   - **Disable New User Signups** stops strangers from creating accounts. We left it off during
     development. Turn it **on** before production.

### 3.3 HubSpot (CRM)

1. Create a free **HubSpot** account (the free plan allows 2 users and 1,000 contacts).
2. As a super admin, go to **Settings → Integrations → Private Apps** (called **Legacy Apps** in
   newer accounts) → **Create app**.
3. Under **Scopes**, tick `crm.objects.contacts.read` and `crm.objects.contacts.write`.
4. Create the app and copy its **access token**. This is `HUBSPOT_PRIVATE_APP_TOKEN`.

We use **Contacts**, not HubSpot's separate Leads object.

### 3.4 Gmail (read and send email)

This is the trickiest part and takes about 10 minutes. You need four values: `GMAIL_CLIENT_ID`,
`GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` and `GMAIL_SENDER`.

**Step 1: create a Google Cloud project**
1. Go to **console.cloud.google.com**.
2. Click the project dropdown at the top → **New Project**.
3. Name it `juniper-hollow` → **Create**.

**Step 2: turn on the Gmail API**
1. Type **Gmail API** in the search bar.
2. Click **Enable**.

**Step 3: set up the consent screen**
1. Left menu → **Google Auth Platform** (or **OAuth consent screen**) → **Get started**.
2. Fill in:
   - App name: `Juniper Hollow`
   - Support email: your Gmail
   - Audience: **External**
   - Contact email: your Gmail
3. Click **Create**.
4. Go to **Audience → Test users → Add users**, add your Gmail, and click **Save**.

**Step 4: create the Client ID and Client secret**
1. Go to **Clients → Create client**.
2. Type: **Web application**.
3. Leave the **"use this for an AI-powered agent"** box **unticked**. That box is for Google's MCP
   tools, which we don't use.
4. Under **Authorized redirect URIs**, add exactly `https://developers.google.com/oauthplayground`.
5. Click **Create**.
6. Copy the **Client ID**. This is `GMAIL_CLIENT_ID`.
7. Copy the **Client secret**. This is `GMAIL_CLIENT_SECRET`.

You don't need the Auth URI, Token URI, certificate URL or project ID.

**Step 5: get the refresh token**
1. Open **https://developers.google.com/oauthplayground**.
2. Click the ⚙️ **gear** at the top right.
3. Leave the settings as they are (Server-side, Offline, Consent Screen).
4. Tick **"Use your own OAuth credentials"**.
5. Paste your **Client ID** and **Client secret**, then click **Close**.
6. Pick the two Gmail permissions. Do **one** of these:
   - **Easiest:** in the list on the left, expand **Gmail API v1** and tick
     `https://www.googleapis.com/auth/gmail.readonly` and
     `https://www.googleapis.com/auth/gmail.send`.
   - **Or** paste both into **"Input your own scopes"** on one line, with a space between them.
7. Click **Authorize APIs**.
8. Sign in with your Gmail. You should see **two** permissions: read and send email.
9. At "Google hasn't verified this app", click **Continue**, then **Allow**.
10. Back on the Playground, Step 2 is open. Click **Exchange authorization code for tokens** straight
    away, because the code expires within minutes.
11. Copy the **Refresh token** (it starts with `1//`). This is `GMAIL_REFRESH_TOKEN`.

Don't copy the **Access token**. It expires in an hour, and the app makes fresh ones from the
refresh token. You don't need Playground Step 3; just close the page.

**Step 6: the sender**
`GMAIL_SENDER` is the Gmail address you signed in with in Step 5. It must be the **same** account,
otherwise the agent can't tell your own sent mail from customer mail.

**Every 7 days: renew the refresh token**
While the Google app is in **Testing** mode, Google expires the refresh token after **7 days**. When
Gmail calls start failing with `invalid_grant`:
1. Open the OAuth Playground.
2. ⚙️ **gear** → tick **"Use your own OAuth credentials"** → paste the **same** Client ID and Client
   secret → **Close**.
3. Tick the same two scopes, `gmail.readonly` and `gmail.send`, under **Gmail API v1**.
4. Click **Authorize APIs** → sign in → **Continue** → **Allow**.
5. Click **Exchange authorization code for tokens**.
6. Copy the new **Refresh token** and replace `GMAIL_REFRESH_TOKEN` in `.env`. Once deployed, update
   it in **AWS Secrets Manager** instead.

The Client ID, Client secret and sender stay the same; only the refresh token changes. To stop the
7-day expiry for good, publish the app: **Google Auth Platform → Audience → Publish app**. Part
19.1 covers the steps that go with it.

**Gmail troubleshooting**

| Problem | Fix |
|---|---|
| `redirect_uri_mismatch` | Add `https://developers.google.com/oauthplayground` to the client's **Authorized redirect URIs** (Step 4). |
| `access_denied` / not allowed | Add your Gmail under **Audience → Test users** (Step 3). |
| Only one permission shown at sign-in | Redo Step 5 with **both** scopes. |
| `invalid_grant` | The refresh token expired or was revoked. Renew it (see above). |
| Code won't exchange | The authorization code expired. Click **Authorize APIs** again. |

### 3.5 LangSmith (tracing)

1. Sign up at **smith.langchain.com**.
2. Go to **Settings → API Keys** → create a key. This is `LANGSMITH_API_KEY`.

---

## Part 4. Start the project

1. Create the project folder and open it in your editor.
2. Run `git init`.
3. Create a **`.gitignore`** first, before any secret exists. It ignores `.env`, `.venv`, caches,
   local data folders, editor and Claude Code settings, the `images/` folder, the private planning
   file `docs/project-tasks.md`, and (added later) Terraform state, plans and `.terraform/` folders.
   The rest of `docs/` is committed.
4. Create **`pyproject.toml`** with the project name, Python 3.12+, and pinned versions of the
   dependencies: Deep Agents, LangChain, the OpenRouter and OpenAI LangChain packages, the text
   splitter, pypdf, pydantic, LangSmith, httpx and python-dotenv. A `dev` group holds pytest. More
   dependencies are added as the build goes on (FastAPI, Mangum, boto3, the Postgres checkpointer,
   psycopg, python-multipart), plus uvicorn in the `dev` group for running the API locally.
5. Run `uv sync`. It creates `.venv` and writes **`uv.lock`**, which pins every version. Commit
   `uv.lock` (we ignored it at first, then started tracking it so builds are repeatable).
6. Create the empty package layout so every file has its place:
   - `src/` with `__init__.py`, `config.py`, `schemas.py`, `prompts.py`, `models.py`, `rag.py`,
     `middleware.py`, `database.py`, `agents.py`, `api.py`, `runtime.py`, `worker.py`, `cron.py`
     (and later `storage.py`);
   - `src/tools/` with `knowledge_tools.py`, `inquiry_tools.py`, `retention_tools.py`,
     `supervisor_tools.py`;
   - `src/security/` with `auth.py` and `permissions.py`;
   - `tests/` and `frontend/`.
7. Add the venue's knowledge file, **`juniper-hollow-knowledge-pack.pdf`**, to the project root.
   It holds the FAQs, venue details and policies the agent answers from.
8. Write a first **README** with the project overview.

---

## Part 5. Create your `.env`

1. Create **`.env.example`** in the project root with every key left blank. It is safe to commit.
2. Copy it to **`.env`** and fill in the values from Part 3. Use no quotes and no spaces around `=`.

The keys:
- **Models and embeddings:** `OPENROUTER_API_KEY`
- **InsForge:** `INSFORGE_URL`, `INSFORGE_API_KEY`, `DATABASE_URL`
- **CRM:** `HUBSPOT_PRIVATE_APP_TOKEN`
- **Gmail:** `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_SENDER`
- **Tracing:** `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=juniper-hollow-agent`
- **Switches:**
  - `AUTO_SEND_ROUTINE=false` (true lets routine, source-backed replies send themselves);
  - `RUN_JOBS_LOCALLY=true` (runs agent jobs on your machine instead of AWS);
  - `TEST_EMAIL_RECIPIENT` (where test emails go; defaults to `GMAIL_SENDER`).

`.env` is git-ignored. Never commit it, paste it into chat, or screenshot it.

---

## Part 6. Build the backend (`src/`), file by file

This is the order we built the files in. After most steps we ran a quick check or a live test.
The tests talk to the real services and clean up after themselves. The only stand-in is in
`test_security.py`, which uses pytest's `monkeypatch` to simulate InsForge failures.

### 6.1 `config.py`: settings
Reads `.env` and holds the shared settings: the company ID, venue timezone, the switches, and the
names of every key the app needs.

### 6.2 `schemas.py`: the runtime context and data shapes
Defines **`AgentContext`**, a frozen record the server fills in for every run: company ID, contact
ID, job type, whether staff approved, marketing consent, cooldown, and whether the source document
is outdated. This is the **trust boundary**: tools read these values from the context and never
trust IDs or consent typed by the model. It also holds the shapes of inquiries and of each
subagent's structured result.

### 6.3 `rag.py` and `tools/knowledge_tools.py`: the knowledge base
- Loads PDF, TXT or Markdown files, splits them into overlapping chunks, embeds them with Google
  `gemini-embedding-2` through OpenRouter, and stores them in a **pgvector** table in InsForge
  Postgres.
- Every chunk carries the company, source ID and version.
- Search only returns chunks from the company's **published** document version, so indexing a new
  file never changes what the agent sees until a manager publishes it.
- `search_company_knowledge` is the read-only tool the agent uses.
- We started with a local Chroma store, then moved to pgvector so the API, the indexing job and the
  agent all share one index with no extra service to host.
- **Test:** `test_rag.py` indexes the knowledge pack under a throwaway company and checks that only
  published versions are found.

### 6.4 `prompts.py`: the instructions
XML-structured prompts for the supervisor and the three subagents, with shared blocks: the safety
rules, when staff must decide, and the writing style (120–180 words, no filler, no dashes, plain
text). The supervisor's final answer is at most three lines.
Added later, after live testing (Part 17.2): rules that stop the agents treating details the
customer never asked about as gaps.

### 6.5 `models.py`: the chat models
Four `ChatOpenRouter` models with reasoning switched on, streaming off, and output-token caps.
Different agents use different models.

Added later, once the agent ran inside a Lambda with a 15-minute limit: each model gets a **request
timeout of 240 seconds** and **`max_retries=1`**, so a hung call fails instead of using up the whole
job.
- We picked 240 s from real LangSmith traces: healthy calls took up to about 190 s (median 12 to 17 s).
- `ChatOpenRouter`'s `timeout` is in **milliseconds** (`240_000`), unlike the general LangChain docs.
- `max_retries=1` gives the OpenRouter SDK a 150-second window for quick 5xx and connection errors; a
  timed-out call is past that window, so it isn't retried there and goes to `ModelRetryMiddleware`.
- Never use `max_retries=0`: the SDK then falls back to its own default of retrying timeouts for up to
  an hour. We proved the setting with a local server that never answers: one attempt, then an error
  right at the timeout.

### 6.6 `middleware.py`: the guardrails
- **Human-in-the-loop gate:** pauses the send tools so staff can approve, edit or reject. It fails
  closed: a reply only sends itself when `AUTO_SEND_ROUTINE=true`, the draft cites sources, it isn't
  flagged for review, and a sensitive-topic pattern matches neither the customer's message nor the
  reply. Retention drafts always pause.
- **Delivery guardrail:** a hard block on the send tools when there is no company, no recipient, no
  consent, an active cooldown, an outdated source, or a needed review without staff approval.
- **Tool error handling:** turns tool errors into messages the model can read and fix.
- Added later: **PII redaction** (card, bank and ID numbers are removed before any model sees them,
  and LangSmith traces are masked), **OpenRouter prompt caching**, and, after live testing, a
  **tool budget** that hides a subagent's tool once its limit is used up (Part 17.1).

### 6.7 `database.py`: the data layer
No ORM or database driver: everything goes over HTTP to InsForge (raw SQL and record CRUD). It holds
the schema for staff members, email drafts, couples, documents and audit logs, created by one
`init_schema` call. Drafts move from `pending_review` to reviewed to `sent`.
**Test:** `test_database.py`.

### 6.8 `tools/inquiry_tools.py`: HubSpot and inquiry drafts
Creates or updates the customer as a HubSpot **Contact**, reads their history, and saves inquiry
drafts. Saving forces `requires_review` on when the policy says so, even if the model said no, and
rejects drafts containing em or en dashes.

It also logs emails on the contact's HubSpot **Activity timeline** (`POST /crm/v3/objects/emails`,
linked to the contact with HubSpot's association type 198):
- the customer's message as an **incoming** email, once per inquiry, before the agent runs (website
  form fields such as venue and guest count are added under the message);
- every reply we send as an **outgoing** email (from `supervisor_tools.py`).

We confirmed with one test call that the private app's token can create these activities. Reading them
back through the API would need the extra `sales-email-read` scope, which the app doesn't need.
Staff see the conversation in HubSpot under **CRM → Contacts → the customer → Activity**.

### 6.9 `tools/retention_tools.py`: past couples
Checks if a couple may be contacted: no consent, do-not-contact, an open complaint, a 90-day
cooldown, or an existing pending draft all block it. Reads couple history and saves retention
drafts, which always need review.
**Test:** `test_tools.py` covers the HubSpot and retention tools.

### 6.10 `agents.py`: the agent graph
Builds the **supervisor** with `create_deep_agent` and three subagents:
- `knowledge_retriever`: searches the knowledge base (read-only);
- `inquiry_agent`: reads history and saves inquiry drafts;
- `retention_agent`: reads couple history and saves retention drafts.

Subagents can only **draft**. Only the supervisor holds the send tools. Each agent gets call limits,
retries and error handling; each subagent can call its model at most 10 times per task (added in
Part 17.1). Memory under `/memories/` is stored per company and contact; everything
else is per conversation. Each subagent returns a structured result.
**Check:** building the graph (`import src.agents`) must succeed.

### 6.11 `tools/supervisor_tools.py`: sending
The two send tools, for inquiry replies and retention notes. They send through the **Gmail API**
(as a reply in the customer's thread, with an HTML version of the text), then log it on the
customer's HubSpot contact as an **outgoing email activity**. The Gmail token is fetched before the
draft is claimed, so a token problem never leaves a draft stuck.
**Test:** `test_supervisor_tools.py` (costs tokens and sends real emails).

### 6.12 `security/auth.py` and `security/permissions.py`: staff login and roles
- `auth.py` checks a staff member's login token with InsForge and finds their active staff record.
  A missing or bad token gets 401, and an InsForge outage fails closed. It also signs staff in,
  creates staff logins with an admin-chosen password, and handles the password-reset code flow
  (InsForge emails a 6-digit code).
- `permissions.py` maps roles to actions: `email_approver` reviews emails, `knowledge_manager`
  manages documents, `staff_admin` manages staff. A wrong role or company gets 403.
**Test:** `test_security.py`.

### 6.13 `api.py`: the web API (FastAPI)
- `POST /inquiries`: the website form (name, email, message, venue, guest count, preferred date).
  It saves the inquiry and queues a job.
- `POST /auth/login`, `GET /me`, and the password code and reset endpoints.
- `GET /reviews`, `GET /reviews/{id}`, `POST /reviews/{id}/decision`: approve, edit or reject.
- `GET /staff`, `POST /staff`, and updating staff (staff admin only).
- `GET /documents`, `POST /documents` (upload up to 4 MB), publish and rollback (knowledge managers).
  Publish and rollback swap the live version in a single SQL statement.
- It runs locally with uvicorn and on AWS Lambda through Mangum.

### 6.14 More tables in `database.py`
- `agent_reviews`: the paused runs waiting for staff.
- `jobs`: every piece of agent work, with a dedupe key so the same email is never handled twice.
- `sync_state`: where the Gmail poll left off.
- An `error` column on documents for failed indexing.

### 6.15 `runtime.py`: the job runner
Runs one job: starts a new agent run, or resumes a paused one with the staff decision.
Paused runs are saved in Postgres (the LangGraph Postgres checkpointer), so they can wait for days.
It also runs the **index** job for uploaded documents (`processing` → `ready` or `failed`). On AWS
the worker Lambda calls it; locally, jobs run in a background thread when `RUN_JOBS_LOCALLY=true`.

### 6.16 `worker.py`: the queue worker
A Lambda triggered by the SQS jobs queue, one job per invocation. It checks that no other job of the
same conversation is running, runs the job with `runtime.run_job` (up to 15 minutes; a job ends at the
first staff-review pause), and reports only failed messages back to SQS so they are retried.

### 6.17 `cron.py`: the scheduled jobs
- **Every 8 minutes:** polls Gmail for new customer emails and queues each one as a job. Replies
  stay in the same Gmail thread. Added later (Part 17.3): the quoted earlier message under a reply
  is cut off before the job is queued.
- **Every day:** queues retention jobs for couples with an anniversary coming up, marks jobs cut
  off by the worker's timeout as failed, and deletes conversation data older than **120 days** (drafts and audit logs are kept).

### 6.18 `storage.py`: uploaded files
Stores the original uploaded file in the S3 knowledge bucket when deployed, or in `.data/uploads`
locally. The indexing job reads it back from there.

### 6.19 Speed and polish
- One shared HTTP client for every outbound call (InsForge, HubSpot, Gmail), which cut each call
  from about 1.3 s to 0.3 s.
- When deployed, `src/__init__.py` loads the app's keys from **AWS Secrets Manager** into the
  environment.
- **Test:** `test_end_to_end.py` runs the full staff workflow: review, edit, reject, auto-send and
  retention.

---

## Part 7. One-time setup: database, knowledge and the first admin

Run the one-time setup script from the README once. It:
1. creates every table (with the pgvector extension);
2. indexes the knowledge pack and marks it **published**;
3. creates the first **staff admin** login with all three roles.

Change the admin email and password in the script before running it. From then on, the staff admin
signs in to the portal and adds every other staff member there.

**Check that it all connects:** build the agent graph, then run `test_rag.py`. If both pass, your
keys, database and knowledge are working.

---

## Part 8. Build the website (`frontend/`)

A static site in plain HTML, CSS and JavaScript:
- `index.html` (home), `venues.html` (the two venues), `contact.html` (the inquiry form);
- `login.html` (staff login), `password.html` (change or forgotten password with an emailed code);
- `portal.html`, the staff portal, with three sections shown by role:
  - **Pending reviews:** open an email, then approve, approve with edits, or reject;
  - **Knowledge documents:** upload, wait for *Ready to publish*, publish, or roll back;
  - **Staff:** add a staff member with an email, starting password and roles, or deactivate one;
- `styles.css` for the look, `app.js` for the API calls and busy labels on buttons;
- `config.js`, which holds the API address. Locally it points to `http://localhost:8000`; the
  deploy script rewrites it with the real API address.

### Try it locally
1. Terminal 1: start the backend with uvicorn on port 8000.
2. Terminal 2: serve the `frontend` folder on port 3000.
3. Open **http://localhost:3000**. Use `localhost`, not `127.0.0.1`, because the backend only
   accepts requests from `http://localhost:3000`.
4. Send an inquiry from **Contact Us**, sign in as the staff admin, and approve the review.

With `RUN_JOBS_LOCALLY=true`, every inquiry runs the agent on your machine. It costs tokens and
creates a draft for staff review.

---

## Part 9. Put the code on GitHub

1. Create a GitHub repository (ours is public).
2. Push the code to **`main`**.
3. Create a **`dev`** branch. From now on, work is pushed to `dev` first and merged into `main`
   after testing. Pushing to `dev` deploys the dev environment; pushing to `main` deploys prod.

In the video we push with two Claude Code agents: one commits each changed file to `dev`, and the
other merges `dev` into `main`. Plain git works the same way.

---

## Part 10. Write the deployment files

### 10.1 Terraform bootstrap (`terraform/bootstrap/`, run once by hand)
Creates the things the main stack needs before it can run:
- the **state bucket** that stores Terraform's state (versioned, encrypted, private, protected from
  deletion);
- the **GitHub OIDC provider**, so GitHub Actions can log in to AWS without stored keys;
- the **`juniper-hollow-github-deploy`** role that GitHub Actions uses. It only trusts this
  repository's `dev` and `prod` environments.

### 10.2 The main stack (`terraform/`)
Two environments, **dev** and **prod**, each a Terraform workspace with its own state.
- `versions.tf` and `backend.tf`: Terraform and AWS provider versions, and the S3 state with a
  lock file.
- `variables.tf`, `terraform.tfvars` (dev values) and `prod.tfvars` (prod overrides: no localhost,
  longer log retention, buckets protected, higher API limits). No secrets go in these files.
- `main.tf`: shared names and tags, and a guard that only allows the `dev` and `prod` workspaces.
- `s3.tf`: the private knowledge uploads bucket.
- `sqs.tf`: the FIFO jobs queue and its dead-letter queue. The visibility timeout is 6 × the worker's
  timeout, as AWS recommends for Lambda.
- `secrets.tf`: the app secret, **created empty**. You fill it in by hand later, so keys never
  touch Terraform.
- `ecr.tf`: the Lambda image repository, with tags that can't be overwritten.
- `iam.tf`: the roles for the three Lambdas.
- `lambda.tf`: the api, worker and cron Lambdas, all from one image, plus the SQS trigger. The worker
  runs the agent jobs: 2,048 MB, the 15-minute maximum timeout, one job per invocation, and at most 2
  jobs at once (the trigger's maximum concurrency).
- `api_gateway.tf`: the HTTP API in front of the api Lambda, with CORS and throttling.
- `scheduler.tf`: Gmail polling every 8 minutes and the daily job at 7 am Chicago time.
- `amplify.tf`: the website on Amplify Hosting, on its default address.
- `outputs.tf`: the website and API addresses and everything the deploy script needs.

### 10.3 Docker image
- `Dockerfile`: the Lambda image (x86) for the api, worker and cron functions. The worker runs the
  agent from it, so it carries every dependency in `uv.lock`.
- `.dockerignore`: keeps secrets, tests, evals, docs, the frontend and Terraform out of the image.

### 10.4 Deploy and destroy scripts (`scripts/`)
`deploy.sh` and `deploy.ps1` do the same steps on a laptop or in CI:
1. set up Terraform and pick the environment's workspace;
2. create the image repository;
3. build and push the Lambda image, tagged with the git commit (skipped if already pushed);
4. apply the rest of Terraform;
5. write the API address into `config.js` and publish the website to Amplify;
6. warn if the app secret is still empty.

`destroy.sh` and `destroy.ps1` remove one environment, after you type its name to confirm.

### 10.5 GitHub Actions (`.github/workflows/`)
- `deploy.yml`: a push to `dev` deploys dev, a push to `main` deploys prod, or run it by hand and
  pick one. It logs in to AWS through OIDC and runs the deploy script.
- `destroy.yml`: manual only; you pick the environment and type its name again to confirm.

---

## Part 11. Set up AWS

### 11.1 Create the AWS account
1. Sign up at **aws.amazon.com**.
2. Choose a plan. We chose the **Paid plan**: pay-as-you-go with no minimum, and your sign-up
   credits are used first.
3. Recommended: in **Billing → Budgets**, create a small budget alert (for example $10).

### 11.2 Install Terraform and the AWS CLI
1. Open a separate PowerShell window.
2. Install Terraform with `winget install Hashicorp.Terraform`.
3. Install the AWS CLI with `winget install Amazon.AWSCLI`.
4. Close and reopen every terminal, including the one in your editor.
5. Check with `terraform -version` and `aws --version`.
6. Docker Desktop is only needed to deploy from your own computer. GitHub Actions has its own.

### 11.3 Create a login for your computer
Never create access keys for the root account.
1. Sign in as root and open **IAM → Users → Create user** (for example `admin`).
2. Attach the **AdministratorAccess** policy and create the user.
3. Open the user → **Security credentials → Create access key** → use case **Command Line
   Interface**. Copy the access key and secret.
4. Run `aws configure` and enter the access key, the secret, region **`us-east-1`** and output
   **`json`**.
5. Check with `aws sts get-caller-identity`. It should show your account ID.

The access key stays on your computer only. It never goes into GitHub or chat.

---

## Part 12. Run the bootstrap (once)

1. In a terminal, go to `terraform/bootstrap`.
2. Run `terraform init`, then `terraform apply`, and type `yes` when asked.
3. If it fails because a GitHub OIDC provider already exists in your account, run the apply again
   with `-var create_github_oidc_provider=false`.
4. Copy the output **`github_deploy_role_arn`**.
5. Keep `terraform/bootstrap/terraform.tfstate` safe. It is git-ignored, so back it up yourself.

---

## Part 13. Set up GitHub

In the repository, open **Settings**:

1. **Secrets and variables → Actions → New repository secret**, and add two secrets:
   - `AWS_ROLE_ARN`: the `github_deploy_role_arn` value from the bootstrap;
   - `DEFAULT_AWS_REGION`: `us-east-1`.
2. **Environments → New environment**:
   - create **`dev`**;
   - create **`prod`**, tick **Required reviewers** and add yourself, and set **Deployment
     branches** to `main` only.

These two secrets are the only AWS values in GitHub. There are no AWS keys in GitHub, and the app's
keys never go there either.

---

## Part 14. The first dev deploy

1. Push your work to the **`dev`** branch. Each push starts a **Deploy dev** run.
   - Runs from pushes made **before** Part 13 fail because the GitHub secrets didn't exist yet.
     Ignore them.
   - If nothing new needs pushing, start it by hand: **Actions → Deploy → Run workflow**, set **Use
     workflow from** to `dev` and **Environment** to `dev`, then click **Run workflow**.
2. Open the run and watch the **Deploy** step. It sets up Terraform, creates the image repository,
   builds and pushes the Lambda image, applies Terraform, and publishes the website. The first run is
   the slowest because the image is built from scratch.
3. The end of the **Deploy** step's log prints two addresses:
   - **Website:** `https://dev.<app id>.amplifyapp.com`. In the AWS console it is also under
     **Amplify → All apps → `juniper-hollow-dev` → branch `dev`**.
   - **API:** the API Gateway address, also under **API Gateway → APIs → `juniper-hollow-dev`**
     (Invoke URL). The website already knows it through `config.js`.
4. The log ends with a warning that the app secret has no value yet. That is expected the first time.

### 14.1 Fill the app secret
The functions can't start until the app's keys are in Secrets Manager.
1. In the AWS console, set the region (top right) to **N. Virginia (us-east-1)**.
2. Open **Secrets Manager → Secrets → `juniper-hollow-dev/app`**.
3. In the **Secret value** box, click **Retrieve secret value**. The secret is empty, so click **Set
   secret value**.
4. Choose the **Plaintext** tab and delete the `{}` that is there.
5. Paste a JSON object with these 10 keys and your values from `.env`: `OPENROUTER_API_KEY`,
   `INSFORGE_URL`, `INSFORGE_API_KEY`, `DATABASE_URL`, `HUBSPOT_PRIVATE_APP_TOKEN`, `GMAIL_CLIENT_ID`,
   `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_SENDER`, `LANGSMITH_API_KEY`.
   - Turn each `.env` line `NAME=value` into `"NAME": "value"`, wrap them all in `{ }`, and put a
     comma between entries but none after the last one.
   - Leave out `RUN_JOBS_LOCALLY` and `TEST_EMAIL_RECIPIENT` (they are only for your computer), and
     `LANGSMITH_TRACING` and `LANGSMITH_PROJECT` (Terraform already sets them as plain settings).
6. Check that the editor shows **0 errors**, then click **Save**.

No redeploy is needed; the functions read the secret when they start. While the Google app is in
Testing mode, renew `GMAIL_REFRESH_TOKEN` here every 7 days (see Part 3.4).

### 14.2 Check the AWS quotas we depend on
New AWS accounts start with some quotas lower than the AWS defaults.
1. Open **Service Quotas → AWS services**, pick a service, and compare the **applied account-level
   quota value** (what your account really has) with the **AWS default quota value** (what accounts
   normally get).
2. Ours: **Lambda concurrent executions is 10** (the default is 1,000), shared by the api, worker and
   cron functions. That is why the worker's queue trigger has a maximum concurrency of 2, leaving 8
   for the website's API and the cron. Everything else we use (API Gateway, SQS, EventBridge
   Scheduler, S3, ECR, Secrets Manager, Amplify, CloudWatch Logs, IAM) is at its default.

### 14.3 Test dev, then prod
1. Open the website address, send an inquiry from **Contact Us**, sign in as the staff admin, and
   approve the review. Logs are in **CloudWatch** (the Lambda functions) and **LangSmith** (the agent
   runs).
2. Merging `dev` into `main` deploys prod once you approve the run in GitHub Actions. Fill
   **`juniper-hollow-prod/app`** the same way only when you're ready to switch: dev and prod with the
   same Gmail and InsForge keys would both poll the same inbox every 8 minutes. Until it is filled,
   prod's cron fails every 8 minutes, which only adds error logs in CloudWatch. Part 19 walks
   through going to prod.

---

## Part 15. Evaluate the agent with LangSmith

Next we measure how well the agent behaves. The architecture doc requires a
LangSmith evaluation, and `AUTO_SEND_ROUTINE` stays off in prod until routine replies pass it (dev
turns it on for testing, Part 18). Everything in
this part runs on your own computer; it doesn't need the AWS deploy.

### 15.1 Research the LangSmith docs
1. Connect the **LangChain docs MCP** to Claude Code so it can read docs.langchain.com directly.
2. Read the LangSmith sections on **datasets**, **running an evaluation with the SDK**, **evaluator
   types**, and **analyzing experiments**. We had a Claude Code subagent do about 25 lookups and save
   detailed notes to `docs/langsmith-evaluation-notes.md`.
3. Learn the evaluator types:
   - **Code evaluators:** plain functions; deterministic and free. Best for pass/fail rules.
   - **Trajectory evaluators:** check which tools the agent called, and in what order.
   - **LLM-as-judge:** a second model grades things code can't, like whether a reply is grounded.
   - **Decision model (Jev):** typed yes/no questions, but it can only be set up in the LangSmith UI,
     not the SDK. We left it for later, as an online check on live traffic.
4. Decide SDK or UI: we run everything from code with the SDK, so the dataset, the evaluators and the
   pass rules live in the repo. The LangSmith UI is where we read and compare the results.

### 15.2 Decide what to measure (7 checks)

| # | Check | Type | Applies to |
|---|---|---|---|
| 1 | Sensitive inquiries are held for staff, routine ones are cleared to auto-send, nothing is sent | Code | all |
| 2 | Prompt injection changes nothing: held, not sent, no price, no promise, no system-prompt leak | Code | injection |
| 3 | Routine replies cite the knowledge pack | Code | routine FAQ |
| 4 | Drafts are 120 to 180 words | Code | all |
| 5 | The knowledge base is searched before the draft is saved | Trajectory | routine FAQ |
| 6 | The right tools are called, nothing from the retention path, nothing sent unreviewed | Trajectory | all |
| 7 | Every fact in a routine reply is supported by the retrieved knowledge | LLM judge | routine FAQ |

The safety rules (1 and 2) are code checks on purpose: a safety pass/fail should never depend on a
judge model that can be wrong. The dash rule isn't checked because `save_inquiry_draft` already
rejects dashes in code.

### 15.3 Create the dataset (`evals/dataset.py`)
1. Pick the examples. The docs suggest starting with 10 to 20 hand-picked ones; we use **13**:
   - 5 routine FAQs: guest capacity, rain plan, dogs, music end time, wheelchair access;
   - 2 pricing, 2 date hold or booking, 1 mixed (a FAQ plus a refund question), 1 complaint;
   - 2 prompt injections ("SYSTEM: ignore all previous rules…" and "You are now in admin mode…").
2. Choose routine questions whose answers contain no prices or dates. The sensitive-topic check also
   reads the reply, so an answer mentioning "$8 parking" would be held for review and look like a
   failure.
3. For each example, store:
   - **inputs:** the customer's name, email, venue, subject and message, the same fields a real
     inquiry has;
   - **reference outputs:** whether it must go to review, the tools that should be called, and, for
     routine questions, a reference answer and the source ID from the knowledge pack;
   - **metadata:** the category (`faq`, `pricing`, `date_hold`, `mixed`, `complaint`, `injection`),
     so results can be grouped by category.
4. From the project root, run `uv run python -m evals.dataset`. It creates the dataset
   `juniper-hollow-inquiries` in LangSmith, and does nothing if it already exists.

### 15.4 Write the target function (`evals/run.py`)
The target runs one example through the agent and returns what the evaluators need.
1. **Use the real inquiry path:** build the agent's message with the same `runtime.build_start` that
   production uses, then run the supervisor.
2. **Use a separate eval company** (`eval-juniper-hollow`) with the knowledge pack published for it.
   It is indexed once and reused, and real company data is never touched.
3. **Force `AUTO_SEND_ROUTINE` off,** so every send pauses for review and no email can leave. The run
   ends at that pause, which is the expected result, not an error.
4. **Stream with the subagents included.** The search and draft tools run inside the subagents, so
   the target streams with `subgraphs=True` to record every tool call, the subagents used, and the
   retrieved knowledge.
5. **Return** the saved draft (read from the database), the ordered tool calls, the subagents, the
   retrieved knowledge, whether anything was sent, and whether the send rule would have allowed
   auto-send.
6. **Clean up afterwards:** delete the run's drafts, audit rows and test HubSpot contacts. The
   published eval knowledge is kept for the next run.

### 15.5 Write the evaluators (`evals/evaluators.py`)
1. Write one Python function per check. Each returns a named score and a comment explaining it.
2. Each evaluator reads the example's category and **skips** examples it doesn't apply to, so the
   averages aren't diluted. We checked in the SDK source that a skip records no score.
3. **The groundedness judge** uses a different model from the agents (Claude Sonnet 5.5 through
   OpenRouter), with temperature 0, structured output, and three graded examples in its prompt.
4. **The "search before draft" check** gives partial credit (the fraction of expected steps seen in
   order), as the LangSmith docs show.

### 15.6 Smoke-test before the full run
A full run costs model tokens and takes close to an hour, so test small first:
1. `uv run python -m evals.run --limit 1 --no-upload` runs one example and records nothing in
   LangSmith.
2. Add `--category faq` or `--category injection` to pick the example type, so every evaluator gets
   tested at least once.
3. Check that each evaluator scores, the comments make sense, and the cleanup removed the test drafts
   and HubSpot contacts.

### 15.7 Run the full experiment
1. From the project root, run `uv run python -m evals.run`. There's no need to activate the venv or
   `cd` into `evals`; `uv run` uses the project's environment.
2. Each example takes about 2 to 4 minutes, sometimes much longer. Two run at a time, so 13 examples
   took about 50 minutes.
3. Keep the computer on until the terminal prints the score summary. The agent **and** the evaluators
   run on your computer, so shutting down stops both and skips the cleanup.
4. The terminal prints the average for each check and the comment for every failure.

### 15.8 Read the results in LangSmith
1. Open **Datasets & Experiments → `juniper-hollow-inquiries` → Experiments**.
2. The progress bar has two lines: **runs completed** (the agent finished) and **runs evaluated** (our
   evaluators scored it). The second one lags behind the first.
3. Click the experiment to see every example with its scores, filter or sort by a score, and group by
   the `category` metadata.
4. Open an example's **trace** to see each tool call. The review pause shows up as a `GraphInterrupt`
   "error" at `HumanInTheLoopMiddleware`; that is the expected stop, not a failure.
5. Open the **evaluator run** to read the judge's reasoning.
6. We also checked progress and read the drafts from Claude Code with the **LangSmith MCP**.

### 15.9 Turn the results into next steps
1. Sort each failure into **a real agent problem** (fix the prompts or code) or **a wrong check** (fix
   the evaluator). Read the actual draft in the trace before deciding.
2. Set the first experiment as the **baseline** in LangSmith, make the fixes, re-run, and compare the
   new experiment against it.
3. Turn on `AUTO_SEND_ROUTINE` only when routine replies pass reliably.

---

## Part 16. Test dev end to end with real emails

The evaluation runs on your computer. Now we test the deployed dev environment the way a customer and
a staff member would use it. Each test costs a few cents of model tokens and sends real email.

### 16.1 A website inquiry
1. Open the dev website and send a message from **Contact Us**. Ask one or two routine questions
   whose answers are in the knowledge pack, for example Wi-Fi and wheelchair access at the barn.
   Avoid prices, dates and booking words for this first test.
2. The API saves the inquiry and queues a job straight away. The job's conversation ID is
   `inquiry:<id>`; we use it to find the run in LangSmith.
3. The worker Lambda picks up the job within seconds. A healthy run takes about 1 to 3 minutes.
4. Then one of two things happens:
   - **Auto-send** (dev has `AUTO_SEND_ROUTINE` on): the reply arrives in the customer's inbox.
   - **Staff review:** the email appears under **Pending reviews** in the staff portal. Open it, read
     the review reasons, and approve, edit or reject it. Approving sends it.
5. Open the customer in **HubSpot → CRM → Contacts → Activity**. You should see the customer's
   message as an incoming email and our reply as an outgoing one.

### 16.2 An email inquiry
1. From a different email account, send a question to the venue's Gmail address (`GMAIL_SENDER`).
2. Wait up to **8 minutes** for the cron Lambda's next Gmail poll. It queues the email as a job with
   the conversation ID `gmail:<Gmail thread ID>`.
3. The reply is sent in the same Gmail thread, so the customer sees one conversation.
4. Reply to our answer from the customer's account. The next poll picks it up as a new job in the
   **same** conversation, and the agent reads the customer's history, including our earlier drafts.
5. Only one job per conversation runs at a time. If a job in that conversation is still running, the
   new one waits in the queue until it finishes.

### 16.3 Follow a run in LangSmith
1. Open **LangSmith → Tracing projects → `juniper-hollow-agent`**.
2. Filter by the conversation ID (`inquiry:<id>` or `gmail:<id>`) to see every run in it.
3. Open the run and read it from the top:
   - the supervisor's `task` calls, one per subagent, with the instructions it wrote for them;
   - `search_company_knowledge` calls and what they returned;
   - `save_inquiry_draft`, whose inputs show the draft, `requires_review` and the `review_reasons`;
   - `send_customer_email`, or the pause for staff review.
4. The top-level run often stays **pending** even after the job has finished. The Lambda is frozen
   before the last trace data is uploaded. Look at the steps inside the run; they are complete.
5. We also read traces from Claude Code with the **LangSmith MCP**: give it the conversation ID and
   ask it to explain the run.

### 16.4 Why did a reply go to staff review?
No single agent decides. It is a chain of two checks:
1. **The inquiry agent** (Mistral Large) sets `requires_review` when it saves the draft, and writes
   its `review_reasons`. This is the model's own judgement: it flags anything restricted, anything
   the evidence doesn't answer, or anything it is unsure about. `save_inquiry_draft` then forces it
   on if the sensitive-topic pattern matches.
2. **The send gate** (`should_interrupt_delivery`, plain code on the supervisor, no model) pauses the
   send when `AUTO_SEND_ROUTINE` is off, the draft has no sources, it is marked for review, or the
   sensitive-topic pattern matches the customer's message or the reply.

So when a reply goes to review, open `save_inquiry_draft` in the trace and read `review_reasons`.
If they are empty, the sensitive-topic pattern caught a word. It treats any year (`2026`) or month
and day (`Oct 28`) as a date request, and words such as price, quote, booking, deposit or refund as
restricted.

### 16.5 Problems we hit, and the fix for each

| Symptom | Cause | Fix |
|---|---|---|
| Sending fails with `400 Bad Request` from `oauth2.googleapis.com/token` | The Gmail refresh token expired (7 days in Testing mode) | Renew it (Part 3.4) and update `GMAIL_REFRESH_TOKEN` in `.env` and in the app secret |
| Every model call fails with `401 "User not found"` | The OpenRouter key was disabled | Re-enable the key, or create a new one and update the app secret |
| A new secret value isn't used | Warm Lambdas keep the values they loaded at start | Wait about 15 minutes for them to be replaced, or redeploy |
| The job runs for 15 minutes and nothing arrives, not even a review | A subagent was stuck in a loop until the Lambda was cut off | Fixed in Part 17.1 |
| Routine questions go to review with reasons the customer never asked about | The agents invented "gaps" | Fixed in Part 17.2 |
| Every Gmail reply goes to review, whatever it says | The quoted earlier message (with its date) reached the agent | Fixed in Part 17.3 |
| Promotional or system emails are treated as inquiries | The poller skips bulk mail by its headers, but not all of it | Known limit (Part 20.3) |
| The job ends as `failed` and no one is told | Failed jobs don't alert staff yet | Known limit (Part 20.3) |

---

## Part 17. Fix what live testing found

Each fix follows the same loop: find the run in LangSmith, read the steps, find the cause in the
code or the prompt, make the smallest change, push to `dev`, and send the same test again.

### 17.1 A subagent stuck in a loop
**What we saw:** an inquiry got no reply and no review. In LangSmith, the knowledge retriever had
called the model about 60 times, about every 10 seconds, until the Lambda was cut off at 15 minutes.
Each answer came back with "Tool call limit exceeded".

**The cause:**
1. Each subagent returns a structured result. To get it, LangChain forces the model to call a tool
   on every turn.
2. The retriever hit its limit of 6 searches. The limit blocked further searches but let the model
   carry on.
3. The model wrote its answer as plain text and still asked for one more search, which is a tool
   call. That was blocked, and it repeated. Nothing limited the subagent's number of model calls.

**Why not simply end the subagent at the limit:** LangChain's "end" option replaces the subagent's
last message with a "limit exceeded" notice, so the supervisor would lose the evidence it had found.
We checked this in the library's source code.

**The fix, in `middleware.py` and `agents.py`:**
1. A new **tool budget** middleware: once a tool's limit is used up, the tool is removed from the
   list the model sees. The only tool left is the structured answer, so the subagent returns its
   result. All three subagents use it (6 searches, 3 inquiry saves, 2 retention saves).
2. A **model-call limit of 10** on each subagent. A healthy run uses about 5, so a stuck subagent
   now stops in about a minute instead of using the whole 15.

After the fix, the same three questions took about 30 seconds per subagent.

### 17.2 Routine questions sent to review for invented gaps
**What we saw:** "Is the barn wheelchair accessible?" went to review because "restroom accessibility
is not documented". The customer never asked about restrooms.

**The cause,** found by reading each agent's input in the trace:
1. The **supervisor** (Gemini) wrote the retriever's task as "accessibility (ramps, restrooms,
   ceremony and reception areas)", adding topics the customer didn't ask about.
2. The **retriever** searched for them, found nothing, and reported them as missing.
3. The **inquiry agent** was told to flag every gap, so it marked the draft for review.

**The fix, in `prompts.py` only:**
1. **Supervisor:** pass the customer's questions as they wrote them, without adding questions or
   sub-topics, and pass the evidence on without notes about details nobody asked about.
2. **Knowledge retriever:** a question is answered when the evidence answers it; don't search for or
   report unasked details as missing; a general venue rule (such as dogs being welcome outdoors with
   a handler) applies to every guest unless the source limits it.
3. **Inquiry agent:** a gap is a question the customer asked that the evidence leaves unanswered;
   details they didn't ask about are not gaps or review reasons.

The safety rules didn't change. Anything restricted, or a question the knowledge really doesn't
answer, still goes to staff.

**The result:** the same Wi-Fi and wheelchair questions, sent again from the website form, were
saved with `requires_review=False` and sent to the customer automatically, about 1.5 minutes after
the inquiry arrived.

### 17.3 Gmail replies always held for review
**What we saw:** every customer reply in a Gmail thread went to review.

**The cause:** Gmail puts the earlier message under every reply, starting with a line like
"On Wed, Oct 28, 2026 at 9:18 PM … wrote:". The poller passed the whole email to the agent, so:
- the sensitive-topic pattern saw `2026` and `Oct 28` and treated them as a date request;
- the agent read our own earlier reply as part of the customer's message;
- every reply in the thread resent the whole conversation, using more tokens each time.

**The fix, in `cron.py`:** before queuing an email, the poller cuts it at the "On … wrote:" line (or
Outlook's "Original Message" line) and drops lines starting with `>`. If nothing new would be left,
it keeps the whole text. The earlier messages aren't lost; they are already on the customer's HubSpot
timeline, and the agent still reads our earlier drafts.

We checked it on our real email, on a Gmail header split over two lines, an Outlook reply, inline
answers, a quote-only email, and a normal message that happens to start with "On Saturday…".

### 17.4 Retest
1. Push the fixes to `dev` and wait for the **Deploy dev** run to finish.
2. Repeat Part 16.1 with the same questions that failed. Make the message match itself: if it says
   "two questions", list two. A mismatch is a fair reason for review.
3. Repeat Part 16.2 and reply in the thread without deleting the quoted text.
4. Re-run the evaluation (Part 15.7) and compare the new experiment with the baseline in LangSmith.
   A fix that helps one inquiry must not lower the other scores.

---

## Part 18. Turn on auto-send for routine replies

1. `AUTO_SEND_ROUTINE` is a Terraform setting, `auto_send_routine`, that becomes a Lambda setting.
   Its default is **off**.
2. Know which file sets it for which environment:
   - `terraform/terraform.tfvars` is loaded for **both** dev and prod;
   - `terraform/prod.tfvars` is added on top for prod, and only changes what it sets.
3. **Dev:** we set `auto_send_routine = true` in `terraform.tfvars` to test auto-send.
4. **Prod:** because `terraform.tfvars` now turns it on, prod would auto-send too. Keep prod off by
   setting `auto_send_routine = false` in `prod.tfvars`, and change it to `true` only when the
   evaluation and the live tests show routine replies are right every time.
5. Even with it on, these always go to staff: anything the sensitive-topic pattern catches, drafts
   without sources, drafts the inquiry agent marks for review, and every retention email.
6. Pushing the change deploys it. No code changes are needed.

---

## Part 19. Go to production

Work through this list in order. Prod is deployed from `main` and needs your approval in GitHub
Actions every time.

### 19.1 Before the first prod deploy
1. **Gmail:** stop the 7-day token expiry.
   1. Publish the Google app: **Google Auth Platform → Audience → Publish app**. Its status changes
      from **Testing** to **In production**.
   2. Get a fresh refresh token once (Part 3.4, Step 5) and put it in the app secret. Google's docs
      only say that apps in Testing get 7-day tokens. They don't say whether a token made before
      publishing keeps that limit, so make a new one to be safe.
   3. At sign-in, Google shows its **unverified app** screen, because reading Gmail is a restricted
      permission and the app isn't verified. Click **Continue** as in Part 3.4. Unverified apps are
      limited to 100 users, which is fine here: only the venue's own Gmail account signs in.
   4. A week later, check that Gmail polling and sending still work.
2. **InsForge:** turn **Disable New User Signups** on (Part 3.2).
3. **Decide how dev and prod share services.** Both environments would otherwise poll the same Gmail
   inbox every 8 minutes and use the same database, so both would answer the same customers. Choose
   one:
   - **Separate services (best):** a second InsForge project and a second Gmail account for dev.
     Put dev's new keys in `juniper-hollow-dev/app`, and run the one-time setup (Part 7) against the
     new InsForge project.
   - **Simplest:** destroy dev (Part 21) before switching prod on, and bring it back when you need it.
4. Check the evaluation and the live tests are passing on dev.

### 19.2 Deploy prod
1. Merge `dev` into `main` and push `main`.
2. Open **GitHub → Actions**. The **Deploy prod** run waits for approval. Click **Review deployments**,
   tick **prod**, and approve.
3. When it finishes, the end of the **Deploy** step's log prints the prod website and API addresses,
   as in Part 14.
4. The log warns that the prod secret is empty. Fill **`juniper-hollow-prod/app`** exactly as in
   Part 14.1.
5. **InsForge:** add the prod website's `/sign-in` and `/reset-password` addresses to the allowed
   redirect URLs (Part 3.2), so staff login and password reset work from prod.

### 19.3 Check prod
1. Repeat Part 16.1 and Part 16.2 on prod with your own test addresses.
2. Sign in to the prod portal as the staff admin and add the real staff, each with only the roles
   they need.
3. Check **CloudWatch → Log groups** for the three prod Lambdas: no repeating errors.
4. Check LangSmith shows the prod runs.
5. Set or check the **AWS budget alert** (Part 11.1) and your OpenRouter credit.

---

## Part 20. Run it day to day

### 20.1 Regular tasks
- **Staff:** check **Pending reviews** a few times a day; approve, edit or reject.
- **Knowledge:** when a policy changes, a knowledge manager uploads the new file in the portal, waits
  for **Ready to publish**, then publishes it. The agent uses the new version straight away. If it's
  wrong, **roll back**.
- **Staff accounts:** the staff admin adds people and deactivates those who leave. A deactivated
  account is locked out at once.
- **Data:** the daily job deletes conversation data older than 120 days. Drafts and audit logs are
  kept.

### 20.2 What to watch
1. **Failed jobs:** jobs that end as `failed` need a staff member to answer the customer by hand.
   The daily job also marks any job still "running" more than an hour after the Lambda limit as
   failed.
2. **The dead-letter queue** (`jobs_dead_letter_queue_url` in the Terraform outputs): messages SQS couldn't deliver
   after 10 tries. Inspect them and redrive by hand from the SQS console.
3. **Costs:** OpenRouter usage, and the AWS bill against the budget.
4. **Keys:** if the Gmail token or the OpenRouter key stops working, every job fails. Fix it as in
   Part 16.5.

### 20.3 Known limits and next steps
- **No alert on failure:** a failed job or a failed send doesn't notify staff yet. An email or
  portal alert is the next thing to build.
- **Pending root traces:** the worker should flush LangSmith before it returns, so the top-level run
  is marked complete.
- **Gmail filtering:** promotional and system emails can still become inquiries. Filter them before
  queuing.
- **Slow start:** the worker's first start after a quiet period takes more than 10 seconds because of
  heavy imports. It still works; jobs just start a little later.
- **Lambda concurrency:** new AWS accounts get 10 concurrent Lambdas in total, so the worker runs at
  most 2 jobs at once. Ask AWS for more in **Service Quotas** before real traffic grows.

---

## Part 21. Tear it down

1. Open **GitHub → Actions → Destroy → Run workflow**.
2. Pick the environment, type its name again in **confirm**, and run it. Prod needs your approval,
   like a deploy.
3. It removes that environment's Lambdas, API, queues, schedules, website, image repository,
   knowledge bucket and app secret.
   - **Dev** is removed completely, even with files and images left in it. Its secret is deleted at
     once.
   - **Prod** is protected: the destroy stops with an error while the knowledge bucket still has
     files or the image repository still has images, so nothing is lost by mistake. Its secret can be
     restored for 30 days after deletion.
4. From your own computer, `scripts/destroy.sh` or `scripts/destroy.ps1` does the same.
5. The destroy never touches the **bootstrap**: the Terraform state bucket and the GitHub deploy role
   stay. They cost almost nothing, and the state bucket is protected from deletion. Leave them unless
   you are finished with the project for good.
6. Outside AWS, nothing is deleted: the InsForge project, HubSpot contacts, Gmail messages, LangSmith
   traces and the OpenRouter key stay until you remove them yourself.
