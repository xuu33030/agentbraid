"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const PAGE_SIZE = 50;
const csrfToken = document.querySelector('meta[name="agentbraid-csrf"]').content;

const state = {
  meta: null,
  workspaces: [],
  runs: [],
  capabilities: [],
  selectedRunId: null,
  detail: null,
  offset: 0,
  hasMore: false,
  pollInFlight: false,
};

const elements = {
  workspaceSelect: document.getElementById("workspace-select"),
  refreshButton: document.getElementById("refresh-button"),
  connectionState: document.getElementById("connection-state"),
  runCount: document.getElementById("run-count"),
  scopeSummary: document.getElementById("scope-summary"),
  runList: document.getElementById("run-list"),
  loadMoreButton: document.getElementById("load-more-button"),
  emptyState: document.getElementById("empty-state"),
  runDetail: document.getElementById("run-detail"),
  runStatus: document.getElementById("run-status"),
  runId: document.getElementById("run-id"),
  runGoal: document.getElementById("run-goal"),
  runWorkspace: document.getElementById("run-workspace"),
  cancelButton: document.getElementById("cancel-button"),
  applyButton: document.getElementById("apply-button"),
  actionMessage: document.getElementById("action-message"),
  totalTokens: document.getElementById("metric-total-tokens"),
  cacheRatio: document.getElementById("metric-cache-ratio"),
  cachedTokens: document.getElementById("metric-cached-tokens"),
  retryTokens: document.getElementById("metric-retry-tokens"),
  duration: document.getElementById("metric-duration"),
  invocations: document.getElementById("metric-invocations"),
  taskDag: document.getElementById("task-dag"),
  taskDetail: document.getElementById("task-detail"),
  usageChart: document.getElementById("usage-chart"),
  legacyNote: document.getElementById("legacy-note"),
  capabilityList: document.getElementById("capability-list"),
  usageTableBody: document.getElementById("usage-table-body"),
  eventTimeline: document.getElementById("event-timeline"),
  deliveryDetail: document.getElementById("delivery-detail"),
  applyBlockers: document.getElementById("apply-blockers"),
  finalSummary: document.getElementById("final-summary"),
  cancelDialog: document.getElementById("cancel-dialog"),
  confirmCancelButton: document.getElementById("confirm-cancel-button"),
  applyDialog: document.getElementById("apply-dialog"),
  applyForm: document.getElementById("apply-form"),
  applyConfirmation: document.getElementById("apply-confirmation"),
  confirmApplyButton: document.getElementById("confirm-apply-button"),
  closeApplyDialog: document.getElementById("close-apply-dialog"),
  toast: document.getElementById("toast"),
};

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body) {
    headers.set("Content-Type", "application/json");
  }
  if (options.method && options.method !== "GET") {
    headers.set("X-AgentBraid-CSRF", csrfToken);
  }
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (!response.ok) {
    const message = payload?.error?.message || `Dashboard request failed (${response.status})`;
    throw new Error(message);
  }
  return payload;
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) {
    element.className = className;
  }
  if (text !== undefined && text !== null) {
    element.textContent = String(text);
  }
  return element;
}

function createSvg(tag, attributes = {}) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) {
    element.setAttribute(name, String(value));
  }
  return element;
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

function formatDuration(seconds) {
  const value = Number(seconds || 0);
  if (value < 60) {
    return `${value.toFixed(value < 10 ? 1 : 0)}s`;
  }
  const minutes = Math.floor(value / 60);
  const remaining = Math.round(value % 60);
  return `${minutes}m ${remaining}s`;
}

function formatDate(value) {
  if (!value) {
    return "—";
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function shortPath(path) {
  if (!path) {
    return "Unknown workspace";
  }
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || path;
}

function truncate(value, length) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function statusPill(status) {
  const pill = createElement("span", `status-pill ${status}`, status);
  return pill;
}

function setConnection(status, label) {
  elements.connectionState.className = `connection-state ${status}`;
  elements.connectionState.lastElementChild.textContent = label;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 3200);
}

function showActionMessage(message, type = "") {
  elements.actionMessage.textContent = message;
  elements.actionMessage.className = `message ${type}`.trim();
  elements.actionMessage.hidden = false;
}

function clearActionMessage() {
  elements.actionMessage.hidden = true;
  elements.actionMessage.textContent = "";
}

async function initialize() {
  try {
    setConnection("", "Loading");
    const [metaPayload, workspacePayload, capabilityPayload] = await Promise.all([
      api("/api/v1/meta"),
      api("/api/v1/workspaces"),
      api("/api/v1/capabilities"),
    ]);
    state.meta = metaPayload;
    state.workspaces = workspacePayload.workspaces;
    state.capabilities = capabilityPayload.capabilities;
    renderWorkspaceOptions();
    if (
      state.meta.initial_workspace &&
      state.workspaces.some((item) => item.workspace === state.meta.initial_workspace)
    ) {
      elements.workspaceSelect.value = state.meta.initial_workspace;
    }
    await loadRuns({ reset: true });
    setConnection("connected", "Live");
  } catch (error) {
    handleError(error);
  }
}

function renderWorkspaceOptions() {
  const currentValue = elements.workspaceSelect.value;
  const allOption = createElement("option", "", "All projects");
  allOption.value = "";
  const options = [allOption];
  for (const workspace of state.workspaces) {
    const option = createElement(
      "option",
      "",
      `${shortPath(workspace.workspace)} · ${workspace.run_count}`,
    );
    option.value = workspace.workspace;
    option.title = workspace.workspace;
    options.push(option);
  }
  elements.workspaceSelect.replaceChildren(...options);
  if ([...elements.workspaceSelect.options].some((option) => option.value === currentValue)) {
    elements.workspaceSelect.value = currentValue;
  }
}

async function loadRuns({ reset = false, append = false, silent = false } = {}) {
  if (reset) {
    state.offset = 0;
  }
  const workspace = elements.workspaceSelect.value;
  const query = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(state.offset),
  });
  if (workspace) {
    query.set("workspace", workspace);
  }
  if (!silent) {
    setConnection("", "Refreshing");
  }
  const payload = await api(`/api/v1/runs?${query.toString()}`);
  state.runs = append ? [...state.runs, ...payload.runs] : payload.runs;
  state.hasMore = payload.runs.length === PAGE_SIZE;
  renderRunList();
  renderScopeSummary();

  if (state.selectedRunId && state.runs.some((run) => run.run_id === state.selectedRunId)) {
    await loadRunDetail(state.selectedRunId, { silent: true });
  } else if (state.runs.length) {
    await selectRun(state.runs[0].run_id);
  } else {
    state.selectedRunId = null;
    state.detail = null;
    renderEmptyState();
  }
  setConnection("connected", "Live");
}

function renderRunList() {
  elements.runCount.textContent = formatNumber(state.runs.length);
  if (!state.runs.length) {
    elements.runList.replaceChildren(
      createElement("div", "empty-list", "No AgentBraid runs in this scope."),
    );
  } else {
    const items = state.runs.map((run) => {
      const button = createElement(
        "button",
        `run-item${run.run_id === state.selectedRunId ? " selected" : ""}`,
      );
      button.type = "button";
      button.dataset.runId = run.run_id;
      button.setAttribute("aria-pressed", String(run.run_id === state.selectedRunId));

      const top = createElement("div", "run-item-top");
      top.append(statusPill(run.status));
      top.append(createElement("span", "mono subtle", run.run_id.slice(0, 8)));
      const goal = createElement("div", "run-item-goal", run.goal);
      const footer = createElement("div", "run-item-footer");
      footer.append(createElement("span", "", shortPath(run.workspace)));
      footer.append(createElement("span", "", formatNumber(run.observed_total_tokens)));
      button.append(top, goal, footer);
      button.addEventListener("click", () => selectRun(run.run_id));
      return button;
    });
    elements.runList.replaceChildren(...items);
  }
  elements.loadMoreButton.hidden = !state.hasMore;
}

function renderScopeSummary() {
  const selected = elements.workspaceSelect.value;
  const relevant = selected
    ? state.workspaces.filter((workspace) => workspace.workspace === selected)
    : state.workspaces;
  const active = relevant.reduce((sum, workspace) => sum + workspace.active_run_count, 0);
  const tokens = relevant.reduce((sum, workspace) => sum + workspace.observed_total_tokens, 0);
  elements.scopeSummary.textContent = `${formatNumber(active)} active · ${formatNumber(tokens)} observed tokens`;
}

async function selectRun(runId) {
  state.selectedRunId = runId;
  renderRunList();
  await loadRunDetail(runId);
}

async function loadRunDetail(runId, { silent = false } = {}) {
  if (!silent) {
    setConnection("", "Loading run");
  }
  const detail = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
  if (state.selectedRunId !== runId) {
    return;
  }
  state.detail = detail;
  renderRunDetail();
  setConnection("connected", "Live");
}

function renderEmptyState() {
  elements.emptyState.hidden = false;
  elements.runDetail.hidden = true;
}

function renderRunDetail() {
  if (!state.detail) {
    renderEmptyState();
    return;
  }
  const { run, usage, actions } = state.detail;
  elements.emptyState.hidden = true;
  elements.runDetail.hidden = false;
  elements.runStatus.textContent = run.status;
  elements.runStatus.className = `status-pill ${run.status}`;
  elements.runId.textContent = run.run_id;
  elements.runGoal.textContent = run.request.goal;
  elements.runWorkspace.textContent = run.request.workspace || "Unknown workspace";
  elements.cancelButton.hidden = !actions.can_cancel;
  elements.cancelButton.disabled = !actions.can_cancel;
  elements.applyButton.hidden = run.request.delivery_mode !== "integration_branch";
  elements.applyButton.disabled = !actions.apply.can_apply;
  clearActionMessage();

  renderMetrics(usage.totals);
  renderDag(run.tasks);
  renderUsageChart(usage.by_phase, usage.totals);
  renderUsageTable(usage.records);
  renderCapabilities();
  renderTimeline(state.detail.events);
  renderDelivery(run, actions.apply);
}

function renderMetrics(totals) {
  elements.totalTokens.textContent = formatNumber(totals.observed_total_tokens);
  const ratio = totals.input_tokens
    ? Math.round((totals.cached_input_tokens / totals.input_tokens) * 100)
    : 0;
  elements.cacheRatio.textContent = `${ratio}%`;
  elements.cachedTokens.textContent = `${formatNumber(totals.cached_input_tokens)} cached`;
  elements.retryTokens.textContent = formatNumber(totals.retry_tokens);
  elements.duration.textContent = formatDuration(totals.duration_seconds);
  elements.invocations.textContent = `${formatNumber(totals.invocation_count)} invocations`;
  if (totals.legacy_invocation_count) {
    elements.legacyNote.textContent = `${formatNumber(totals.legacy_invocation_count)} legacy invocation records do not include attempt/outcome attribution.`;
    elements.legacyNote.hidden = false;
  } else {
    elements.legacyNote.hidden = true;
  }
}

function taskDepths(tasks) {
  const byId = new Map(tasks.map((task) => [task.spec.task_id, task]));
  const memo = new Map();
  function depth(taskId, visiting = new Set()) {
    if (memo.has(taskId)) {
      return memo.get(taskId);
    }
    if (visiting.has(taskId)) {
      return 0;
    }
    visiting.add(taskId);
    const task = byId.get(taskId);
    const value = task && task.spec.dependencies.length
      ? Math.max(...task.spec.dependencies.map((dependency) => depth(dependency, visiting))) + 1
      : 0;
    visiting.delete(taskId);
    memo.set(taskId, value);
    return value;
  }
  for (const task of tasks) {
    depth(task.spec.task_id);
  }
  return memo;
}

function renderDag(tasks) {
  elements.taskDag.replaceChildren();
  elements.taskDetail.textContent = tasks.length
    ? "Select a task node to inspect its assignment and latest result."
    : "This run has no persisted task plan yet.";
  if (!tasks.length) {
    elements.taskDag.setAttribute("viewBox", "0 0 680 230");
    return;
  }

  const depths = taskDepths(tasks);
  const columns = new Map();
  for (const task of tasks) {
    const depth = depths.get(task.spec.task_id) || 0;
    if (!columns.has(depth)) {
      columns.set(depth, []);
    }
    columns.get(depth).push(task);
  }
  for (const column of columns.values()) {
    column.sort((left, right) => left.spec.task_id.localeCompare(right.spec.task_id));
  }

  const nodeWidth = 190;
  const nodeHeight = 74;
  const xGap = 62;
  const yGap = 28;
  const padding = 26;
  const maxDepth = Math.max(...columns.keys());
  const maxRows = Math.max(...[...columns.values()].map((column) => column.length));
  const width = Math.max(680, padding * 2 + (maxDepth + 1) * nodeWidth + maxDepth * xGap);
  const height = Math.max(230, padding * 2 + maxRows * nodeHeight + (maxRows - 1) * yGap);
  elements.taskDag.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const positions = new Map();
  for (const [depth, column] of columns.entries()) {
    const columnHeight = column.length * nodeHeight + (column.length - 1) * yGap;
    const startY = Math.max(padding, (height - columnHeight) / 2);
    column.forEach((task, index) => {
      positions.set(task.spec.task_id, {
        x: padding + depth * (nodeWidth + xGap),
        y: startY + index * (nodeHeight + yGap),
      });
    });
  }

  const edges = createSvg("g", { "aria-hidden": "true" });
  for (const task of tasks) {
    const target = positions.get(task.spec.task_id);
    for (const dependency of task.spec.dependencies) {
      const source = positions.get(dependency);
      if (!source || !target) {
        continue;
      }
      const startX = source.x + nodeWidth;
      const startY = source.y + nodeHeight / 2;
      const endX = target.x;
      const endY = target.y + nodeHeight / 2;
      const midpoint = (startX + endX) / 2;
      edges.append(
        createSvg("path", {
          class: "dag-edge",
          d: `M ${startX} ${startY} C ${midpoint} ${startY}, ${midpoint} ${endY}, ${endX} ${endY}`,
        }),
      );
    }
  }
  elements.taskDag.append(edges);

  for (const task of tasks) {
    const position = positions.get(task.spec.task_id);
    const group = createSvg("g", {
      class: "dag-node",
      role: "button",
      tabindex: "0",
      "aria-label": `${task.spec.title}, ${task.executor}, ${task.status}`,
      transform: `translate(${position.x} ${position.y})`,
    });
    group.append(createSvg("rect", { width: nodeWidth, height: nodeHeight, rx: 10 }));
    group.append(
      createSvg("rect", {
        class: `executor-strip ${task.executor}`,
        width: 5,
        height: nodeHeight,
        rx: 2.5,
      }),
    );
    const title = createSvg("text", { x: 15, y: 24 });
    title.textContent = truncate(task.spec.title, 25);
    const identifier = createSvg("text", { x: 15, y: 44, class: "dag-node-meta" });
    identifier.textContent = task.spec.task_id;
    const metadata = createSvg("text", { x: 15, y: 61, class: "dag-node-meta" });
    metadata.textContent = `${task.status} · ${task.executor} · attempt ${task.attempt}`;
    group.append(title, identifier, metadata);
    const select = () => {
      elements.taskDag.querySelectorAll(".dag-node").forEach((node) => node.classList.remove("selected"));
      group.classList.add("selected");
      renderTaskDetail(task);
    };
    group.addEventListener("click", select);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select();
      }
    });
    elements.taskDag.append(group);
  }
}

function renderTaskDetail(task) {
  const result = task.result?.summary ? ` Latest result: ${task.result.summary}` : "";
  elements.taskDetail.textContent = `${task.spec.task_id} · ${task.assignment_rationale} Mutates workspace: ${task.spec.mutates_workspace ? "yes" : "no"}.${result}`;
}

function renderUsageChart(buckets, totals) {
  if (!buckets.length) {
    elements.usageChart.replaceChildren(
      createElement("div", "empty-list", "No provider usage has been recorded for this run."),
    );
    return;
  }
  const maximum = Math.max(...buckets.map((bucket) => bucket.observed_total_tokens), 1);
  const rows = buckets.map((bucket) => {
    const row = createElement("div", "usage-row");
    row.append(createElement("span", "usage-label", bucket.phase));
    const chart = createSvg("svg", {
      class: "usage-track",
      viewBox: "0 0 100 15",
      preserveAspectRatio: "none",
      role: "img",
      "aria-label": `${bucket.phase}: ${formatNumber(bucket.observed_total_tokens)} observed tokens`,
    });
    const segments = [
      ["uncached", bucket.uncached_input_tokens],
      ["cached", bucket.cached_input_tokens],
      ["output", bucket.non_reasoning_output_tokens],
      ["reasoning", bucket.reasoning_output_tokens],
    ];
    let x = 0;
    for (const [name, value] of segments) {
      const width = (Number(value) / maximum) * 100;
      if (width > 0) {
        const segment = createSvg("rect", {
          class: `usage-segment ${name}`,
          x,
          y: 0,
          width,
          height: 15,
        });
        segment.append(createSvg("title"));
        segment.firstElementChild.textContent = `${name}: ${formatNumber(value)}`;
        chart.append(segment);
      }
      x += width;
    }
    row.append(chart);
    row.append(createElement("span", "usage-value", formatNumber(bucket.observed_total_tokens)));
    return row;
  });
  elements.usageChart.replaceChildren(...rows);
  if (totals.observed_total_tokens === 0) {
    elements.usageChart.append(
      createElement("div", "empty-list", "Usage records contain no reported tokens."),
    );
  }
}

function renderUsageTable(records) {
  if (!records.length) {
    const row = createElement("tr");
    const cell = createElement("td", "subtle", "No provider invocation records.");
    cell.colSpan = 9;
    row.append(cell);
    elements.usageTableBody.replaceChildren(row);
    return;
  }
  const rows = records.map((record) => {
    const row = createElement("tr");
    const label = record.task_id ? `${record.phase} / ${record.task_id}` : record.phase;
    const values = [
      [label, ""],
      [record.model, "mono"],
      [record.phase === "task" ? (record.attempt ?? "legacy") : "—", "number"],
      [record.outcome ?? "legacy", ""],
      [formatNumber(record.input_tokens), "number"],
      [formatNumber(record.cached_input_tokens), "number"],
      [formatNumber(record.output_tokens), "number"],
      [formatNumber(record.reasoning_output_tokens), "number"],
      [formatDuration(record.duration_seconds), "number"],
    ];
    for (const [value, className] of values) {
      row.append(createElement("td", className, value));
    }
    return row;
  });
  elements.usageTableBody.replaceChildren(...rows);
}

function renderCapabilities() {
  if (!state.capabilities.length) {
    elements.capabilityList.replaceChildren(
      createElement("div", "empty-list", "No provider capability observations yet."),
    );
    return;
  }
  const items = state.capabilities.map((capability) => {
    const item = createElement("div", "capability-item");
    item.append(createElement("div", "capability-model", `${capability.executor} / ${capability.model}`));
    item.append(statusPill(capability.status));
    item.append(
      createElement(
        "div",
        "capability-meta",
        `${formatNumber(capability.successes)} successes · ${formatNumber(capability.failures)} failures · ${formatDuration(capability.average_latency_seconds)} avg`,
      ),
    );
    return item;
  });
  elements.capabilityList.replaceChildren(...items);
}

const eventLabels = {
  "run.created": "Run created",
  "run.planned": "Plan persisted",
  "run.status_changed": "Run status changed",
  "run.lead_thread_updated": "Lead thread updated",
  "run.reviewed": "Final review recorded",
  "run.cancelled": "Run cancelled",
  "run.applied": "Integration applied",
  "task.status_changed": "Task status changed",
  "task.claimed": "Task claimed",
  "task.worktree_assigned": "Worktree assigned",
  "task.result_submitted": "Task result submitted",
};

function eventSummary(event) {
  const payload = event.payload || {};
  if (payload.from || payload.to) {
    return [payload.from, payload.to].filter(Boolean).join(" → ");
  }
  if (payload.outcome || payload.status) {
    return [payload.outcome, payload.status, payload.attempt ? `attempt ${payload.attempt}` : null]
      .filter(Boolean)
      .join(" · ");
  }
  if (payload.executor || payload.claimed_by) {
    return [payload.executor, payload.claimed_by].filter(Boolean).join(" · ");
  }
  if (event.task_id) {
    return event.task_id;
  }
  return "Durable state event";
}

function renderTimeline(events) {
  if (!events.length) {
    elements.eventTimeline.replaceChildren(
      createElement("li", "empty-list", "No durable events have been recorded."),
    );
    return;
  }
  const items = events.map((event) => {
    const item = createElement("li", "timeline-item");
    item.append(createElement("time", "timeline-time", formatDate(event.created_at)));
    item.append(createElement("span", "timeline-rail"));
    const copy = createElement("div", "timeline-copy");
    copy.append(createElement("strong", "", eventLabels[event.event_type] || event.event_type));
    const taskPrefix = event.task_id ? `${event.task_id} · ` : "";
    copy.append(createElement("span", "", `${taskPrefix}${eventSummary(event)}`));
    item.append(copy);
    return item;
  });
  elements.eventTimeline.replaceChildren(...items);
}

function renderDelivery(run, readiness) {
  const entries = [
    ["Delivery mode", run.request.delivery_mode],
    ["Integration branch", run.integration_branch || "—"],
    ["Expected branch", readiness.expected_branch || "—"],
    ["Expected commit", readiness.expected_commit || "—"],
    ["Current branch", readiness.current_branch || "—"],
    ["Current commit", readiness.current_commit || "—"],
  ];
  const nodes = [];
  for (const [term, description] of entries) {
    nodes.push(createElement("dt", "", term), createElement("dd", "", description));
  }
  elements.deliveryDetail.replaceChildren(...nodes);
  const blockers = readiness.blockers.map((blocker) => createElement("div", "blocker", blocker));
  elements.applyBlockers.replaceChildren(...blockers);
  elements.finalSummary.textContent = run.final_summary || run.error || "Final review has not completed.";
}

async function refreshAll({ silent = false } = {}) {
  if (state.pollInFlight) {
    return;
  }
  state.pollInFlight = true;
  try {
    const [workspacePayload, capabilityPayload] = await Promise.all([
      api("/api/v1/workspaces"),
      api("/api/v1/capabilities"),
    ]);
    state.workspaces = workspacePayload.workspaces;
    state.capabilities = capabilityPayload.capabilities;
    renderWorkspaceOptions();
    await loadRuns({ reset: true, silent });
  } catch (error) {
    handleError(error);
  } finally {
    state.pollInFlight = false;
  }
}

function handleError(error) {
  const message = error instanceof Error ? error.message : "Dashboard request failed";
  setConnection("error", "Disconnected");
  showActionMessage(message, "error");
}

elements.workspaceSelect.addEventListener("change", () => {
  state.selectedRunId = null;
  state.detail = null;
  loadRuns({ reset: true }).catch(handleError);
});

elements.refreshButton.addEventListener("click", () => refreshAll());

elements.loadMoreButton.addEventListener("click", async () => {
  state.offset += PAGE_SIZE;
  try {
    await loadRuns({ append: true });
  } catch (error) {
    state.offset -= PAGE_SIZE;
    handleError(error);
  }
});

elements.cancelButton.addEventListener("click", () => elements.cancelDialog.showModal());

elements.cancelDialog.addEventListener("close", async () => {
  if (elements.cancelDialog.returnValue !== "confirm" || !state.selectedRunId) {
    return;
  }
  try {
    await api(`/api/v1/runs/${encodeURIComponent(state.selectedRunId)}/cancel`, {
      method: "POST",
    });
    showToast("Run cancelled.");
    await refreshAll();
  } catch (error) {
    handleError(error);
  }
});

elements.applyButton.addEventListener("click", () => {
  elements.applyConfirmation.value = "";
  elements.confirmApplyButton.disabled = true;
  elements.applyDialog.showModal();
  elements.applyConfirmation.focus();
});

elements.applyConfirmation.addEventListener("input", () => {
  elements.confirmApplyButton.disabled = elements.applyConfirmation.value !== "apply-reviewed-run";
});

elements.closeApplyDialog.addEventListener("click", () => elements.applyDialog.close());

elements.applyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedRunId || elements.applyConfirmation.value !== "apply-reviewed-run") {
    return;
  }
  elements.confirmApplyButton.disabled = true;
  try {
    await api(`/api/v1/runs/${encodeURIComponent(state.selectedRunId)}/apply`, {
      method: "POST",
      body: JSON.stringify({ confirmation: elements.applyConfirmation.value }),
    });
    elements.applyDialog.close();
    showToast("Reviewed integration branch applied.");
    await refreshAll();
  } catch (error) {
    elements.confirmApplyButton.disabled = false;
    elements.applyDialog.close();
    handleError(error);
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshAll({ silent: true });
  }
});

window.setInterval(() => {
  if (!document.hidden) {
    refreshAll({ silent: true });
  }
}, 2000);

initialize();
