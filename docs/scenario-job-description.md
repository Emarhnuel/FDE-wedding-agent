# AI Integration Specialist — Wedding Inquiry & Retention Agent

> Fictional client brief for a Forward Deployed AI Engineering course, inspired by the Upwork posting. This course version uses HubSpot instead of Zoho.

## 1. Job Description

**Juniper Hollow Weddings & Events** is a small wedding venue company with two venues. Our team spends too much time answering repeated questions and rarely follows up with past couples.

We need an AI engineer to connect our shared Gmail inquiry inbox, website inquiry form, and HubSpot CRM. The system should answer routine questions automatically, involve staff when needed, and help us stay in touch with past customers.

### What you will build

- An inquiry agent that reads incoming messages and replies using approved company information.
- A **RAG knowledge base**: the agent looks up relevant FAQs, service details, and venue policies before writing an answer, rather than guessing.
- Tools to create or update HubSpot contacts, read customer history, save drafts, and send emails through Gmail.
- A pending-review list in the protected staff portal, so authorized staff can find and review messages that need approval.
- A login-protected staff portal: authorized reviewers approve emails, while designated knowledge managers upload or replace policy/FAQ files for RAG; the department head can hold both permissions. New document versions enter live retrieval only after indexing and checks succeed and an authorized manager publishes them.
- A daily retention job that drafts anniversary and referral messages for eligible past couples.

The engineer should be comfortable with LLM tool calling, RAG, email and CRM APIs, human approval, and testing. Deliver a working integration and a short handover for staff.

## 2. How the Inquiry Agent Runs

1. A customer emails the company's shared Gmail inbox or submits the website inquiry form.
2. Our integration detects the incoming email through the Gmail API. Website submissions go to our integration as well; they are not assumed to appear in the CRM automatically.
3. The integration creates or updates a **Contact in HubSpot** and records the inquiry. It checks for an existing customer before creating a new record.
4. The agent reads the message, checks relevant customer history, and retrieves approved information from the knowledge base.
5. Software checks decide whether the reply can be sent automatically or needs staff review.
6. The reply is sent from the company's Gmail inbox, and the outcome is recorded against the HubSpot contact. Later customer replies trigger the same process.

This agent reacts to incoming inquiries; it does not search the internet for leads.

## 3. When Staff Get Involved

**Send automatically:** routine questions about services, opening hours, venue capacity, and FAQs, when the answer is supported by current approved information.

**Require staff review:** pricing, quotes, discounts, booking or date-hold promises, contracts, cancellations, refunds, and complaints. If a message mixes routine questions with any of these topics, hold the whole reply for review.

**Hand off without an automatic answer:** missing or conflicting information, sensitive situations, failed safety checks, or system errors.

Only authenticated staff with the `email_approver` role review messages. They open the pending-review list in the protected staff portal. The portal shows the original inquiry, proposed reply, supporting information, and reason for review. Staff can **Approve & Send**, **Edit**, or **Reject**. Approval applies only to the exact message reviewed; edits must be approved before sending.

These controls must be enforced by the software, not only by instructions to the AI. Instructions inside customer messages must not override company rules. Failed processing must alert staff, and repeated events must not send duplicate replies.

## 4. How the Retention Job Runs

Once a day, a separate scheduled job checks past customers in HubSpot for upcoming wedding anniversaries and agreed referral opportunities.

It uses approved details such as the couple's names, wedding date, and venue to draft a personal message. **All retention messages require staff approval before sending.**

Skip contacts without marketing consent, those marked do not contact, those with relationship-change flags or open complaints, bounced addresses, and anyone contacted within the agreed cooldown period. Keep these flags in structured fields, not only free-text notes.

## 5. Course Setup and Success Checks

Use **HubSpot Free** with a small sample dataset: up to **2 users and 1,000 contacts**. Use Contacts rather than HubSpot's separate Leads object.

The AI service, draft database, knowledge base, and daily scheduler run outside HubSpot. This avoids relying on paid CRM workflows or custom objects. AI usage and hosting costs are separate from the free CRM.

Before enabling automatic replies, test that routine answers match approved sources, restricted questions always reach staff, duplicate events do not send twice, and retention exclusions are respected. Begin with all replies reviewed by staff, then enable automatic replies only for tested routine topics.
