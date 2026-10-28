import { App, applyDocumentTheme } from '@modelcontextprotocol/ext-apps';

const form = document.querySelector('#customer-form');
const fields = [...form.querySelectorAll('input[name], textarea[name]')];
// This object belongs only to this document. No persistence or customer-data RPC.
const values = Object.fromEntries(fields.map(field => [field.name, '']));
const review = document.querySelector('#review');
const details = document.querySelector('#details');
const status = document.querySelector('#status');
const reviewButton = document.querySelector('#review-button');
const clearButton = document.querySelector('#clear-button');
const confirmation = document.querySelector('#clear-confirmation');
const cancelClear = document.querySelector('#cancel-clear');

const syncValues = () => {
  for (const field of fields) values[field.name] = field.value;
};
const renderDetails = () => {
  const rows = fields.map(field => {
    const row = document.createElement('div');
    row.className = 'detail';
    const label = document.createElement('dt');
    label.textContent = field.labels[0].textContent;
    const value = document.createElement('dd');
    // User content is always text, never HTML.
    value.textContent = values[field.name] === '' ? 'Not provided' : values[field.name];
    row.append(label, value);
    return row;
  });
  details.replaceChildren(...rows);
};
const hideConfirmation = () => {
  confirmation.hidden = true;
  clearButton.setAttribute('aria-expanded', 'false');
};
const hideReview = () => {
  review.hidden = true;
  reviewButton.setAttribute('aria-expanded', 'false');
  details.replaceChildren();
};
const clearValues = () => {
  form.reset();
  for (const field of fields) {
    field.value = '';
    values[field.name] = '';
  }
  hideReview();
  hideConfirmation();
};
const onEdit = () => {
  syncValues();
  hideConfirmation();
  status.textContent = '';
  if (!review.hidden) renderDetails();
};
form.addEventListener('input', onEdit);
form.addEventListener('change', onEdit);
form.addEventListener('submit', event => {
  event.preventDefault();
  syncValues();
  hideConfirmation();
  renderDetails();
  review.hidden = false;
  reviewButton.setAttribute('aria-expanded', 'true');
  status.textContent = '';
  document.querySelector('#review-heading').focus();
});
clearButton.addEventListener('click', () => {
  syncValues();
  if (fields.every(field => values[field.name] === '')) {
    status.textContent = 'The form is already empty.';
    return;
  }
  confirmation.hidden = false;
  clearButton.setAttribute('aria-expanded', 'true');
  cancelClear.focus();
});
cancelClear.addEventListener('click', () => {
  hideConfirmation();
  clearButton.focus();
});
document.querySelector('#confirm-clear').addEventListener('click', () => {
  clearValues();
  status.textContent = 'All fields cleared.';
  fields[0].focus();
});
document.querySelector('#back-button').addEventListener('click', () => {
  hideReview();
  fields[0].focus();
});
confirmation.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    hideConfirmation();
    clearButton.focus();
  }
});
// Also discard values when a document enters the browser's back/forward cache.
window.addEventListener('pagehide', clearValues);
window.addEventListener('pageshow', event => {
  if (event.persisted) clearValues();
});

const app = new App(
  { name: 'Customer information', version: '1.0.0' },
  { availableDisplayModes: ['inline', 'fullscreen'] },
);
const applyTheme = context => {
  if (context?.theme) applyDocumentTheme(context.theme);
};
app.onhostcontextchanged = applyTheme;
// The bridge carries appearance and size only; never publish the values object.
try {
  await app.connect();
  applyTheme(app.getHostContext());
} catch {
  // The local form remains usable in non-App clients and offline previews.
  status.textContent = 'Standalone form · values still stay in memory only.';
}
