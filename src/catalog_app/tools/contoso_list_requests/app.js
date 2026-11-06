import { App, applyDocumentTheme } from 'https://cdn.jsdelivr.net/npm/@modelcontextprotocol/ext-apps@2.0.0/dist/src/app-with-deps.js';

const app = new App(
  { name: 'Software projects', version: '1.0.0' },
  { availableDisplayModes: ['inline', 'fullscreen'] },
);
const summary = document.getElementById('summary');
const projects = document.getElementById('projects');
const labels = { draft: 'Unsubmitted', submitted: 'Submitted', needs_clarification: 'Needs clarification', in_review: 'In review' };

function showError(error) {
  projects.replaceChildren();
  summary.textContent = error.message;
  summary.dataset.error = 'true';
}

app.ontoolresult = result => {
  try {
    if (result.isError) {
      throw new Error(result.content?.filter(item => item.type === 'text').map(item => item.text).join(' ') || 'Could not load projects.');
    }
    const requests = result.structuredContent?.requests;
    if (!Array.isArray(requests) || !requests.every(request => request &&
        ['request_id', 'title', 'status', 'next_action'].every(key => typeof request[key] === 'string'))) {
      throw new Error('The project list response is incomplete.');
    }
    projects.replaceChildren();
    for (const request of requests) {
      const item = document.createElement('li');
      const title = document.createElement('h2');
      title.textContent = request.title;
      const details = document.createElement('p');
      details.className = 'details';
      details.textContent = `${request.request_id} · ${labels[request.status] ?? request.status}`;
      const action = document.createElement('p');
      action.textContent = request.next_action;
      item.append(title, details, action);
      projects.append(item);
    }
    summary.textContent = requests.length ? `${requests.length} project${requests.length === 1 ? '' : 's'}` : 'No projects found.';
    summary.dataset.error = 'false';
  } catch (error) {
    showError(error);
  }
};

const applyTheme = context => {
  if (context?.theme) applyDocumentTheme(context.theme);
};
app.onhostcontextchanged = applyTheme;
try {
  await app.connect();
  applyTheme(app.getHostContext());
} catch (error) {
  showError(error);
}
