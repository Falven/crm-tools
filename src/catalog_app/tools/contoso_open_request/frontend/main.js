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
const submitRequest = document.getElementById('submit-request');
const submitRequestHint = document.getElementById('submit-request-hint');
const submit = document.getElementById('submit-answers');
const submitHint = document.getElementById('submit-hint');
const reload = document.getElementById('reload');
const message = document.getElementById('message');
const answerInputs = new Map();
let initialResult = null;
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

function missingIntakeFields() {
  return requiredIntakeFields.filter(key => !intakeInputs.get(key).value.trim());
}

function missingAnswers() {
  return request.questions.filter(question => {
    const value = answerValue(question, answerInputs.get(question.id));
    return question.required && (value === null || typeof value === 'string' && !value.trim());
  });
}

function showMissing(sectionId, listId, labels) {
  const list = document.getElementById(listId);
  list.replaceChildren();
  for (const label of labels) {
    const item = document.createElement('li');
    item.textContent = label;
    list.append(item);
  }
  document.getElementById(sectionId).hidden = labels.length === 0;
}

function availability(working) {
  busy = working;
  form.setAttribute('aria-busy', String(working));
  const review = request?.view === 'review';
  const clarification = request?.view === 'clarification' && request.status === 'needs_clarification';
  for (const input of intakeInputs.values()) input.disabled = working || !review;
  for (const input of answerInputs.values()) input.disabled = working || !clarification;
  submitRequest.hidden = !review;
  submitRequestHint.hidden = !review;
  const missingFields = review ? missingIntakeFields() : [];
  submitRequest.disabled = working || !connected || !review || missingFields.length > 0;
  if (review) {
    showMissing('missing-fields', 'missing-fields-list', missingFields.map(key => intakeFields[key]));
    submitRequestHint.textContent = missingFields.length
      ? 'Complete the four required fields before submitting.'
      : 'Review your details, then submit when you are ready to send them for review.';
  }
  submit.hidden = !clarification;
  submitHint.hidden = !clarification;
  const missingQuestions = clarification ? missingAnswers() : [];
  submit.disabled = working || !connected || !clarification || missingQuestions.length > 0;
  if (clarification) {
    showMissing('missing-answers', 'missing-list', missingQuestions.map(question => question.prompt));
    submitHint.textContent = missingQuestions.length
      ? 'Complete the required answers before submitting.'
      : 'Submit answers saves your answers and sends them for review.';
  }
  reload.hidden = !requestId;
  reload.disabled = working || !connected || !requestId;
}

function unpack(result) {
  if (result.isError) {
    const text = result.content?.filter(item => item.type === 'text').map(item => item.text).join(' ');
    throw new Error(text || 'The request could not be completed.');
  }
  if (!result.structuredContent) throw new Error('The request response is incomplete.');
  return result.structuredContent;
}

function detail(list, label, value) {
  const term = document.createElement('dt');
  term.textContent = label;
  const description = document.createElement('dd');
  description.textContent = value;
  list.append(term, description);
}

function show(data) {
  const review = data.view === 'review';
  if (!['review', 'clarification', 'status'].includes(data.view) || !data.brief
      || !Object.keys(intakeFields).every(key => typeof data.brief[key] === 'string')
      || (review ? !/^[0-9a-f]{32}$/.test(data.submission_id)
        : !['request_id', 'title', 'status', 'next_action', 'revision'].every(key => typeof data[key] === 'string')
          || !Array.isArray(data.questions) || !data.answers)) {
    throw new Error('The request response is incomplete.');
  }
  request = data;
  requestId = review ? '' : data.request_id;
  document.getElementById('request-title').textContent = data.brief.requested_software || 'Software request';
  document.getElementById('request-id').textContent = requestId;
  const labels = { draft: 'Unsubmitted', submitted: 'Submitted', needs_clarification: 'Needs clarification', in_review: 'In review' };
  document.getElementById('request-status').textContent = review ? 'Review before submitting' : '· ' + (labels[data.status] ?? data.status);
  document.getElementById('next-action').textContent = review ? 'Nothing is submitted until you choose Submit request.' : data.next_action;
  for (const [key, input] of intakeInputs) input.value = data.brief[key];
  document.getElementById('intake-view').hidden = !review;
  document.getElementById('clarification-view').hidden = data.view !== 'clarification';
  const questions = review ? [] : data.questions;
  document.getElementById('status-view').hidden = data.view !== 'status' || questions.length === 0;

  const brief = document.getElementById('brief');
  brief.replaceChildren();
  for (const [key, label] of Object.entries(intakeFields)) {
    if (data.brief[key]) detail(brief, label, data.brief[key]);
  }
  const savedBrief = document.getElementById('saved-brief');
  savedBrief.hidden = review;
  savedBrief.open = data.view === 'status';

  answerInputs.clear();
  const questionFields = document.getElementById('questions');
  questionFields.replaceChildren();
  const savedAnswers = document.getElementById('saved-answers');
  savedAnswers.replaceChildren();
  for (const [index, question] of questions.entries()) {
    const answer = Object.hasOwn(data.answers, question.id) ? data.answers[question.id] : undefined;
    if (data.view === 'status') {
      detail(savedAnswers, question.prompt, answer === true ? 'Yes' : answer === false ? 'No' : answer || 'Not answered');
      continue;
    }
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
    questionFields.append(field);
    answerInputs.set(question.id, input);
  }
}

async function load() {
  if (busy || !connected || !requestId) return;
  availability(true);
  status('Loading the saved project…');
  try {
    show(unpack(await app.callServerTool({
      name: 'contoso_open_request', arguments: { request_id: requestId },
    })));
    status('Latest saved project loaded.');
  } catch (error) {
    request = null;
    status(error.message, true);
  } finally {
    availability(false);
  }
}

async function submitTool(name, arguments_, success) {
  availability(true);
  status('Submitting…');
  try {
    show(unpack(await app.callServerTool({ name, arguments: arguments_ })));
    status(success);
  } catch (error) {
    const review = request?.view === 'review';
    if (!review) request = null;
    status(error.message + (review ? '' : ' Reload the saved project before trying again.'), true);
  } finally {
    availability(false);
  }
}

form.addEventListener('input', () => { if (!busy) availability(false); });
form.addEventListener('change', () => { if (!busy) availability(false); });
form.addEventListener('submit', event => event.preventDefault());

submitRequest.addEventListener('click', async () => {
  if (busy || !connected || request?.view !== 'review' || missingIntakeFields().length) return;
  await submitTool('contoso_submit_request', {
    submission_id: request.submission_id,
    ...Object.fromEntries([...intakeInputs].map(([key, input]) => [key, input.value])),
  }, 'Request submitted for review.');
});

submit.addEventListener('click', async () => {
  if (busy || !connected || request?.view !== 'clarification' || missingAnswers().length) return;
  const answers = Object.fromEntries(request.questions.map(question =>
    [question.id, answerValue(question, answerInputs.get(question.id))]));
  await submitTool('contoso_submit_answers', {
    request_id: request.request_id, answers, revision: request.revision,
    question_set_revision: request.question_set_revision,
  }, 'Clarification submitted for review.');
});

reload.addEventListener('click', load);

function initialize() {
  try {
    if (initialResult.view === 'review') {
      // A review reopens its recorded brief; open the submitted project for saved state.
      show(initialResult);
      status('Review your details. Changes in this form are saved only when you submit.');
      availability(false);
    } else {
      if (typeof initialResult.request_id !== 'string') throw new Error('The project ID is missing.');
      requestId = initialResult.request_id;
      void load();
    }
  } catch (error) {
    status(error.message, true);
  }
}

app.ontoolresult = result => {
  if (initialResult) return;
  try {
    initialResult = unpack(result);
    if (connected) initialize();
  } catch (error) {
    status(error.message, true);
  }
};

if (window.parent === window) {
  status('Open this form from your agent or the MCP App picker.', true);
} else {
  app.connect().then(() => {
    connected = true;
    if (initialResult) initialize();
  }).catch(error => status('Could not connect to the agent. Reopen the form. ' + error.message, true));
}
