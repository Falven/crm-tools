import { App } from '@modelcontextprotocol/ext-apps';

const app = new App({ name: 'Software request', version: '1.0.0' });
const form = document.getElementById('request-form');
const intakeFields = {
  requested_software: 'Requested software', business_objective: 'Business objective',
  capabilities: 'Capabilities', systems_and_constraints: 'Systems and constraints',
  decision_maker: 'Decision maker', preferred_deadline: 'Preferred deadline',
};
const requiredIntakeFields = ['requested_software', 'business_objective', 'capabilities', 'decision_maker'];
const intakeInputs = new Map(Object.keys(intakeFields).map(key => [key, document.getElementById(key.replaceAll('_', '-'))]));
const save = document.getElementById('save');
const submitRequest = document.getElementById('submit-request');
const submitRequestHint = document.getElementById('submit-request-hint');
const submit = document.getElementById('submit-answers');
const submitHint = document.getElementById('submit-hint');
const reload = document.getElementById('reload');
const message = document.getElementById('message');
const answerInputs = new Map();
let requestId = '';
let request = null;
let connected = false;
let busy = false;

function status(text, error = false) {
  message.textContent = text;
  message.dataset.error = String(error);
}

function answerValue(question, input) {
  if (input.value === '') return null;
  return question.type === 'yes_no' ? input.value === 'true' : input.value;
}

function unsavedAnswers() {
  return request.questions.some(question => {
    const saved = Object.hasOwn(request.answers, question.id) ? request.answers[question.id] : null;
    return answerValue(question, answerInputs.get(question.id)) !== (saved === '' ? null : saved);
  });
}

function changedIntakeFields() {
  return Object.fromEntries([...intakeInputs].filter(([key, input]) => input.value !== request[key])
    .map(([key, input]) => [key, input.value]));
}

function unsavedIntake() {
  return Object.keys(changedIntakeFields()).length > 0;
}

function incompleteIntake() {
  return request.missing_required_fields.length > 0
    || requiredIntakeFields.some(key => !request[key].trim());
}

function availability(working) {
  busy = working;
  form.setAttribute('aria-busy', String(working));
  const intake = request?.view === 'intake' && request.status === 'draft';
  const clarification = request?.view === 'clarification' && request.status === 'needs_clarification';
  const intakeDirty = intake && unsavedIntake();
  const intakeIncomplete = intake && incompleteIntake();
  save.hidden = !intake && !clarification;
  save.disabled = working || (!intake && !clarification) || (intake && !intakeDirty);
  for (const input of intakeInputs.values()) input.disabled = working || !intake;
  for (const input of answerInputs.values()) input.disabled = working || !clarification;
  submitRequest.hidden = !intake;
  submitRequestHint.hidden = !intake;
  submitRequest.disabled = working || !intake || intakeDirty || intakeIncomplete;
  if (intake) {
    submitRequestHint.textContent = intakeDirty ? 'Save your changes before submitting your request.'
      : intakeIncomplete ? 'Complete and save the four required fields before submitting your request.'
      : 'Your draft is saved. Select Submit request when you are ready to send it for review.';
  }
  submit.hidden = !clarification;
  submitHint.hidden = !clarification;
  const dirty = clarification && unsavedAnswers();
  const incomplete = clarification && request.missing_required_answers.length > 0;
  submit.disabled = working || !clarification || dirty || incomplete;
  if (clarification) {
    submitHint.textContent = dirty ? 'Save your changes before submitting.'
      : incomplete ? 'Complete and save the required answers before submitting.'
      : 'Your answers are saved. Submit when you are ready to send them for review.';
  }
  reload.disabled = working || !connected || !requestId;
}

function unpack(result) {
  if (result.isError) {
    const text = result.content?.filter(item => item.type === 'text').map(item => item.text).join(' ');
    throw new Error(text || 'The request could not be completed.');
  }
  const data = result.structuredContent;
  if (!data || !['request_id', 'title', 'business_objective', 'status', 'next_action', 'revision'].every(key => typeof data[key] === 'string')) {
    throw new Error('The saved request response is incomplete.');
  }
  return data;
}

function detail(list, label, value) {
  const term = document.createElement('dt');
  term.textContent = label;
  const description = document.createElement('dd');
  description.textContent = value;
  list.append(term, description);
}

function show(data) {
  if (!['intake', 'clarification', 'status'].includes(data.view) || !data.brief
      || !Object.keys(intakeFields).every(key => typeof data[key] === 'string')
      || !Array.isArray(data.missing_required_fields)
      || !Array.isArray(data.questions) || !data.answers || !Array.isArray(data.missing_required_answers)) {
    throw new Error('The saved request response is incomplete.');
  }
  request = data;
  document.getElementById('request-title').textContent = data.title || 'Software request';
  document.getElementById('request-id').textContent = data.request_id;
  const labels = { draft: 'Draft', submitted: 'Submitted', needs_clarification: 'Needs clarification', in_review: 'In review' };
  document.getElementById('request-status').textContent = '· ' + (labels[data.status] ?? data.status);
  document.getElementById('next-action').textContent = data.next_action;
  for (const [key, input] of intakeInputs) input.value = data[key];
  const missingFields = document.getElementById('missing-fields-list');
  missingFields.replaceChildren();
  for (const key of data.missing_required_fields) {
    const item = document.createElement('li');
    item.textContent = intakeFields[key] ?? key;
    missingFields.append(item);
  }
  document.getElementById('missing-fields').hidden = data.missing_required_fields.length === 0;
  document.getElementById('intake-view').hidden = data.view !== 'intake';
  document.getElementById('clarification-view').hidden = data.view !== 'clarification';
  document.getElementById('status-view').hidden = data.view !== 'status' || data.questions.length === 0;
  save.textContent = data.view === 'intake' ? 'Save draft' : 'Save answers';

  const brief = document.getElementById('brief');
  brief.replaceChildren();
  for (const [key, label] of Object.entries(intakeFields)) {
    if (data.brief[key]) detail(brief, label, data.brief[key]);
  }
  const savedBrief = document.getElementById('saved-brief');
  savedBrief.hidden = data.view === 'intake';
  savedBrief.open = data.view === 'status';

  answerInputs.clear();
  const questions = document.getElementById('questions');
  questions.replaceChildren();
  const savedAnswers = document.getElementById('saved-answers');
  savedAnswers.replaceChildren();
  for (const [index, question] of data.questions.entries()) {
    const answer = Object.hasOwn(data.answers, question.id) ? data.answers[question.id] : undefined;
    if (data.view === 'status') {
      detail(savedAnswers, question.prompt, answer === true ? 'Yes' : answer === false ? 'No' : answer || 'Not answered');
      continue;
    }
    if (data.view !== 'clarification') continue;
    const field = document.createElement('div');
    field.className = 'question';
    const label = document.createElement('label');
    label.htmlFor = 'question-' + index;
    label.textContent = question.prompt + (question.required ? ' (required)' : ' (optional)');
    const input = document.createElement(question.type === 'text' ? 'textarea' : 'select');
    input.id = label.htmlFor;
    input.required = question.required;
    input.setAttribute('aria-describedby', 'answers-hint');
    if (question.type === 'text') {
      input.rows = 3;
      input.maxLength = 4000;
    } else {
      const choices = question.type === 'yes_no' ? [['true', 'Yes'], ['false', 'No']]
        : question.choices.map(value => [value, value]);
      for (const [value, text] of [['', 'Choose an answer'], ...choices]) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = text;
        input.append(option);
      }
    }
    input.value = answer == null ? '' : String(answer);
    field.append(label, input);
    questions.append(field);
    answerInputs.set(question.id, input);
  }
  const missing = document.getElementById('missing-list');
  missing.replaceChildren();
  for (const question of data.questions.filter(item => data.missing_required_answers.includes(item.id))) {
    const item = document.createElement('li');
    item.textContent = question.prompt;
    missing.append(item);
  }
  document.getElementById('missing-answers').hidden = data.missing_required_answers.length === 0;
}

async function load() {
  if (busy || !connected || !requestId) return;
  availability(true);
  status('Loading the saved request…');
  try {
    show(unpack(await app.callServerTool({
      name: 'contoso_open_request',
      arguments: { request_id: requestId },
    })));
    status('Latest saved request loaded.');
  } catch (error) {
    request = null;
    status(error.message, true);
  } finally {
    availability(false);
  }
}

async function change(name, arguments_, success) {
  availability(true);
  status(name === 'contoso_submit_request' ? 'Submitting request…'
    : name === 'contoso_submit_answers' ? 'Submitting answers…' : 'Saving…');
  try {
    show(unpack(await app.callServerTool({ name, arguments: arguments_ })));
    status(success);
  } catch (error) {
    request = null;
    status(error.message + ' Reload the saved request before trying again.', true);
  } finally {
    availability(false);
  }
}

form.addEventListener('input', () => { if (!busy) availability(false); });
form.addEventListener('change', () => { if (!busy) availability(false); });
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || !request) return;
  const arguments_ = { request_id: request.request_id, revision: request.revision };
  if (request.view === 'intake' && request.status === 'draft') {
    const fields = changedIntakeFields();
    if (Object.keys(fields).length === 0) return;
    await change('contoso_save_request', { ...arguments_, ...fields }, 'Draft saved. Your request has not been submitted.');
  } else if (request.view === 'clarification' && request.status === 'needs_clarification') {
    const answers = Object.fromEntries(request.questions.map(question => [question.id, answerValue(question, answerInputs.get(question.id))]));
    await change('contoso_save_answers', { ...arguments_, answers, question_set_revision: request.question_set_revision }, 'Answers saved.');
  }
});

// Submission is a separate explicit click, never a form submit or save side effect.
submitRequest.addEventListener('click', async () => {
  if (busy || !request || request.view !== 'intake' || request.status !== 'draft'
      || unsavedIntake() || incompleteIntake()) return;
  await change('contoso_submit_request', {
    request_id: request.request_id, revision: request.revision,
  }, 'Request submitted for review.');
});

submit.addEventListener('click', async () => {
  if (busy || !request || request.view !== 'clarification' || request.status !== 'needs_clarification'
      || unsavedAnswers() || request.missing_required_answers.length > 0) return;
  await change('contoso_submit_answers', {
    request_id: request.request_id, revision: request.revision,
    question_set_revision: request.question_set_revision,
  }, 'Clarification submitted for review.');
});

reload.addEventListener('click', load);

app.ontoolresult = result => {
  if (requestId) return;
  try {
    // Retained result data is not current authority; use only its ID for a fresh read.
    requestId = unpack(result).request_id;
    if (connected) void load();
  } catch (error) {
    status(error.message, true);
  }
};

if (window.parent === window) {
  status('Open this form from a software request in your agent.', true);
} else {
  app.connect().then(() => {
    connected = true;
    availability(false);
    if (requestId) void load();
  }).catch(error => status('Could not connect to the agent. Reopen the form. ' + error.message, true));
}
