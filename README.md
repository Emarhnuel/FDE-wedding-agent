# 💍 Juniper Hollow Agent

> The wedding industry's most overqualified email answerer. Replies to "do you have Wi-Fi?" in seconds — but politely refuses to quote $40,000 without asking a human first.

A **Forward Deployed AI Engineering** course project: turning a vague Upwork brief — *"AI Integration Specialist – CRM conversational agent"* — into a production-style platform for **Juniper Hollow Weddings & Events**, a fictional (but very demanding) venue.

## 🤵 What it does

| Feature | The deal |
|---|---|
| 📬 **Inquiry agent** | Answers routine questions instantly using approved venue knowledge |
| ✋ **Human-in-the-loop** | Pricing, date holds, refunds, contracts, complaints → always escalated to staff |
| 🧠 **Agentic RAG** | The agent decides *when* to search ChromaDB — Google `gemini-embedding-2` via OpenRouter |
| 💌 **Retention agent** | Re-engages past couples for referrals & anniversaries (consent-gated) |
| 🔐 **Staff auth** | InsForge login + role-based review workflows |
| 🕵️ **Observability** | LangSmith tracing & evaluation on every run |

## 🏗️ Stack

**Agents:** Deep Agents (`create_deep_agent`) — supervisor + inquiry + retention subagents  
**Retrieval:** ChromaDB · Gemini Embedding 2 via OpenRouter · published-source filtering  
**Comms:** Gmail API · HubSpot CRM  
**Infra:** AWS Bedrock AgentCore · Lambda · API Gateway · SQS · EventBridge Scheduler · PostgreSQL · S3 · Secrets Manager — all Terraform  
**Frontend:** Inquiry form + staff portal on AWS Amplify Hosting (default AWS domain, no CloudFront)  
**CI/CD:** GitHub Actions with OIDC — no AWS keys stored in GitHub  
**Auth:** InsForge

## 📁 Layout

```
src/
├── agents.py      # Deep Agents: supervisor + subagents, memory & approval wiring
├── prompts.py     # XML-structured system prompts (the agents' personalities)
├── rag.py         # load → split → embed → Chroma → search
├── api.py         # FastAPI entry: inquiry form + staff approval endpoints
├── worker.py      # SQS consumer → AgentCore
├── runtime.py     # AgentCore runtime entrypoint
├── cron.py        # Gmail polling, retention sweeps, 120-day cleanup
├── config.py      # env-driven settings
├── schemas.py     # AgentContext & friends
├── tools/         # agent-facing tools (knowledge search, CRM, outreach)
└── security/      # token verification + permissions
```

## 🚀 Quick start

```bash
uv sync   # install pinned deps (uv.lock)
```

Then create a `.env` — see `src/config.py` for the full key list. `OPENROUTER_API_KEY` is the one you can't skip.

## ☁️ Deployment

Three environments — **dev**, **test** and **prod** — each a Terraform workspace with its own remote state in S3.

- Push to `main` → GitHub Actions deploys **dev**
- **test** and **prod** → run the deploy workflow by hand
- Tearing down an environment is a manual workflow that asks you to type its name first
- The same deploy scripts work locally (PowerShell or bash) and in CI

The backend and frontend ship together: one run builds the images, applies Terraform, then builds and uploads the frontend.

## ⚠️ Status

Agents, tools, API, worker, runtime and cron are built and tested against live services. Deployment files are scaffolded but empty; document uploads and the frontend come next. Design docs & course plan live locally (kept out of the repo on purpose).

*No weddings were harmed in the making of this codebase.* 🎩
