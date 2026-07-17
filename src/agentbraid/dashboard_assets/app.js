"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const PAGE_SIZE = 50;
const SUPPORTED_LOCALES = new Set(["en", "zh-TW", "zh-CN"]);
const LOCALE_COOKIE_NAME = "agentbraid_dashboard_locale";
const LOCALE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60;
const MOBILE_BREAKPOINT = window.matchMedia("(max-width: 800px)");
const csrfToken = document.querySelector('meta[name="agentbraid-csrf"]').content;

const state = {
  locale: "en",
  messages: {},
  translationsAvailable: false,
  meta: null,
  workspaces: [],
  runs: [],
  capabilities: [],
  modelOptions: { codex: [], host: [] },
  selectedRunIds: new Set(),
  deletePreviews: [],
  selectedRunId: null,
  selectedTaskId: null,
  detail: null,
  offset: 0,
  hasMore: false,
  pollInFlight: false,
  mobileRunListExpanded: false,
  connection: {
    status: "",
    key: "connection.connecting",
    fallback: "Connecting",
  },
};

const elements = {
  workspaceSelect: document.getElementById("workspace-select"),
  languageSelect: document.getElementById("language-select"),
  refreshButton: document.getElementById("refresh-button"),
  settingsButton: document.getElementById("settings-button"),
  startRunButton: document.getElementById("start-run-button"),
  connectionState: document.getElementById("connection-state"),
  runCount: document.getElementById("run-count"),
  runListToggle: document.getElementById("run-list-toggle"),
  runListRegion: document.getElementById("run-list-region"),
  scopeSummary: document.getElementById("scope-summary"),
  selectVisibleRuns: document.getElementById("select-visible-runs"),
  deleteSelectedButton: document.getElementById("delete-selected-button"),
  runList: document.getElementById("run-list"),
  loadMoreButton: document.getElementById("load-more-button"),
  emptyState: document.getElementById("empty-state"),
  runDetail: document.getElementById("run-detail"),
  runStatus: document.getElementById("run-status"),
  runId: document.getElementById("run-id"),
  runGoal: document.getElementById("run-goal"),
  runOriginalGoal: document.getElementById("run-original-goal"),
  runWorkspace: document.getElementById("run-workspace"),
  cancelButton: document.getElementById("cancel-button"),
  renameButton: document.getElementById("rename-button"),
  applyButton: document.getElementById("apply-button"),
  applyActionHint: document.getElementById("apply-action-hint"),
  actionMessage: document.getElementById("action-message"),
  totalTokens: document.getElementById("metric-total-tokens"),
  cacheRatio: document.getElementById("metric-cache-ratio"),
  cachedTokens: document.getElementById("metric-cached-tokens"),
  retryTokens: document.getElementById("metric-retry-tokens"),
  duration: document.getElementById("metric-duration"),
  invocations: document.getElementById("metric-invocations"),
  dagContainer: document.getElementById("dag-container"),
  taskDag: document.getElementById("task-dag"),
  dagScrollHint: document.getElementById("dag-scroll-hint"),
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
  startRunDialog: document.getElementById("start-run-dialog"),
  startRunForm: document.getElementById("start-run-form"),
  closeStartRun: document.getElementById("close-start-run"),
  startWorkspace: document.getElementById("start-workspace"),
  startGoal: document.getElementById("start-goal"),
  startConstraints: document.getElementById("start-constraints"),
  startCodexModel: document.getElementById("start-codex-model"),
  startHostModel: document.getElementById("start-host-model"),
  startRouting: document.getElementById("start-routing"),
  startDelivery: document.getElementById("start-delivery"),
  startWorkspaceMode: document.getElementById("start-workspace-mode"),
  startParallel: document.getElementById("start-parallel"),
  startAttempts: document.getElementById("start-attempts"),
  startTimeout: document.getElementById("start-timeout"),
  startOutputBytes: document.getElementById("start-output-bytes"),
  startSaveDefaults: document.getElementById("start-save-defaults"),
  settingsDialog: document.getElementById("settings-dialog"),
  settingsForm: document.getElementById("settings-form"),
  closeSettings: document.getElementById("close-settings"),
  settingsWorkspace: document.getElementById("settings-workspace"),
  settingsCodexModel: document.getElementById("settings-codex-model"),
  settingsHostModel: document.getElementById("settings-host-model"),
  settingsRouting: document.getElementById("settings-routing"),
  settingsDelivery: document.getElementById("settings-delivery"),
  settingsWorkspaceMode: document.getElementById("settings-workspace-mode"),
  settingsParallel: document.getElementById("settings-parallel"),
  settingsAttempts: document.getElementById("settings-attempts"),
  settingsTimeout: document.getElementById("settings-timeout"),
  settingsOutputBytes: document.getElementById("settings-output-bytes"),
  settingsCodexBinary: document.getElementById("settings-codex-binary"),
  settingsWorktreeDir: document.getElementById("settings-worktree-dir"),
  settingsDatabasePath: document.getElementById("settings-database-path"),
  settingsStateDir: document.getElementById("settings-state-dir"),
  settingsRestartNote: document.getElementById("settings-restart-note"),
  renameDialog: document.getElementById("rename-dialog"),
  renameForm: document.getElementById("rename-form"),
  closeRename: document.getElementById("close-rename"),
  renameEn: document.getElementById("rename-en"),
  renameZhTw: document.getElementById("rename-zh-tw"),
  renameZhCn: document.getElementById("rename-zh-cn"),
  deleteDialog: document.getElementById("delete-dialog"),
  deleteForm: document.getElementById("delete-form"),
  closeDelete: document.getElementById("close-delete"),
  deletePreview: document.getElementById("delete-preview"),
  deleteConfirmation: document.getElementById("delete-confirmation"),
  confirmDelete: document.getElementById("confirm-delete"),
  codexModelOptions: document.getElementById("codex-model-options"),
  hostModelOptions: document.getElementById("host-model-options"),
  toast: document.getElementById("toast"),
};

const valueLabels = {
  status: {
    created: ["status.created", "Created"],
    planning: ["status.planning", "Planning"],
    running: ["status.running", "Running"],
    integrating: ["status.integrating", "Integrating"],
    reviewing: ["status.reviewing", "Reviewing"],
    completed: ["status.completed", "Completed"],
    blocked: ["status.blocked", "Blocked"],
    cancelled: ["status.cancelled", "Cancelled"],
    failed: ["status.failed", "Failed"],
    pending: ["status.pending", "Pending"],
    ready: ["status.ready", "Ready"],
    retrying: ["status.retrying", "Retrying"],
    succeeded: ["status.succeeded", "Succeeded"],
    healthy: ["status.healthy", "Healthy"],
    constrained: ["status.constrained", "Constrained"],
    cooldown: ["status.cooldown", "Cooldown"],
    unavailable: ["status.unavailable", "Unavailable"],
  },
  executor: {
    codex: ["executor.codex", "Codex"],
    host: ["executor.host", "Host"],
  },
  outcome: {
    succeeded: ["status.succeeded", "Succeeded"],
    failed: ["status.failed", "Failed"],
    blocked: ["status.blocked", "Blocked"],
    approved: ["outcome.approved", "Approved"],
    rejected: ["outcome.rejected", "Rejected"],
  },
  phase: {
    planning: ["phase.planning", "Planning"],
    task: ["phase.task", "Task"],
    review: ["phase.review", "Review"],
  },
  deliveryMode: {
    integration_branch: ["deliveryMode.integration_branch", "Integration branch"],
    report_only: ["deliveryMode.report_only", "Report only"],
  },
  routingMode: {
    hybrid: ["routingMode.hybrid", "Hybrid"],
    codex_only: ["routingMode.codex_only", "Codex-only"],
  },
  workspaceMode: {
    worktree_write: ["workspaceMode.worktree_write", "Isolated worktree writes"],
    read_only: ["workspaceMode.read_only", "Read-only"],
  },
  artifactKind: {
    database: ["cleanup.database", "Database records"],
    worktree: ["cleanup.worktree", "Managed worktree"],
    branch: ["cleanup.branch", "Managed branch"],
  },
  usageSegment: {
    uncached: ["usage.segment.uncached", "uncached"],
    cached: ["usage.segment.cached", "cached"],
    output: ["usage.segment.output", "output"],
    reasoning: ["usage.segment.reasoning", "reasoning"],
  },
};

function interpolate(template, parameters = {}) {
  return String(template).replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => {
    return Object.hasOwn(parameters, name) ? String(parameters[name]) : match;
  });
}

function t(key, fallback = key, parameters = {}) {
  const template = state.messages[state.locale]?.[key]
    ?? state.messages.en?.[key]
    ?? fallback;
  return interpolate(template, parameters);
}

function translatedValue(group, value) {
  if (value === undefined || value === null || value === "") {
    return value;
  }
  const entry = valueLabels[group]?.[value];
  return entry ? t(entry[0], entry[1]) : String(value);
}

function cookieValue(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const candidate = part.trim();
    if (candidate.startsWith(prefix)) {
      try {
        return decodeURIComponent(candidate.slice(prefix.length));
      } catch (_error) {
        return "";
      }
    }
  }
  return "";
}

function localeForLanguage(value) {
  const locale = String(value || "").replaceAll("_", "-").toLowerCase();
  if (locale === "en" || locale.startsWith("en-")) {
    return "en";
  }
  if (!locale.startsWith("zh")) {
    return null;
  }
  if (
    locale.includes("hant")
    || locale === "zh-tw"
    || locale.startsWith("zh-tw-")
    || locale === "zh-hk"
    || locale.startsWith("zh-hk-")
    || locale === "zh-mo"
    || locale.startsWith("zh-mo-")
  ) {
    return "zh-TW";
  }
  return "zh-CN";
}

function resolveInitialLocale() {
  const saved = cookieValue(LOCALE_COOKIE_NAME);
  if (SUPPORTED_LOCALES.has(saved)) {
    return saved;
  }
  const candidates = Array.isArray(navigator.languages) && navigator.languages.length
    ? navigator.languages
    : [navigator.language];
  for (const candidate of candidates) {
    const locale = localeForLanguage(candidate);
    if (locale) {
      return locale;
    }
  }
  return "en";
}

function persistLocale(locale) {
  document.cookie = `${encodeURIComponent(LOCALE_COOKIE_NAME)}=${encodeURIComponent(locale)}`
    + `; Max-Age=${LOCALE_COOKIE_MAX_AGE}; Path=/; SameSite=Strict`;
}

async function loadTranslations() {
  try {
    const response = await fetch("/assets/locales.json", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`locale asset failed (${response.status})`);
    }
    const payload = await response.json();
    if (![...SUPPORTED_LOCALES].every((locale) => payload[locale])) {
      throw new Error("locale asset is missing a supported language");
    }
    state.messages = payload;
    state.translationsAvailable = true;
  } catch (error) {
    state.locale = "en";
    state.messages = {};
    state.translationsAvailable = false;
    elements.languageSelect.disabled = true;
    elements.languageSelect.title = "Translations unavailable; English remains available.";
    console.warn("AgentBraid locale asset unavailable; using English.", error);
  }
}

function applyStaticTranslations() {
  document.documentElement.lang = state.locale;
  document.title = t("app.title", "AgentBraid Dashboard");
  for (const element of document.querySelectorAll("[data-i18n]")) {
    const fallback = element.dataset.i18nFallback ?? element.textContent.trim();
    element.dataset.i18nFallback = fallback;
    element.textContent = t(element.dataset.i18n, fallback);
  }
  for (const element of document.querySelectorAll("[data-i18n-aria-label]")) {
    const fallback = element.dataset.i18nAriaLabelFallback
      ?? element.getAttribute("aria-label")
      ?? "";
    element.dataset.i18nAriaLabelFallback = fallback;
    element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel, fallback));
  }
  for (const element of document.querySelectorAll("[data-i18n-placeholder]")) {
    const fallback = element.dataset.i18nPlaceholderFallback
      ?? element.getAttribute("placeholder")
      ?? "";
    element.dataset.i18nPlaceholderFallback = fallback;
    element.setAttribute("placeholder", t(element.dataset.i18nPlaceholder, fallback));
  }
}

function applyLocale(locale, { persist = false, announce = false, rerender = true } = {}) {
  state.locale = SUPPORTED_LOCALES.has(locale) ? locale : "en";
  elements.languageSelect.value = state.locale;
  if (persist) {
    persistLocale(state.locale);
  }
  applyStaticTranslations();
  renderConfigurationOptionLabels();
  renderConnection();
  renderRunListToggle();
  if (rerender && state.meta) {
    renderWorkspaceOptions();
    renderRunList();
    renderScopeSummary();
    renderDeletePreview();
    if (state.detail) {
      renderRunDetail({ clearMessage: false });
    } else {
      renderEmptyState();
    }
  }
  if (announce) {
    const language = elements.languageSelect.selectedOptions[0]?.textContent || state.locale;
    showToast(t("toast.languageChanged", "Language changed to {language}.", { language }));
  }
}

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
    const message = payload?.error?.message || t(
      "error.requestFailed",
      "Dashboard request failed ({status})",
      { status: response.status },
    );
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
  return new Intl.NumberFormat(state.locale).format(Number(value || 0));
}

function formatDuration(seconds) {
  const value = Number(seconds || 0);
  if (value < 60) {
    const formatted = new Intl.NumberFormat(state.locale, {
      minimumFractionDigits: value < 10 ? 1 : 0,
      maximumFractionDigits: 1,
    }).format(value);
    return t("duration.seconds", "{seconds}s", { seconds: formatted });
  }
  const minutes = Math.floor(value / 60);
  const remaining = Math.round(value % 60);
  return t("duration.minutesSeconds", "{minutes}m {seconds}s", {
    minutes: formatNumber(minutes),
    seconds: formatNumber(remaining),
  });
}

function formatDate(value) {
  if (!value) {
    return "—";
  }
  return new Intl.DateTimeFormat(state.locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function shortPath(path) {
  if (!path) {
    return t("common.unknownWorkspace", "Unknown workspace");
  }
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || path;
}

function truncate(value, length) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function runGoal(run) {
  return run.goal ?? run.request?.goal ?? "";
}

function localizedRunName(run) {
  const names = run.display_names;
  if (names) {
    return names[state.locale] || names.en || names["zh-TW"] || names["zh-CN"] || runGoal(run);
  }
  return runGoal(run);
}

function concreteWorkspace() {
  const selected = elements.workspaceSelect.value;
  if (selected && state.workspaces.some((item) => item.workspace === selected)) {
    return selected;
  }
  if (
    state.meta?.initial_workspace
    && state.workspaces.some((item) => item.workspace === state.meta.initial_workspace)
  ) {
    return state.meta.initial_workspace;
  }
  return state.workspaces[0]?.workspace || "";
}

function workspaceOptions() {
  return state.workspaces.map((workspace) => {
    const option = createElement("option", "", shortPath(workspace.workspace));
    option.value = workspace.workspace;
    option.title = workspace.workspace;
    return option;
  });
}

function renderActionWorkspaceOptions() {
  const fallback = concreteWorkspace();
  for (const select of [elements.startWorkspace, elements.settingsWorkspace]) {
    const current = select.value;
    select.replaceChildren(...workspaceOptions());
    select.value = [...select.options].some((option) => option.value === current)
      ? current
      : fallback;
  }
  const hasWorkspace = Boolean(fallback);
  elements.startRunButton.disabled = !hasWorkspace;
  elements.settingsButton.disabled = !hasWorkspace;
}

function renderModelOptions() {
  const render = (target, models) => {
    target.replaceChildren(...models.map((model) => {
      const option = document.createElement("option");
      option.value = model;
      return option;
    }));
  };
  render(elements.codexModelOptions, state.modelOptions.codex || []);
  render(elements.hostModelOptions, state.modelOptions.host || []);
}

function renderConfigurationOptionLabels() {
  const mappings = [
    [elements.startRouting, "routingMode"],
    [elements.settingsRouting, "routingMode"],
    [elements.startDelivery, "deliveryMode"],
    [elements.settingsDelivery, "deliveryMode"],
    [elements.startWorkspaceMode, "workspaceMode"],
    [elements.settingsWorkspaceMode, "workspaceMode"],
  ];
  for (const [select, group] of mappings) {
    for (const option of select.options) {
      option.textContent = translatedValue(group, option.value);
    }
  }
}

function statusPill(status) {
  const pill = createElement("span", `status-pill ${status}`, translatedValue("status", status));
  return pill;
}

function renderConnection() {
  elements.connectionState.className = `connection-state ${state.connection.status}`;
  elements.connectionState.lastElementChild.textContent = t(
    state.connection.key,
    state.connection.fallback,
  );
}

function setConnection(status, key, fallback) {
  state.connection = { status, key, fallback };
  renderConnection();
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
    state.locale = resolveInitialLocale();
    await loadTranslations();
    if (!state.translationsAvailable) {
      state.locale = "en";
    }
    applyLocale(state.locale, { rerender: false });
    setConnection("", "connection.loading", "Loading");
    const [metaPayload, workspacePayload, capabilityPayload, modelPayload] = await Promise.all([
      api("/api/v1/meta"),
      api("/api/v1/workspaces"),
      api("/api/v1/capabilities"),
      api("/api/v1/model-options"),
    ]);
    state.meta = metaPayload;
    state.workspaces = workspacePayload.workspaces;
    state.capabilities = capabilityPayload.capabilities;
    state.modelOptions = modelPayload;
    renderWorkspaceOptions();
    renderModelOptions();
    if (
      state.meta.initial_workspace &&
      state.workspaces.some((item) => item.workspace === state.meta.initial_workspace)
    ) {
      elements.workspaceSelect.value = state.meta.initial_workspace;
    }
    await loadRuns({ reset: true });
    setConnection("connected", "connection.live", "Live");
  } catch (error) {
    handleError(error);
  }
}

function renderWorkspaceOptions() {
  const currentValue = elements.workspaceSelect.value;
  const allOption = createElement("option", "", t("workspace.all", "All projects"));
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
  renderActionWorkspaceOptions();
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
    setConnection("", "connection.refreshing", "Refreshing");
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
    state.selectedTaskId = null;
    state.detail = null;
    renderEmptyState();
  }
  renderRunListToggle();
  setConnection("connected", "connection.live", "Live");
}

function renderRunList() {
  const visibleIds = new Set(state.runs.map((run) => run.run_id));
  state.selectedRunIds = new Set(
    [...state.selectedRunIds].filter((runId) => visibleIds.has(runId)),
  );
  elements.runCount.textContent = formatNumber(state.runs.length);
  if (!state.runs.length) {
    elements.runList.replaceChildren(
      createElement("div", "empty-list", t("run.none", "No AgentBraid runs in this scope.")),
    );
  } else {
    const items = state.runs.map((run) => {
      const row = createElement("div", "run-item-row");
      const selection = createElement("label", "run-select");
      const checkbox = document.createElement("input");
      const name = localizedRunName(run);
      checkbox.type = "checkbox";
      checkbox.checked = state.selectedRunIds.has(run.run_id);
      checkbox.setAttribute(
        "aria-label",
        t("run.selectAria", "Select {name} for deletion", { name }),
      );
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          state.selectedRunIds.add(run.run_id);
        } else {
          state.selectedRunIds.delete(run.run_id);
        }
        updateSelectionControls();
      });
      selection.append(checkbox);

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
      const goal = createElement("div", "run-item-goal", name);
      goal.title = run.goal;
      const footer = createElement("div", "run-item-footer");
      footer.append(createElement("span", "", shortPath(run.workspace)));
      footer.append(createElement("span", "", formatNumber(run.observed_total_tokens)));
      button.append(top, goal, footer);
      button.addEventListener("click", () => selectRun(run.run_id, { focusDetail: true }));
      row.append(selection, button);
      return row;
    });
    elements.runList.replaceChildren(...items);
  }
  elements.loadMoreButton.hidden = !state.hasMore;
  updateSelectionControls();
}

function updateSelectionControls() {
  const selectedCount = state.selectedRunIds.size;
  const visibleCount = state.runs.length;
  elements.selectVisibleRuns.disabled = visibleCount === 0;
  elements.selectVisibleRuns.checked = visibleCount > 0 && selectedCount === visibleCount;
  elements.selectVisibleRuns.indeterminate = selectedCount > 0 && selectedCount < visibleCount;
  elements.deleteSelectedButton.disabled = selectedCount === 0;
  elements.deleteSelectedButton.textContent = selectedCount
    ? t("action.deleteSelectedCount", "Delete selected ({count})", {
      count: formatNumber(selectedCount),
    })
    : t("action.deleteSelected", "Delete selected");
}

function renderRunListToggle() {
  const mobile = MOBILE_BREAKPOINT.matches;
  const hasRuns = state.runs.length > 0;
  const expanded = !mobile
    || !hasRuns
    || !state.selectedRunId
    || state.mobileRunListExpanded;
  elements.runListToggle.hidden = !mobile || !hasRuns;
  elements.runListToggle.setAttribute("aria-expanded", String(expanded));
  elements.runListToggle.textContent = expanded
    ? t("sidebar.hideRuns", "Hide runs ({count})", { count: formatNumber(state.runs.length) })
    : t("sidebar.showRuns", "Show runs ({count})", { count: formatNumber(state.runs.length) });
  elements.runListRegion.hidden = !expanded;
}

function renderScopeSummary() {
  const selected = elements.workspaceSelect.value;
  const relevant = selected
    ? state.workspaces.filter((workspace) => workspace.workspace === selected)
    : state.workspaces;
  const active = relevant.reduce((sum, workspace) => sum + workspace.active_run_count, 0);
  const tokens = relevant.reduce((sum, workspace) => sum + workspace.observed_total_tokens, 0);
  elements.scopeSummary.textContent = t(
    "scope.summary",
    "{active} active · {tokens} observed tokens",
    { active: formatNumber(active), tokens: formatNumber(tokens) },
  );
}

async function selectRun(runId, { focusDetail = false } = {}) {
  if (state.selectedRunId !== runId) {
    state.selectedTaskId = null;
  }
  state.selectedRunId = runId;
  if (MOBILE_BREAKPOINT.matches) {
    state.mobileRunListExpanded = false;
  }
  renderRunList();
  renderRunListToggle();
  await loadRunDetail(runId);
  if (focusDetail && MOBILE_BREAKPOINT.matches) {
    window.requestAnimationFrame(() => elements.runGoal.focus());
  }
}

async function loadRunDetail(runId, { silent = false } = {}) {
  if (!silent) {
    setConnection("", "connection.loadingRun", "Loading run");
  }
  const detail = await api(`/api/v1/runs/${encodeURIComponent(runId)}`);
  if (state.selectedRunId !== runId) {
    return;
  }
  state.detail = detail;
  renderRunDetail();
  setConnection("connected", "connection.live", "Live");
}

function renderEmptyState() {
  elements.emptyState.hidden = false;
  elements.runDetail.hidden = true;
}

function renderRunDetail({ clearMessage = true } = {}) {
  if (!state.detail) {
    renderEmptyState();
    return;
  }
  const { run, usage, actions } = state.detail;
  elements.emptyState.hidden = true;
  elements.runDetail.hidden = false;
  elements.runStatus.textContent = translatedValue("status", run.status);
  elements.runStatus.className = `status-pill ${run.status}`;
  elements.runId.textContent = run.run_id;
  const name = localizedRunName(run);
  elements.runGoal.textContent = name;
  elements.runOriginalGoal.textContent = t(
    "run.originalGoal",
    "Original goal: {goal}",
    { goal: run.request.goal },
  );
  elements.runOriginalGoal.hidden = name === run.request.goal;
  elements.runWorkspace.textContent = run.request.workspace
    || t("common.unknownWorkspace", "Unknown workspace");
  elements.cancelButton.hidden = !actions.can_cancel;
  elements.cancelButton.disabled = !actions.can_cancel;
  const showsApply = run.request.delivery_mode === "integration_branch";
  elements.applyButton.hidden = !showsApply;
  elements.applyButton.disabled = !actions.apply.can_apply;
  renderApplyActionHint(showsApply, actions.apply);
  if (clearMessage) {
    clearActionMessage();
  }

  renderMetrics(usage.totals);
  renderDag(run.tasks);
  renderUsageChart(usage.by_phase, usage.totals);
  renderUsageTable(usage.records);
  renderCapabilities();
  renderTimeline(state.detail.events);
  renderDelivery(run, actions.apply);
}

function renderApplyActionHint(showsApply, readiness) {
  if (!showsApply || readiness.can_apply) {
    elements.applyActionHint.hidden = true;
    elements.applyActionHint.textContent = "";
    elements.applyButton.removeAttribute("aria-describedby");
    return;
  }
  const blockers = readiness.blockers || [];
  if (blockers.length > 1) {
    elements.applyActionHint.textContent = t(
      "actions.applyBlockedMore",
      "Cannot apply: {reason} (+{count} more)",
      { reason: blockers[0], count: formatNumber(blockers.length - 1) },
    );
  } else if (blockers.length === 1) {
    elements.applyActionHint.textContent = t(
      "actions.applyBlocked",
      "Cannot apply: {reason}",
      { reason: blockers[0] },
    );
  } else {
    elements.applyActionHint.textContent = t(
      "actions.applyPending",
      "Apply becomes available after final review and delivery checks pass.",
    );
  }
  elements.applyActionHint.hidden = false;
  elements.applyButton.setAttribute("aria-describedby", elements.applyActionHint.id);
}

function renderMetrics(totals) {
  elements.totalTokens.textContent = formatNumber(totals.observed_total_tokens);
  const ratio = totals.input_tokens
    ? Math.round((totals.cached_input_tokens / totals.input_tokens) * 100)
    : 0;
  elements.cacheRatio.textContent = `${ratio}%`;
  elements.cachedTokens.textContent = t(
    "metrics.cachedValue",
    "{count} cached",
    { count: formatNumber(totals.cached_input_tokens) },
  );
  elements.retryTokens.textContent = formatNumber(totals.retry_tokens);
  elements.duration.textContent = formatDuration(totals.duration_seconds);
  elements.invocations.textContent = t(
    "metrics.invocationsValue",
    "{count} invocations",
    { count: formatNumber(totals.invocation_count) },
  );
  if (totals.legacy_invocation_count) {
    elements.legacyNote.textContent = t(
      "metrics.legacyNote",
      "{count} legacy invocation records do not include attempt/outcome attribution.",
      { count: formatNumber(totals.legacy_invocation_count) },
    );
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
  const selectedTask = tasks.find((task) => task.spec.task_id === state.selectedTaskId);
  if (!selectedTask) {
    state.selectedTaskId = null;
  }
  elements.taskDetail.textContent = tasks.length
    ? t("dag.selectTask", "Select a task node to inspect its assignment and latest result.")
    : t("dag.noPlan", "This run has no persisted task plan yet.");
  if (!tasks.length) {
    const width = Math.max(elements.dagContainer.clientWidth, 280);
    elements.taskDag.style.width = `${width}px`;
    elements.taskDag.setAttribute("viewBox", `0 0 ${width} 230`);
    elements.dagScrollHint.hidden = true;
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
  const graphWidth = (maxDepth + 1) * nodeWidth + maxDepth * xGap;
  const contentWidth = padding * 2 + graphWidth;
  const width = Math.max(elements.dagContainer.clientWidth, contentWidth, 280);
  const height = Math.max(230, padding * 2 + maxRows * nodeHeight + (maxRows - 1) * yGap);
  elements.taskDag.style.width = `${width}px`;
  elements.taskDag.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const positions = new Map();
  const startX = Math.max(padding, (width - graphWidth) / 2);
  for (const [depth, column] of columns.entries()) {
    const columnHeight = column.length * nodeHeight + (column.length - 1) * yGap;
    const startY = Math.max(padding, (height - columnHeight) / 2);
    column.forEach((task, index) => {
      positions.set(task.spec.task_id, {
        x: startX + depth * (nodeWidth + xGap),
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
      class: `dag-node${task.spec.task_id === state.selectedTaskId ? " selected" : ""}`,
      role: "button",
      tabindex: "0",
      "aria-label": t("dag.nodeAria", "{title}, {executor}, {status}", {
        title: task.spec.title,
        executor: translatedValue("executor", task.executor),
        status: translatedValue("status", task.status),
      }),
      transform: `translate(${position.x} ${position.y})`,
    });
    const fullTitle = createSvg("title");
    fullTitle.textContent = task.spec.title;
    group.append(fullTitle);
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
    const attempt = t(
      "dag.attempt",
      "attempt {attempt}",
      { attempt: formatNumber(task.attempt) },
    );
    metadata.textContent = [
      translatedValue("status", task.status),
      translatedValue("executor", task.executor),
      attempt,
    ].join(" · ");
    group.append(title, identifier, metadata);
    const select = () => {
      elements.taskDag.querySelectorAll(".dag-node").forEach((node) => node.classList.remove("selected"));
      group.classList.add("selected");
      state.selectedTaskId = task.spec.task_id;
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
  elements.dagScrollHint.hidden = elements.dagContainer.scrollWidth
    <= elements.dagContainer.clientWidth + 1;
  if (selectedTask) {
    renderTaskDetail(selectedTask);
  }
}

function renderTaskDetail(task) {
  const mutates = task.spec.mutates_workspace
    ? t("common.yes", "yes")
    : t("common.no", "no");
  const parts = [
    `${task.spec.task_id} · ${task.assignment_rationale}`,
    `${t("task.mutatesWorkspace", "Mutates workspace:")} ${mutates}.`,
  ];
  if (task.result?.summary) {
    parts.push(`${t("task.latestResult", "Latest result:")} ${task.result.summary}`);
  }
  elements.taskDetail.textContent = parts.join(" ");
}

function renderUsageChart(buckets, totals) {
  if (!buckets.length) {
    elements.usageChart.replaceChildren(
      createElement(
        "div",
        "empty-list",
        t("usage.none", "No provider usage has been recorded for this run."),
      ),
    );
    return;
  }
  const maximum = Math.max(...buckets.map((bucket) => bucket.observed_total_tokens), 1);
  const rows = buckets.map((bucket) => {
    const row = createElement("div", "usage-row");
    const phase = translatedValue("phase", bucket.phase);
    row.append(createElement("span", "usage-label", phase));
    const chart = createSvg("svg", {
      class: "usage-track",
      viewBox: "0 0 100 15",
      preserveAspectRatio: "none",
      role: "img",
      "aria-label": t("usage.chartAria", "{phase}: {tokens} observed tokens", {
        phase,
        tokens: formatNumber(bucket.observed_total_tokens),
      }),
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
        segment.firstElementChild.textContent = [
          translatedValue("usageSegment", name),
          formatNumber(value),
        ].join(": ");
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
      createElement(
        "div",
        "empty-list",
        t("usage.noReportedTokens", "Usage records contain no reported tokens."),
      ),
    );
  }
}

function renderUsageTable(records) {
  const columns = [
    ["table.phaseTask", "Phase / task"],
    ["table.model", "Model"],
    ["table.attempt", "Attempt"],
    ["table.outcome", "Outcome"],
    ["table.input", "Input"],
    ["table.cached", "Cached"],
    ["table.output", "Output"],
    ["table.reasoning", "Reasoning"],
    ["table.duration", "Duration"],
  ];
  if (!records.length) {
    const row = createElement("tr");
    const cell = createElement(
      "td",
      "subtle empty-table-cell",
      t("table.noRecords", "No provider invocation records."),
    );
    cell.colSpan = 9;
    row.append(cell);
    elements.usageTableBody.replaceChildren(row);
    return;
  }
  const rows = records.map((record) => {
    const row = createElement("tr");
    const phase = translatedValue("phase", record.phase);
    const label = record.task_id ? `${phase} / ${record.task_id}` : phase;
    const values = [
      [label, ""],
      [record.model, "mono"],
      [
        record.phase === "task"
          ? (record.attempt ?? t("table.legacy", "legacy"))
          : t("common.notAvailable", "—"),
        "number",
      ],
      [record.outcome ? translatedValue("outcome", record.outcome) : t("table.legacy", "legacy"), ""],
      [formatNumber(record.input_tokens), "number"],
      [formatNumber(record.cached_input_tokens), "number"],
      [formatNumber(record.output_tokens), "number"],
      [formatNumber(record.reasoning_output_tokens), "number"],
      [formatDuration(record.duration_seconds), "number"],
    ];
    values.forEach(([value, className], index) => {
      const cell = createElement("td", className, value);
      cell.dataset.label = t(columns[index][0], columns[index][1]);
      row.append(cell);
    });
    return row;
  });
  elements.usageTableBody.replaceChildren(...rows);
}

function renderCapabilities() {
  if (!state.capabilities.length) {
    elements.capabilityList.replaceChildren(
      createElement(
        "div",
        "empty-list",
        t("capabilities.none", "No provider capability observations yet."),
      ),
    );
    return;
  }
  const items = state.capabilities.map((capability) => {
    const item = createElement("div", "capability-item");
    item.append(
      createElement(
        "div",
        "capability-model",
        `${translatedValue("executor", capability.executor)} / ${capability.model}`,
      ),
    );
    item.append(statusPill(capability.status));
    item.append(
      createElement(
        "div",
        "capability-meta",
        t("capabilities.meta", "{successes} successes · {failures} failures · {duration} avg", {
          successes: formatNumber(capability.successes),
          failures: formatNumber(capability.failures),
          duration: formatDuration(capability.average_latency_seconds),
        }),
      ),
    );
    return item;
  });
  elements.capabilityList.replaceChildren(...items);
}

const eventLabels = {
  "run.created": ["event.run.created", "Run created"],
  "run.planned": ["event.run.planned", "Plan persisted"],
  "run.status_changed": ["event.run.status_changed", "Run status changed"],
  "run.lead_thread_updated": ["event.run.lead_thread_updated", "Lead thread updated"],
  "run.reviewed": ["event.run.reviewed", "Final review recorded"],
  "run.cancelled": ["event.run.cancelled", "Run cancelled"],
  "run.applied": ["event.run.applied", "Integration applied"],
  "run.renamed": ["event.run.renamed", "Run names updated"],
  "task.status_changed": ["event.task.status_changed", "Task status changed"],
  "task.claimed": ["event.task.claimed", "Task claimed"],
  "task.worktree_assigned": ["event.task.worktree_assigned", "Worktree assigned"],
  "task.result_submitted": ["event.task.result_submitted", "Task result submitted"],
};

function translatedEventValue(value) {
  for (const group of ["status", "outcome", "executor"]) {
    if (valueLabels[group][value]) {
      return translatedValue(group, value);
    }
  }
  return String(value);
}

function eventSummary(event) {
  const payload = event.payload || {};
  if (payload.from || payload.to) {
    return [payload.from, payload.to].filter(Boolean).map(translatedEventValue).join(" → ");
  }
  if (payload.outcome || payload.status) {
    return [
      payload.outcome ? translatedValue("outcome", payload.outcome) : null,
      payload.status ? translatedValue("status", payload.status) : null,
      payload.attempt
        ? t("event.attempt", "attempt {attempt}", { attempt: formatNumber(payload.attempt) })
        : null,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (payload.executor || payload.claimed_by) {
    return [
      payload.executor ? translatedValue("executor", payload.executor) : null,
      payload.claimed_by,
    ].filter(Boolean).join(" · ");
  }
  if (event.task_id) {
    return event.task_id;
  }
  return t("event.durableState", "Durable state event");
}

function renderTimeline(events) {
  if (!events.length) {
    elements.eventTimeline.replaceChildren(
      createElement(
        "li",
        "empty-list",
        t("event.none", "No durable events have been recorded."),
      ),
    );
    return;
  }
  const items = events.map((event) => {
    const item = createElement("li", "timeline-item");
    item.append(createElement("time", "timeline-time", formatDate(event.created_at)));
    item.append(createElement("span", "timeline-rail"));
    const copy = createElement("div", "timeline-copy");
    const label = eventLabels[event.event_type];
    copy.append(
      createElement("strong", "", label ? t(label[0], label[1]) : event.event_type),
    );
    const taskPrefix = event.task_id ? `${event.task_id} · ` : "";
    copy.append(createElement("span", "", `${taskPrefix}${eventSummary(event)}`));
    item.append(copy);
    return item;
  });
  elements.eventTimeline.replaceChildren(...items);
}

function renderDelivery(run, readiness) {
  const entries = [
    [
      t("delivery.mode", "Delivery mode"),
      translatedValue("deliveryMode", run.request.delivery_mode),
    ],
    [
      t("delivery.integrationBranch", "Integration branch"),
      run.integration_branch || t("common.notAvailable", "—"),
    ],
    [
      t("delivery.expectedBranch", "Expected branch"),
      readiness.expected_branch || t("common.notAvailable", "—"),
    ],
    [
      t("delivery.expectedCommit", "Expected commit"),
      readiness.expected_commit || t("common.notAvailable", "—"),
    ],
    [
      t("delivery.currentBranch", "Current branch"),
      readiness.current_branch || t("common.notAvailable", "—"),
    ],
    [
      t("delivery.currentCommit", "Current commit"),
      readiness.current_commit || t("common.notAvailable", "—"),
    ],
  ];
  const nodes = [];
  for (const [term, description] of entries) {
    nodes.push(createElement("dt", "", term), createElement("dd", "", description));
  }
  elements.deliveryDetail.replaceChildren(...nodes);
  const blockers = readiness.blockers.map((blocker) => createElement("div", "blocker", blocker));
  elements.applyBlockers.replaceChildren(...blockers);
  elements.finalSummary.textContent = run.final_summary
    || run.error
    || t("delivery.finalPending", "Final review has not completed.");
}

function integerValue(input) {
  return Number.parseInt(input.value, 10);
}

function addModelOption(group, model) {
  const value = String(model || "").trim();
  if (!value || state.modelOptions[group].includes(value)) {
    return;
  }
  state.modelOptions[group] = [value, ...state.modelOptions[group]];
  renderModelOptions();
}

async function workspaceSettings(workspace) {
  const query = new URLSearchParams({ workspace });
  return api(`/api/v1/settings?${query.toString()}`);
}

function populateStartSettings(settings) {
  elements.startCodexModel.value = settings.codex_model || "";
  elements.startHostModel.value = settings.host_model;
  elements.startRouting.value = settings.routing_mode;
  elements.startDelivery.value = settings.delivery_mode;
  elements.startWorkspaceMode.value = settings.workspace_mode;
  elements.startParallel.value = settings.max_parallel_codex;
  elements.startAttempts.value = settings.max_task_attempts;
  elements.startTimeout.value = settings.codex_timeout_seconds;
  elements.startOutputBytes.value = settings.max_output_bytes;
}

function settingsFieldMap() {
  return {
    codex_model: elements.settingsCodexModel,
    max_parallel_codex: elements.settingsParallel,
    max_task_attempts: elements.settingsAttempts,
    codex_timeout_seconds: elements.settingsTimeout,
    max_output_bytes: elements.settingsOutputBytes,
    codex_binary: elements.settingsCodexBinary,
    worktree_dir: elements.settingsWorktreeDir,
  };
}

function populateSettingsForm(payload) {
  const settings = payload.settings;
  elements.settingsWorkspace.value = settings.workspace;
  elements.settingsCodexModel.value = settings.codex_model || "";
  elements.settingsHostModel.value = settings.host_model;
  elements.settingsRouting.value = settings.routing_mode;
  elements.settingsDelivery.value = settings.delivery_mode;
  elements.settingsWorkspaceMode.value = settings.workspace_mode;
  elements.settingsParallel.value = settings.max_parallel_codex;
  elements.settingsAttempts.value = settings.max_task_attempts;
  elements.settingsTimeout.value = settings.codex_timeout_seconds;
  elements.settingsOutputBytes.value = settings.max_output_bytes;
  elements.settingsCodexBinary.value = settings.codex_binary;
  elements.settingsWorktreeDir.value = settings.worktree_dir;
  elements.settingsDatabasePath.value = payload.database_path;
  elements.settingsStateDir.value = payload.state_dir;
  elements.settingsRestartNote.hidden = !payload.requires_restart;
  const locked = new Set(payload.locked_fields || []);
  for (const [field, input] of Object.entries(settingsFieldMap())) {
    input.disabled = locked.has(field);
    input.title = locked.has(field)
      ? t("form.environmentLocked", "Controlled by an environment variable")
      : "";
  }
}

function startRequestPayload() {
  const codexModel = elements.startCodexModel.value.trim();
  const hostModel = elements.startHostModel.value.trim();
  const constraints = elements.startConstraints.value
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  return {
    request: {
      goal: elements.startGoal.value.trim(),
      workspace: elements.startWorkspace.value,
      host_model: hostModel,
      constraints,
      delivery_mode: elements.startDelivery.value,
      execution: {
        codex_model: codexModel || null,
        host_model: hostModel,
        routing_mode: elements.startRouting.value,
        delivery_mode: elements.startDelivery.value,
        workspace_mode: elements.startWorkspaceMode.value,
        max_parallel_codex: integerValue(elements.startParallel),
        max_task_attempts: integerValue(elements.startAttempts),
        codex_timeout_seconds: integerValue(elements.startTimeout),
        max_output_bytes: integerValue(elements.startOutputBytes),
      },
    },
    save_defaults: elements.startSaveDefaults.checked,
  };
}

function settingsRequestPayload() {
  const codexModel = elements.settingsCodexModel.value.trim();
  return {
    settings: {
      workspace: elements.settingsWorkspace.value,
      codex_binary: elements.settingsCodexBinary.value.trim(),
      codex_model: codexModel || null,
      host_model: elements.settingsHostModel.value.trim(),
      routing_mode: elements.settingsRouting.value,
      delivery_mode: elements.settingsDelivery.value,
      workspace_mode: elements.settingsWorkspaceMode.value,
      max_parallel_codex: integerValue(elements.settingsParallel),
      max_task_attempts: integerValue(elements.settingsAttempts),
      codex_timeout_seconds: integerValue(elements.settingsTimeout),
      max_output_bytes: integerValue(elements.settingsOutputBytes),
      worktree_dir: elements.settingsWorktreeDir.value.trim(),
    },
  };
}

async function openStartRunDialog() {
  const workspace = concreteWorkspace();
  if (!workspace) {
    return;
  }
  elements.startRunForm.reset();
  renderActionWorkspaceOptions();
  elements.startWorkspace.value = workspace;
  const payload = await workspaceSettings(workspace);
  populateStartSettings(payload.settings);
  elements.startSaveDefaults.checked = false;
  elements.startRunDialog.showModal();
  elements.startGoal.focus();
}

async function openSettingsDialog() {
  const workspace = concreteWorkspace();
  if (!workspace) {
    return;
  }
  renderActionWorkspaceOptions();
  elements.settingsWorkspace.value = workspace;
  populateSettingsForm(await workspaceSettings(workspace));
  elements.settingsDialog.showModal();
}

function renderDeletePreview() {
  if (!state.deletePreviews.length) {
    elements.deletePreview.replaceChildren();
    elements.confirmDelete.disabled = true;
    return;
  }
  const runById = new Map(state.runs.map((run) => [run.run_id, run]));
  const items = state.deletePreviews.map((preview) => {
    const item = createElement("article", `cleanup-item${preview.deletable ? "" : " blocked"}`);
    const run = runById.get(preview.run_id);
    const heading = createElement("div", "cleanup-heading");
    heading.append(
      createElement("strong", "", run ? localizedRunName(run) : preview.run_id),
      createElement(
        "span",
        `cleanup-state ${preview.deletable ? "ready" : "blocked"}`,
        preview.deletable
          ? t("cleanup.ready", "Ready to delete")
          : t("cleanup.blocked", "Deletion blocked"),
      ),
    );
    item.append(heading);
    const artifacts = createElement("ul", "cleanup-artifacts");
    for (const artifact of preview.artifacts) {
      const label = translatedValue("artifactKind", artifact.kind);
      const status = artifact.removable
        ? t("cleanup.removable", "removable")
        : t("cleanup.preserved", "preserved");
      const detail = artifact.detail ? ` · ${artifact.detail}` : "";
      artifacts.append(
        createElement(
          "li",
          artifact.removable ? "" : "blocked",
          `${label}: ${artifact.identifier} · ${status}${detail}`,
        ),
      );
    }
    item.append(artifacts);
    for (const blocker of preview.blockers) {
      item.append(createElement("p", "cleanup-blocker", blocker));
    }
    return item;
  });
  elements.deletePreview.replaceChildren(...items);
  const deletable = state.deletePreviews.some((preview) => preview.deletable);
  elements.deleteConfirmation.disabled = !deletable;
  elements.confirmDelete.disabled = !deletable
    || elements.deleteConfirmation.value !== "delete-selected-runs";
}

async function openDeleteDialog() {
  const runIds = state.runs
    .filter((run) => state.selectedRunIds.has(run.run_id))
    .map((run) => run.run_id);
  if (!runIds.length) {
    return;
  }
  const payload = await api("/api/v1/runs/delete-preview", {
    method: "POST",
    body: JSON.stringify({ run_ids: runIds }),
  });
  state.deletePreviews = payload.previews;
  elements.deleteConfirmation.value = "";
  renderDeletePreview();
  elements.deleteDialog.showModal();
  if (state.deletePreviews.some((preview) => preview.deletable)) {
    elements.deleteConfirmation.focus();
  }
}

async function refreshAll({ silent = false } = {}) {
  if (state.pollInFlight) {
    return;
  }
  state.pollInFlight = true;
  try {
    const [workspacePayload, capabilityPayload, modelPayload] = await Promise.all([
      api("/api/v1/workspaces"),
      api("/api/v1/capabilities"),
      api("/api/v1/model-options"),
    ]);
    state.workspaces = workspacePayload.workspaces;
    state.capabilities = capabilityPayload.capabilities;
    state.modelOptions = modelPayload;
    renderWorkspaceOptions();
    renderModelOptions();
    await loadRuns({ reset: true, silent });
  } catch (error) {
    handleError(error);
  } finally {
    state.pollInFlight = false;
  }
}

function handleError(error) {
  const message = error instanceof Error
    ? error.message
    : t("error.requestFailedGeneric", "Dashboard request failed");
  setConnection("error", "connection.disconnected", "Disconnected");
  showActionMessage(message, "error");
}

elements.workspaceSelect.addEventListener("change", () => {
  state.selectedRunIds.clear();
  state.deletePreviews = [];
  state.selectedRunId = null;
  state.selectedTaskId = null;
  state.mobileRunListExpanded = false;
  state.detail = null;
  loadRuns({ reset: true }).catch(handleError);
});

elements.languageSelect.addEventListener("change", () => {
  applyLocale(elements.languageSelect.value, { persist: true, announce: true });
});

elements.runListToggle.addEventListener("click", () => {
  state.mobileRunListExpanded = !state.mobileRunListExpanded;
  renderRunListToggle();
});

elements.refreshButton.addEventListener("click", () => refreshAll());

elements.selectVisibleRuns.addEventListener("change", () => {
  if (elements.selectVisibleRuns.checked) {
    state.selectedRunIds = new Set(state.runs.map((run) => run.run_id));
  } else {
    state.selectedRunIds.clear();
  }
  renderRunList();
});

elements.deleteSelectedButton.addEventListener("click", () => {
  openDeleteDialog().catch(handleError);
});

elements.startRunButton.addEventListener("click", () => {
  openStartRunDialog().catch(handleError);
});

elements.closeStartRun.addEventListener("click", () => elements.startRunDialog.close());

elements.startWorkspace.addEventListener("change", async () => {
  try {
    populateStartSettings((await workspaceSettings(elements.startWorkspace.value)).settings);
  } catch (error) {
    handleError(error);
  }
});

elements.startRunForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = elements.startRunForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    const requestPayload = startRequestPayload();
    const payload = await api("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify(requestPayload),
    });
    addModelOption("codex", requestPayload.request.execution.codex_model);
    addModelOption("host", requestPayload.request.host_model);
    state.selectedRunId = payload.run.run_id;
    elements.startRunDialog.close();
    showToast(t("toast.runStarted", "Run started."));
    await refreshAll();
  } catch (error) {
    handleError(error);
  } finally {
    submitButton.disabled = false;
  }
});

elements.settingsButton.addEventListener("click", () => {
  openSettingsDialog().catch(handleError);
});

elements.closeSettings.addEventListener("click", () => elements.settingsDialog.close());

elements.settingsWorkspace.addEventListener("change", async () => {
  try {
    populateSettingsForm(await workspaceSettings(elements.settingsWorkspace.value));
  } catch (error) {
    handleError(error);
  }
});

elements.settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = elements.settingsForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    const requestPayload = settingsRequestPayload();
    const payload = await api("/api/v1/settings", {
      method: "PUT",
      body: JSON.stringify(requestPayload),
    });
    addModelOption("codex", requestPayload.settings.codex_model);
    addModelOption("host", requestPayload.settings.host_model);
    populateSettingsForm(payload);
    showToast(
      payload.requires_restart
        ? t("toast.settingsRestart", "Settings saved. Restart Dashboard and MCP to apply runtime changes.")
        : t("toast.settingsSaved", "Workspace settings saved."),
    );
    await refreshAll({ silent: true });
  } catch (error) {
    handleError(error);
  } finally {
    submitButton.disabled = false;
  }
});

elements.renameButton.addEventListener("click", () => {
  if (!state.detail) {
    return;
  }
  const { run } = state.detail;
  const names = run.display_names || {};
  elements.renameEn.value = names.en || run.request.goal;
  elements.renameZhTw.value = names["zh-TW"] || run.request.goal;
  elements.renameZhCn.value = names["zh-CN"] || run.request.goal;
  elements.renameDialog.showModal();
  elements.renameEn.focus();
});

elements.closeRename.addEventListener("click", () => elements.renameDialog.close());

elements.renameForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedRunId) {
    return;
  }
  const submitButton = elements.renameForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    await api(`/api/v1/runs/${encodeURIComponent(state.selectedRunId)}/names`, {
      method: "PATCH",
      body: JSON.stringify({
        display_names: {
          en: elements.renameEn.value.trim(),
          "zh-TW": elements.renameZhTw.value.trim(),
          "zh-CN": elements.renameZhCn.value.trim(),
        },
      }),
    });
    elements.renameDialog.close();
    showToast(t("toast.runRenamed", "Run names updated."));
    await refreshAll();
  } catch (error) {
    handleError(error);
  } finally {
    submitButton.disabled = false;
  }
});

elements.closeDelete.addEventListener("click", () => elements.deleteDialog.close());

elements.deleteConfirmation.addEventListener("input", renderDeletePreview);

elements.deleteDialog.addEventListener("close", () => {
  state.deletePreviews = [];
  elements.deleteConfirmation.value = "";
  renderDeletePreview();
});

elements.deleteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (elements.deleteConfirmation.value !== "delete-selected-runs") {
    return;
  }
  const runIds = state.deletePreviews.map((preview) => preview.run_id);
  if (!runIds.length) {
    return;
  }
  elements.confirmDelete.disabled = true;
  try {
    const payload = await api("/api/v1/runs/delete", {
      method: "POST",
      body: JSON.stringify({
        run_ids: runIds,
        confirmation: elements.deleteConfirmation.value,
      }),
    });
    const deleted = payload.results.filter((result) => result.deleted);
    const blocked = payload.results.length - deleted.length;
    for (const result of deleted) {
      state.selectedRunIds.delete(result.run_id);
      if (state.selectedRunId === result.run_id) {
        state.selectedRunId = null;
      }
    }
    elements.deleteDialog.close();
    showToast(t(
      "toast.runsDeleted",
      "Deleted {deleted} run(s); {blocked} preserved.",
      { deleted: formatNumber(deleted.length), blocked: formatNumber(blocked) },
    ));
    await refreshAll();
  } catch (error) {
    elements.confirmDelete.disabled = false;
    handleError(error);
  }
});

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
    showToast(t("toast.cancelled", "Run cancelled."));
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
    showToast(t("toast.applied", "Reviewed integration branch applied."));
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

MOBILE_BREAKPOINT.addEventListener("change", () => {
  if (MOBILE_BREAKPOINT.matches) {
    state.mobileRunListExpanded = false;
  }
  renderRunListToggle();
  if (state.detail) {
    renderDag(state.detail.run.tasks);
  }
});

let resizeFrame = null;
window.addEventListener("resize", () => {
  if (resizeFrame !== null) {
    window.cancelAnimationFrame(resizeFrame);
  }
  resizeFrame = window.requestAnimationFrame(() => {
    resizeFrame = null;
    if (state.detail) {
      renderDag(state.detail.run.tasks);
    }
  });
});

window.setInterval(() => {
  if (!document.hidden) {
    refreshAll({ silent: true });
  }
}, 2000);

initialize();
