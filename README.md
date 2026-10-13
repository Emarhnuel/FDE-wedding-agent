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
**Infra:** AWS Bedrock AgentCore · Lambda · API Gateway · SQS · EventBridge · RDS · S3 · CloudFront — all Terraform  
**Auth:** InsForge

## 📁 Layout

```
src/
├── agents.py      # Deep Agents: supervisor + subagents, memory & approval wiring
├── prompts.py     # XML-structured system prompts (the agents' personalities)
├── rag.py         # load → split → embed → Chroma → search
├── api.py         # FastAPI entry: webhooks + staff approval endpoints
├── worker.py      # SQS consumer → AgentCore
├── runtime.py     # AgentCore runtime entrypoint
├── cron.py        # scheduled retention sweeps
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

## ⚠️ Status

Early build phase — agents not yet wired, integrations stubbed. Design docs & course plan live locally (kept out of the repo on purpose).

*No weddings were harmed in the making of this codebase.* 🎩
