// All entered details live only in the current document. The App bridge is
// used solely for host appearance, automatic sizing, and display modes.
const form = document.querySelector("#intake-form");
const review = document.querySelector("#review");
const reviewDetails = document.querySelector("#review-details");
const status = document.querySelector("#status");
const fields = {
  firstName: document.querySelector("#first-name"),
  lastName: document.querySelector("#last-name"),
  age: document.querySelector("#age"),
  description: document.querySelector("#description"),
};

function setError(field, message = "") {
  const error = document.querySelector(`#${field.id}-error`);
  error.textContent = message;
  error.hidden = !message;
  if (message) field.setAttribute("aria-invalid", "true");
  else field.removeAttribute("aria-invalid");
}

function wipeReview() {
  reviewDetails.replaceChildren();
  review.hidden = true;
}

function clearForm(announce = true, focus = true) {
  form.reset();
  for (const field of Object.values(fields)) {
    field.value = "";
    field.defaultValue = "";
    setError(field);
  }
  wipeReview();
  form.hidden = false;
  status.textContent = announce ? "Form cleared. No details were saved or sent." : "";
  if (focus) fields.firstName.focus();
}

function validate() {
  let firstInvalid = null;
  const check = (field, message) => {
    setError(field, message);
    if (message && !firstInvalid) firstInvalid = field;
  };
  for (const field of [fields.firstName, fields.lastName]) {
    const length = [...field.value.trim()].length;
    check(field, length < 1
      ? "Enter a name, not just spaces."
      : length > 100 ? "Use 100 characters or fewer, excluding surrounding spaces." : "");
  }
  const age = fields.age.value.trim();
  check(fields.age, age !== "" && (!/^\d+$/.test(age) || Number(age) > 150)
    ? "Enter a whole number from 0 to 150, or leave this blank." : "");
  check(fields.description, fields.description.value.length > 2000
    ? "Use 2,000 characters or fewer." : "");
  if (firstInvalid) {
    status.textContent = "Check the highlighted fields before reviewing.";
    firstInvalid.focus();
    return false;
  }
  return true;
}

function addReviewRow(label, value) {
  const row = document.createElement("div");
  row.className = "review-row";
  const term = document.createElement("dt");
  term.textContent = label;
  const details = document.createElement("dd");
  // Never interpret entered data as HTML or put it into attributes or URLs.
  details.textContent = value;
  row.append(term, details);
  reviewDetails.append(row);
}

// Bind local safeguards before loading the SDK. Even a failed CDN import or
// host connection cannot fall back to transmitting a conventional form.
form.addEventListener("submit", (event) => {
  event.preventDefault();
  event.stopPropagation();
  if (!validate()) return;
  wipeReview();
  addReviewRow("First name", fields.firstName.value.trim());
  addReviewRow("Last name", fields.lastName.value.trim());
  addReviewRow("Age", fields.age.value.trim() === "" ? "Not provided" : String(Number(fields.age.value.trim())));
  addReviewRow("Description", fields.description.value.trim() === "" ? "Not provided" : fields.description.value);
  form.hidden = true;
  review.hidden = false;
  status.textContent = "Review is shown only on this screen. Not saved or sent.";
  document.querySelector("#review-title").focus();
});

for (const field of Object.values(fields)) {
  field.addEventListener("input", () => {
    setError(field);
    status.textContent = "";
  });
}
document.querySelector("#edit-details").addEventListener("click", () => {
  wipeReview();
  form.hidden = false;
  status.textContent = "Editing locally. No details were saved or sent.";
  fields.firstName.focus();
});
for (const id of ["#clear-form", "#clear-review"]) {
  document.querySelector(id).addEventListener("click", () => clearForm());
}

// Do not retain browser-restored form state when this document is reopened.
clearForm(false, false);
window.addEventListener("pagehide", () => clearForm(false, false));
window.addEventListener("pageshow", (event) => {
  if (event.persisted) clearForm(false, false);
});

async function connectToHost() {
  try {
    const { App, applyDocumentTheme } = await import(
      "https://cdn.jsdelivr.net/npm/@modelcontextprotocol/ext-apps@2.0.0/dist/src/app-with-deps.js"
    );
    const app = new App(
      { name: "Customer intake", version: "1.0.0" },
      { availableDisplayModes: ["inline", "fullscreen"] },
    );
    const applyHostContext = (context) => {
      if (context?.theme) applyDocumentTheme(context.theme);
    };
    app.onhostcontextchanged = applyHostContext;
    // Intentionally ignore all invocation inputs and results: never prefill.
    app.ontoolinput = () => {};
    app.ontoolresult = () => {};
    app.onteardown = async () => {
      clearForm(false, false);
      return {};
    };
    await app.connect();
    applyHostContext(app.getHostContext());
  } catch {
    // Local interaction remains fully functional without the host bridge.
    // No diagnostics are logged, and no data is transmitted on failure.
  }
}
void connectToHost();
