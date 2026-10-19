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
    inquiryForm.addEventListener("submit", (event) => {
      event.preventDefault();

      const name = document.getElementById("inquiry-name").value.trim();
      const email = document.getElementById("inquiry-email").value.trim();
      const phone = document.getElementById("inquiry-phone").value.trim();
      const venue = document.getElementById("inquiry-venue").value;
      const guests = document.getElementById("inquiry-guests").value.trim();
      const datePref = document.getElementById("inquiry-date").value.trim();
      const message = document.getElementById("inquiry-message").value.trim();

      if (!name || !email || !message) {
        inquiryStatus.className = "status-banner visible error";
        inquiryStatus.textContent = "Please provide your name(s), email address, and message.";
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
    staffLoginForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const email = document.getElementById("staff-email").value.trim();
      const password = document.getElementById("staff-password").value;

      if (!email || !password) {
        staffLoginStatus.className = "status-banner visible error";
        staffLoginStatus.textContent = "Please enter both your work email and password.";
        return;
      }

      staffLoginStatus.className = "status-banner visible success";
      staffLoginStatus.innerHTML =
        "<strong>Welcome back, " + escapeHtml(email) + ".</strong><br>" +
        "Your credentials have been captured. Staff portal API connection will be enabled next.";
    });
  }
});

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
