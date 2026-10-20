document.addEventListener("DOMContentLoaded", () => {
  // 1. FAQ Accordion interactions
  const faqTriggers = document.querySelectorAll(".faq-trigger");
  faqTriggers.forEach((btn) => {
    btn.addEventListener("click", () => {
      const item = btn.closest(".faq-item");
      if (!item) return;
      const isOpen = item.classList.contains("open");
      document.querySelectorAll(".faq-item.open").forEach((el) => {
        el.classList.remove("open");
      });
      if (!isOpen) {
        item.classList.add("open");
      }
    });
  });

  // 2. Pre-select venue on Contact Us page if ?venue= query param is present
  const params = new URLSearchParams(window.location.search);
  const preselectedVenue = params.get("venue");
  const venueSelect = document.getElementById("inquiry-venue");
  if (venueSelect && preselectedVenue) {
    for (const option of venueSelect.options) {
      if (option.value.toLowerCase() === preselectedVenue.toLowerCase()) {
        venueSelect.value = option.value;
        break;
      }
    }
  }

  // 3. Contact Us Inquiry Form
  const inquiryForm = document.getElementById("wedding-inquiry-form");
  const inquiryStatus = document.getElementById("inquiry-status");

  if (inquiryForm && inquiryStatus) {
    inquiryForm.addEventListener("submit", async (event) => {
      event.preventDefault();

      const name = document.getElementById("inquiry-name").value.trim();
      const email = document.getElementById("inquiry-email").value.trim();
      const phone = document.getElementById("inquiry-phone").value.trim();
      const venue = document.getElementById("inquiry-venue").value;
      const guests = document.getElementById("inquiry-guests").value.trim();
      const datePref = document.getElementById("inquiry-date").value.trim();
      const message = document.getElementById("inquiry-message").value.trim();

      if (!name || !email || !message) {
        showStatus(inquiryStatus, "error", "Please provide your name(s), email address, and message.");
        return;
      }

      const submit = inquiryForm.querySelector('button[type="submit"]');
      try {
        // POST /inquiries queues the inquiry for the agent and answers 202 straight away.
        await whileBusy(submit, "Sending\u2026", () =>
          api("/inquiries", {
            method: "POST",
            json: {
              name,
              email,
              phone,
              venue,
              guest_count: guests ? Number(guests) : null,
              preferred_date: datePref,
              message,
            },
          })
        );
      } catch (error) {
        showStatus(inquiryStatus, "error", "We couldn't send your inquiry. " + error.message);
        return;
      }

      inquiryStatus.className = "status-banner visible success";
      inquiryStatus.innerHTML =
        "<strong>Thank you, " + escapeHtml(name) + ".</strong><br>" +
        "We have received your inquiry regarding <em>" +
        escapeHtml(venue || "Juniper Hollow") + "</em>" +
        (guests ? " (" + escapeHtml(guests) + " guests)" : "") +
        (datePref ? " for " + escapeHtml(datePref) : "") +
        ". Our hospitality team will follow up at <strong>" +
        escapeHtml(email) + "</strong>" +
        (phone ? " (" + escapeHtml(phone) + ")" : "") + ".";

      inquiryForm.reset();
    });
  }

  // 4. Staff Login Page: "Are you a staff member?" Gate
  const staffGatePanel = document.getElementById("staff-gate-panel");
  const staffLoginPanel = document.getElementById("staff-login-panel");
  const btnStaffYes = document.getElementById("btn-staff-yes");
  const btnStaffNo = document.getElementById("btn-staff-no");
  const btnBackToGate = document.getElementById("btn-back-to-gate");
  const staffLoginForm = document.getElementById("staff-login-form");
  const staffLoginStatus = document.getElementById("staff-login-status");

  if (btnStaffNo) {
    btnStaffNo.addEventListener("click", () => {
      window.location.href = "index.html";
    });
  }

  if (btnStaffYes && staffGatePanel && staffLoginPanel) {
    btnStaffYes.addEventListener("click", () => {
      staffGatePanel.classList.add("hidden-panel");
      staffLoginPanel.classList.remove("hidden-panel");
      const emailInput = document.getElementById("staff-email");
      if (emailInput) emailInput.focus();
    });
  }

  if (btnBackToGate && staffGatePanel && staffLoginPanel) {
    btnBackToGate.addEventListener("click", () => {
      staffLoginPanel.classList.add("hidden-panel");
      staffGatePanel.classList.remove("hidden-panel");
      if (staffLoginStatus) {
        staffLoginStatus.className = "status-banner";
        staffLoginStatus.textContent = "";
      }
    });
  }

  if (staffLoginForm && staffLoginStatus) {
    staffLoginForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const email = document.getElementById("staff-email").value.trim();
      const password = document.getElementById("staff-password").value;

      if (!email || !password) {
        showStatus(staffLoginStatus, "error", "Please enter both your work email and password.");
        return;
      }

      try {
        // POST /auth/login is an OAuth2 password form: the email goes in "username".
        const submit = staffLoginForm.querySelector('button[type="submit"]');
        const data = await whileBusy(submit, "Signing in\u2026", () =>
          api("/auth/login", { method: "POST", form: { username: email, password } })
        );
        sessionStorage.setItem(TOKEN_KEY, data.access_token);
        window.location.href = "portal.html";
      } catch (error) {
        showStatus(staffLoginStatus, "error", error.message);
      }
    });
  }

  // 5. Staff Portal
  if (document.getElementById("portal-welcome")) {
    initPortal();
  }

  // 6. Change / Forgot Password
  if (document.getElementById("password-code-form")) {
    initPasswordPage();
  }
});

// =============================================================================
// Backend API
// =============================================================================

const TOKEN_KEY = "jhStaffToken";

// Calls the FastAPI backend. Throws an Error whose message is safe to show, with .status set.
async function api(path, { method = "GET", json, form } = {}) {
  const headers = {};
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = "Bearer " + token;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = new URLSearchParams(form);
  }

  let response;
  try {
    response = await fetch(window.API_BASE_URL + path, { method, headers, body });
  } catch {
    throw new Error("Can't reach the server. Please check that the backend is running and try again.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    // FastAPI sends a string detail, or a list of validation errors for bad input (422).
    const detail = Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join(" ") : data.detail;
    const error = new Error(detail || "Something went wrong (" + response.status + "). Please try again.");
    error.status = response.status;
    throw error;
  }
  return data;
}

function showStatus(element, type, message) {
  element.className = "status-banner visible " + type;
  element.textContent = message;
}

// Shows a working label and disables the button the moment it is clicked, so every click gets
// instant feedback even while the server works. Returns the task's result.
async function whileBusy(button, label, task) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await task();
  } finally {
    button.textContent = original;
    button.disabled = false;
  }
}

function signOut() {
  sessionStorage.removeItem(TOKEN_KEY);
  window.location.href = "login.html";
}

// =============================================================================
// Staff Portal
// =============================================================================

const ROLE_LABELS = {
  email_approver: "Email approver",
  knowledge_manager: "Knowledge manager",
  staff_admin: "Staff admin",
};

function roleNames(roles) {
  return roles.map((role) => ROLE_LABELS[role] || role).join(", ");
}

async function initPortal() {
  if (!sessionStorage.getItem(TOKEN_KEY)) {
    window.location.href = "login.html";
    return;
  }
  document.getElementById("btn-logout").addEventListener("click", signOut);

  let me;
  try {
    me = await api("/me");
  } catch (error) {
    if (error.status === 401) return signOut(); // Expired or invalid token: sign in again.
    document.getElementById("portal-welcome").textContent = "We couldn't open your portal.";
    showStatus(document.getElementById("portal-status"), "error", error.message);
    return;
  }

  document.getElementById("portal-welcome").textContent = "Welcome, " + (me.full_name || me.email) + ".";
  document.getElementById("portal-details").textContent = me.email + " · " + roleNames(me.roles);
  document.getElementById("link-change-password").href = "password.html?email=" + encodeURIComponent(me.email);

  if (me.roles.includes("email_approver")) {
    document.getElementById("reviews-section").classList.remove("hidden-panel");
    initReviews();
  }
  if (me.roles.includes("staff_admin")) {
    document.getElementById("staff-admin-section").classList.remove("hidden-panel");
    initStaffAdmin(me);
  }
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

// =============================================================================
// Staff Portal: Pending Reviews (human-in-the-loop approve / edit / reject)
// =============================================================================

// The agent tool calls that pause for staff (interrupt_on in src/agents.py).
const ACTION_LABELS = {
  save_inquiry_draft: "Approve the reply draft",
  save_retention_draft: "Approve the retention draft",
  send_customer_email: "Approve sending the reply",
  send_retention_email: "Approve sending the retention email",
};

function initReviews() {
  const list = document.getElementById("review-list");
  const detail = document.getElementById("review-detail");
  const status = document.getElementById("reviews-status");
  const subjectInput = document.getElementById("review-subject");
  const bodyInput = document.getElementById("review-body");
  const rejectNote = document.getElementById("review-reject-note");
  const approveButton = document.getElementById("btn-approve");
  const approveEditsButton = document.getElementById("btn-approve-edits");
  const rejectButton = document.getElementById("btn-reject");
  let current = null; // The review being viewed (from the list, then completed by GET /reviews/{id}).

  function handleError(error) {
    if (error.status === 401) return signOut();
    showStatus(status, "error", error.message);
  }

  function requestSummary(request) {
    if (!request) return "Unknown request";
    if (request.contact_id) return "Past couple · " + (request.occasion || "retention") + " outreach";
    return request.name ? request.name + " (" + request.email + ")" : request.email || "Customer";
  }

  async function loadReviews() {
    detail.classList.add("hidden-panel");
    list.classList.remove("hidden-panel");
    current = null;
    list.replaceChildren(element("p", "Loading reviews…", "choice-desc"));
    try {
      const reviews = await api("/reviews");
      list.replaceChildren(
        ...(reviews.length
          ? reviews.map(reviewItem)
          : [element("p", "No reviews are waiting. New ones appear here when the agent pauses.", "choice-desc")])
      );
    } catch (error) {
      handleError(error);
    }
  }

  function reviewItem(review) {
    const request = review.original_request || {};
    const item = element("div", undefined, "review-item");
    const text = element("div", undefined, "review-item-text");
    text.append(
      element("div", requestSummary(review.original_request), "review-item-title"),
      element(
        "div",
        (ACTION_LABELS[review.action_name] || review.action_name) +
          " · " + new Date(review.created_at).toLocaleString() +
          (request.message ? " · “" + request.message + "”" : ""),
        "review-item-sub"
      )
    );
    const open = element("button", "Open", "btn btn-outline");
    open.type = "button";
    open.addEventListener("click", () => openReview(review));
    item.append(text, open);
    return item;
  }

  function fillList(listElement, items, emptyText) {
    listElement.replaceChildren(...(items.length ? items : [emptyText]).map((text) => element("li", text)));
  }

  function sourceText(source) {
    if (typeof source !== "object" || source === null) return String(source);
    return [source.source_id, source.version && "v" + source.version, source.location, source.venue]
      .filter(Boolean)
      .join(" · ");
  }

  // Shows the review at once from the list data; the stored draft's reasons and sources follow.
  async function openReview(review) {
    status.className = "status-banner";
    current = review;
    const args = current.action_args || {};
    const request = current.original_request || {};
    const isSend = current.action_name.startsWith("send_");

    document.getElementById("review-title").textContent = ACTION_LABELS[current.action_name] || current.action_name;
    document.getElementById("review-meta").textContent =
      "The agent paused here on " + new Date(current.created_at).toLocaleString() + ".";

    // The customer's original request (website form, Gmail, or the daily retention job).
    const facts = request.contact_id
      ? [["Couple contact ID", request.contact_id], ["Occasion", request.occasion]]
      : [
          ["From", requestSummary(request)],
          ["Received via", request.source === "gmail" ? "Gmail" : "Website form"],
          ["Phone", request.phone],
          ["Email subject", request.subject],
          ["Preferred venue", request.venue],
          ["Guest count", request.guest_count],
          ["Preferred date", request.preferred_date],
        ];
    const factList = document.getElementById("review-request");
    factList.replaceChildren();
    for (const [label, value] of facts) {
      if (value) factList.append(element("dt", label), element("dd", String(value)));
    }
    const message = document.getElementById("review-request-message");
    message.textContent = request.message || "";
    message.classList.toggle("hidden-panel", !request.message);

    // A draft step carries its reasons and sources in the args; a send step's are on the stored draft.
    const reasonsList = document.getElementById("review-reasons");
    const sourcesList = document.getElementById("review-sources");
    const showDetails = (reasons, sources) => {
      fillList(reasonsList, reasons, "No specific reason was recorded.");
      fillList(sourcesList, sources.map(sourceText), "No sources were cited.");
    };
    if (review.draft_id) {
      fillList(reasonsList, [], "Loading…");
      fillList(sourcesList, [], "Loading…");
    } else {
      showDetails(args.review_reasons || [], args.sources || []);
    }

    document.getElementById("review-recipient").textContent = "To: " + (args.recipient || "unknown");
    subjectInput.value = args.subject || "";
    bodyInput.value = args.body || "";
    rejectNote.value = "";
    approveButton.textContent = isSend ? "Approve & Send" : "Approve Draft";
    updateButtons();
    setButtonsDisabled(false);

    list.classList.add("hidden-panel");
    detail.classList.remove("hidden-panel");
    detail.scrollIntoView({ behavior: "smooth", block: "start" });

    if (!review.draft_id) return;
    try {
      const draft = (await api("/reviews/" + review.id)).draft || {};
      if (current !== review) return; // The reviewer moved on to another review meanwhile.
      showDetails(draft.review_reasons || [], draft.sources || []);
    } catch (error) {
      if (current === review) handleError(error);
    }
  }

  function edits() {
    const args = current.action_args || {};
    const changes = {};
    if (subjectInput.value !== (args.subject || "")) changes.subject = subjectInput.value.trim();
    if (bodyInput.value !== (args.body || "")) changes.body = bodyInput.value.trim();
    return changes;
  }

  // Approving as written and approving with edits are separate buttons, so edits are never lost.
  function updateButtons() {
    const edited = Object.keys(edits()).length > 0;
    approveButton.disabled = edited;
    approveEditsButton.disabled = !edited;
  }

  function setButtonsDisabled(disabled) {
    for (const button of [approveButton, approveEditsButton, rejectButton]) button.disabled = disabled;
    if (!disabled) updateButtons();
  }

  async function decide(button, workingLabel, payload, successMessage) {
    const label = button.textContent;
    setButtonsDisabled(true);
    button.textContent = workingLabel;
    try {
      await api("/reviews/" + current.id + "/decision", { method: "POST", json: payload });
      showStatus(status, "success", successMessage);
      await loadReviews();
    } catch (error) {
      if (error.status === 409) {
        // Someone else decided it first, or the draft changed: show the fresh list.
        showStatus(status, "error", error.message);
        return loadReviews();
      }
      handleError(error);
      setButtonsDisabled(false);
    } finally {
      button.textContent = label;
    }
  }

  function approvedMessage() {
    return current.action_name.startsWith("send_")
      ? "Approved. The agent is resuming and will send the email shortly."
      : "Approved. The agent is resuming; the email will come back here for a final send approval.";
  }

  subjectInput.addEventListener("input", updateButtons);
  bodyInput.addEventListener("input", updateButtons);

  approveButton.addEventListener("click", () =>
    decide(approveButton, "Approving…", { decision: "approve" }, approvedMessage())
  );

  approveEditsButton.addEventListener("click", () => {
    const changes = edits();
    if (changes.subject === "" || changes.body === "") {
      showStatus(status, "error", "The subject and body can't be empty.");
      return;
    }
    decide(approveEditsButton, "Approving…", { decision: "edit", ...changes }, approvedMessage().replace("Approved.", "Approved with your edits."));
  });

  rejectButton.addEventListener("click", () => {
    if (!window.confirm("Reject this email? It will not be sent.")) return;
    const note = rejectNote.value.trim();
    decide(rejectButton, "Rejecting…", { decision: "reject", ...(note ? { message: note } : {}) }, "Rejected. This email will not be sent.");
  });

  document.getElementById("btn-back-to-reviews").addEventListener("click", () => {
    status.className = "status-banner";
    loadReviews();
  });
  const refreshButton = document.getElementById("btn-refresh-reviews");
  refreshButton.addEventListener("click", () => {
    status.className = "status-banner";
    whileBusy(refreshButton, "Refreshing…", loadReviews);
  });

  loadReviews();
}

function initStaffAdmin(me) {
  const form = document.getElementById("add-staff-form");
  const formStatus = document.getElementById("add-staff-status");
  const tableBody = document.getElementById("staff-table-body");
  const listStatus = document.getElementById("staff-list-status");

  async function loadStaff() {
    try {
      const members = await api("/staff");
      tableBody.replaceChildren(...members.map(staffRow));
      listStatus.className = "status-banner";
    } catch (error) {
      if (error.status === 401) return signOut();
      showStatus(listStatus, "error", error.message);
    }
  }

  function staffRow(member) {
    const row = document.createElement("tr");
    for (const text of [member.full_name || "", member.email, roleNames(member.roles), member.is_active ? "Active" : "Deactivated"]) {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.appendChild(cell);
    }
    const actionCell = document.createElement("td");
    // The backend also refuses this, so an admin can never lock themselves out.
    if (member.id !== me.staff_id) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-ghost";
      button.textContent = member.is_active ? "Deactivate" : "Reactivate";
      button.addEventListener("click", async () => {
        try {
          await whileBusy(button, "Saving…", () =>
            api("/staff/" + member.id, { method: "PATCH", json: { is_active: !member.is_active } })
          );
          await loadStaff();
        } catch (error) {
          if (error.status === 401) return signOut();
          showStatus(listStatus, "error", error.message);
        }
      });
      actionCell.appendChild(button);
    }
    row.appendChild(actionCell);
    return row;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fullName = document.getElementById("staff-full-name").value.trim();
    const email = document.getElementById("staff-new-email").value.trim();
    const password = document.getElementById("staff-new-password").value;
    const roles = [...form.querySelectorAll('input[name="role"]:checked')].map((box) => box.value);

    if (!fullName || !email || !password || roles.length === 0) {
      showStatus(formStatus, "error", "Please fill in the name, email, starting password and at least one role.");
      return;
    }

    const submit = form.querySelector('button[type="submit"]');
    try {
      const member = await whileBusy(submit, "Adding…", () =>
        api("/staff", { method: "POST", json: { full_name: fullName, email, password, roles } })
      );
      showStatus(
        formStatus,
        "success",
        "Added " + member.full_name + ". Share their email and starting password with them; they can change the password after signing in."
      );
      form.reset();
      await loadStaff();
    } catch (error) {
      if (error.status === 401) return signOut();
      showStatus(formStatus, "error", error.message);
    }
  });

  loadStaff();
}

// =============================================================================
// Change / Forgot Password (InsForge emails a 6-digit code)
// =============================================================================

function initPasswordPage() {
  const codeForm = document.getElementById("password-code-form");
  const resetForm = document.getElementById("password-reset-form");
  const status = document.getElementById("password-status");
  const emailInput = document.getElementById("password-email");

  const presetEmail = new URLSearchParams(window.location.search).get("email");
  if (presetEmail) emailInput.value = presetEmail;

  codeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = emailInput.value.trim();
    if (!email) {
      showStatus(status, "error", "Please enter your work email.");
      return;
    }
    try {
      const submit = codeForm.querySelector('button[type="submit"]');
      const data = await whileBusy(submit, "Sending code…", () =>
        api("/auth/password/code", { method: "POST", json: { email } })
      );
      showStatus(status, "success", data.detail);
      codeForm.classList.add("hidden-panel");
      resetForm.classList.remove("hidden-panel");
      document.getElementById("password-code").focus();
    } catch (error) {
      showStatus(status, "error", error.message);
    }
  });

  resetForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = document.getElementById("password-code").value.trim();
    const newPassword = document.getElementById("password-new").value;
    const confirm = document.getElementById("password-confirm").value;

    if (!/^\d{6}$/.test(code)) {
      showStatus(status, "error", "The code is the 6 digits from the email.");
      return;
    }
    if (!newPassword || newPassword !== confirm) {
      showStatus(status, "error", "The new passwords don't match.");
      return;
    }
    try {
      const submit = resetForm.querySelector('button[type="submit"]');
      const data = await whileBusy(submit, "Changing password…", () =>
        api("/auth/password/reset", {
          method: "POST",
          json: { email: emailInput.value.trim(), code, new_password: newPassword },
        })
      );
      // Sign out so the next sign-in uses the new password.
      sessionStorage.removeItem(TOKEN_KEY);
      resetForm.classList.add("hidden-panel");
      status.className = "status-banner visible success";
      status.textContent = data.detail + " ";
      const link = document.createElement("a");
      link.href = "login.html";
      link.textContent = "Go to Staff Login →";
      link.style.fontWeight = "600";
      status.appendChild(link);
    } catch (error) {
      showStatus(status, "error", error.message);
    }
  });
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
