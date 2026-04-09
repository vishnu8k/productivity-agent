const API_BASE = "";
const EFFORT_MAP = { easy: 1, medium: 2, hard: 3, tough: 4 };

let authConfig = { googleClientId: "", calendarEnabled: false };
let authState = { authenticated: false, csrfToken: null, calendarConnected: false, user: null };
let lastRequest = null;
let coldStartPending = false;
let currentPlan = [];
let currentUnscheduled = [];
let currentSummary = "";
let currentActionsProposed = [];
let currentDetectedState = "normal";
let pendingTasks = [];
let googleInitAttempts = 0;

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function userInitial() {
  const seed = authState.user?.name || authState.user?.email || "G";
  return seed.charAt(0).toUpperCase();
}

function storageKey() {
  return authState.user ? `plan_${authState.user.sub}` : null;
}

function setPlannerEnabled(enabled) {
  $("userInput").disabled = !enabled;
  $("sendBtn").disabled = !enabled;
  $("userInput").placeholder = enabled
    ? "Describe your tasks... include priority, difficulty, and deadlines"
    : "Sign in with Google to begin";
  $("authBanner").style.display = enabled ? "none" : "block";
}

function resetPlanView() {
  $("planContent").innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
          <rect x="8" y="8" width="32" height="32" rx="8" stroke="currentColor" stroke-width="1.5" stroke-dasharray="4 3"/>
          <path d="M16 24h16M24 16v16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
      </div>
      <p class="empty-title">No plan yet</p>
      <p class="empty-desc">Sign in and send your tasks in the chat to build a schedule.</p>
    </div>`;
  $("planMeta").textContent = authState.authenticated ? "Waiting for your tasks..." : "Sign in to unlock your plan.";
  $("stateDisplay").style.display = "none";
}

function buildSavedRequest() {
  return {
    input_text: lastRequest?.input_text || "",
    state_inputs: lastRequest?.state_inputs || ($("energyInput").value ? { energy: $("energyInput").value } : null),
    confirm_actions: false,
    current_plan: currentPlan,
    current_unscheduled: currentUnscheduled,
    current_summary: currentSummary,
  };
}

function savePlan(state) {
  const key = storageKey();
  if (!key) return;
  localStorage.setItem(key, JSON.stringify({ ...state, ts: Date.now() }));
}

function loadPlan() {
  const key = storageKey();
  if (!key) return null;
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  const parsed = JSON.parse(raw);
  const ageHours = (Date.now() - parsed.ts) / 1000 / 60 / 60;
  if (ageHours > 24) {
    localStorage.removeItem(key);
    return null;
  }
  return parsed;
}

function addMessage(text, role) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="msg-avatar">${role === "agent" ? "AI" : userInitial()}</div><div class="msg-bubble">${esc(text).replace(/\n/g, "<br>")}</div>`;
  $("chatMessages").appendChild(div);
  $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
  return div;
}

function addLoadingMessage() {
  const div = document.createElement("div");
  div.className = "msg agent";
  div.innerHTML = `<div class="msg-avatar">AI</div><div class="msg-bubble loading">Thinking...</div>`;
  $("chatMessages").appendChild(div);
  $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
  return div;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || "GET").toUpperCase();
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (authState.csrfToken && ["POST", "PUT", "PATCH", "DELETE"].includes(method) && path !== "/auth/google") {
    headers.set("X-CSRF-Token", authState.csrfToken);
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, method, headers, credentials: "same-origin" });
  const body = (res.headers.get("content-type") || "").includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const err = new Error(body.detail || body.message || body || "Request failed.");
    err.status = res.status;
    throw err;
  }
  return body;
}

function renderAuth() {
  $("authName").textContent = authState.authenticated ? (authState.user.name || authState.user.email) : "Sign in required";
  $("authEmail").textContent = authState.authenticated
    ? (authState.calendarConnected ? "Google Calendar connected" : "Signed in. Connect Calendar when you are ready.")
    : "Use Google to access your private plan.";
  $("authAvatar").textContent = authState.authenticated ? userInitial() : "G";
  $("logoutBtn").style.display = authState.authenticated ? "inline-flex" : "none";
  $("calendarConnectBtn").style.display = authState.authenticated && authConfig.calendarEnabled && !authState.calendarConnected ? "inline-flex" : "none";
  $("googleSignInButton").style.display = authState.authenticated ? "none" : "block";
  setPlannerEnabled(authState.authenticated);
}

function setStateBadge(state) {
  if (!state) {
    $("stateDisplay").style.display = "none";
    return;
  }
  $("stateBadge").textContent = state.charAt(0).toUpperCase() + state.slice(1);
  $("stateBadge").className = `state-badge ${state}`;
  $("stateDisplay").style.display = "flex";
}

function getCapacityClass(pct) {
  if (pct <= 50) return "low";
  if (pct <= 80) return "medium";
  return "high";
}

function showTaskCards(tasks) {
  const cards = tasks.map((task, i) => `
    <div class="task-card" id="tcard-${i}">
      <div class="task-card-title">${esc(task.title || task)}</div>
      <div class="task-card-fields">
        <div class="field-group">
          <label class="field-label">Priority</label>
          <div class="segmented-control">
            ${["high", "medium", "low"].map((v) => `<button type="button" class="segment-btn" data-action="segment" data-card="${i}" data-field="priority" data-value="${v}">${v}</button>`).join("")}
          </div>
        </div>
        <div class="field-group">
          <label class="field-label">Difficulty</label>
          <div class="segmented-control">
            ${["easy", "medium", "hard", "tough"].map((v) => `<button type="button" class="segment-btn" data-action="segment" data-card="${i}" data-field="difficulty" data-value="${v}">${v}</button>`).join("")}
          </div>
        </div>
        <div class="field-group">
          <label class="field-label">Deadline (optional)</label>
          <input type="date" class="field-date" id="deadline-${i}" data-action="deadline" data-card="${i}" />
        </div>
        <div class="field-group">
          <label class="field-label">Work Style</label>
          <div class="segmented-control">
            <button type="button" class="segment-btn selected" data-action="work-style" data-card="${i}" data-value="single">one sitting</button>
            <button type="button" class="segment-btn" data-action="work-style" data-card="${i}" data-value="progressive">progressive</button>
          </div>
          <div class="spread-days-wrap" id="spread-${i}" style="display:none">
            <div class="stepper-wrap">
              <div>
                <label class="field-label">Spread across days</label>
                <div class="stepper-control">
                  <button type="button" class="stepper-btn" data-action="step" data-card="${i}" data-delta="-1">-</button>
                  <input type="number" class="stepper-input" id="stepper-${i}" data-action="step-input" data-card="${i}" value="2" min="2" max="30" />
                  <button type="button" class="stepper-btn" data-action="step" data-card="${i}" data-delta="1">+</button>
                </div>
              </div>
              <div class="effort-preview" id="preview-${i}">Select difficulty</div>
            </div>
          </div>
        </div>
      </div>
    </div>`).join("");
  const div = document.createElement("div");
  div.className = "msg agent";
  div.id = "taskCardMsg";
  div.innerHTML = `<div class="msg-avatar">AI</div><div class="msg-bubble" style="max-width:95%;width:100%"><div style="margin-bottom:10px;font-size:13px;">A few details are missing. Fill them in and I will build the plan.</div><div class="task-cards-container">${cards}</div><button type="button" class="submit-cards-btn" data-action="submit-cards">Build My Plan</button></div>`;
  $("chatMessages").appendChild(div);
  pendingTasks = tasks.map((task) => ({ title: task.title || task, priority: null, difficulty: null, deadline: null, work_style: "single", spread_days: 2 }));
}

function updatePreview(i) {
  const task = pendingTasks[i];
  const preview = $(`preview-${i}`);
  if (!preview) return;
  if (!task.difficulty) return void (preview.textContent = "Select difficulty");
  const total = EFFORT_MAP[task.difficulty] || 1;
  const days = task.work_style === "progressive" ? task.spread_days || 2 : 1;
  preview.textContent = `About ${Math.max(1, Math.ceil(total / days))} pt/day`;
}

function renderPlan(data) {
  const detectedState = data.detected_state || currentDetectedState || "normal";
  const actions = data.actions_proposed ?? currentActionsProposed;
  setStateBadge(detectedState || null);
  const totalTasks = (data.plan || []).reduce((sum, day) => sum + day.tasks.length, 0);
  $("planMeta").textContent = `${totalTasks} task${totalTasks !== 1 ? "s" : ""} | ${(data.plan || []).length} day${(data.plan || []).length !== 1 ? "s" : ""} | ${detectedState || ""}`;
  const parts = [];
  if (data.daily_summary) parts.push(`<div class="section-card summary-card"><div class="section-header"><div class="section-icon">S</div><span class="section-title">Daily Summary</span></div><div class="summary-text">${esc(data.daily_summary)}</div></div>`);
  if (data.adjustments_applied?.length) parts.push(`<div class="section-card"><div class="section-header"><div class="section-icon">A</div><span class="section-title">Adjustments Applied</span><span class="section-count">${data.adjustments_applied.length}</span></div><div class="adjustments-list">${data.adjustments_applied.map((a) => `<div class="adjustment-item">${esc(a)}</div>`).join("")}</div></div>`);
  if (data.plan?.length) parts.push(`<div class="section-card"><div class="section-header"><div class="section-icon">P</div><span class="section-title">Schedule</span><span class="section-count">${data.plan.length} day${data.plan.length !== 1 ? "s" : ""}</span></div><div class="days-section">${data.plan.map((day) => `<div class="day-card"><div class="day-header"><div class="day-number">D${Number(day.day)}</div><div class="day-info"><div class="day-label">Day ${Number(day.day)}</div><div class="day-date">${esc(day.date || "")}</div></div><div class="day-capacity-info"><div class="day-pts">${Number(day.total_effort_points || 0)} pts</div><div class="capacity-bar-wrap"><div class="capacity-bar"><div class="capacity-fill ${getCapacityClass(Number(day.capacity_percentage || 0))}" style="width:${Math.min(Number(day.capacity_percentage || 0), 100)}%"></div></div><span class="capacity-pct">${Number(day.capacity_percentage || 0)}%</span></div></div></div><div class="task-list">${day.tasks.map((task) => `<div class="task-item"><div class="priority-indicator ${esc(task.effective_priority)}"></div><div class="task-info"><div class="task-title">${esc(task.title)}</div><div class="task-meta">${esc(task.effective_priority)}${task.domain_added ? " | auto-added" : ""}</div></div><div class="task-badges"><span class="badge badge-diff">${esc(task.difficulty)}</span><span class="badge badge-pts">${Number(task.effort_points || 0)}pt</span>${task.deadline ? `<span class="badge badge-dl">${esc(task.deadline)}</span>` : ""}${task.domain_added ? `<span class="badge badge-domain">auto</span>` : ""}${task.session_number ? `<span class="badge badge-session">${Number(task.session_number)}/${Number(task.total_sessions || 0)}</span>` : ""}</div></div>`).join("")}</div></div>`).join("")}</div></div>`);
  if (data.unscheduled_tasks?.length) parts.push(`<div class="section-card unscheduled-card"><div class="section-header"><div class="section-icon">U</div><span class="section-title">Unscheduled Tasks</span><span class="section-count">${data.unscheduled_tasks.length}</span></div><div class="unscheduled-list">${data.unscheduled_tasks.map((task) => `<div class="unscheduled-item"><div class="unscheduled-info"><span class="unscheduled-title">${esc(task.title)}</span><span class="unscheduled-reason">${esc(task.unscheduled_reason || "unscheduled")}</span></div><div class="unscheduled-actions"><select class="unscheduled-select" data-task-id="${esc(task.task_id)}">${Array.from({ length: 30 }, (_, i) => `<option value="${i + 1}">Day ${i + 1}</option>`).join("")}</select><button type="button" class="unscheduled-btn" data-action="schedule" data-task-id="${esc(task.task_id)}">Add</button></div></div>`).join("")}</div></div>`);
  if (actions?.length) parts.push(`<div class="section-card actions-card"><div class="section-header"><div class="section-icon">C</div><span class="section-title">Calendar Actions</span><span class="section-count">${actions.length}</span></div><div class="actions-list">${actions.map((a) => `<div class="action-item"><div class="action-dot"></div><span>${esc(a.tool)} - ${esc(a.action_type)}</span></div>`).join("")}</div><div class="actions-footer"><button type="button" class="confirm-btn-full" data-action="confirm-calendar">${authState.calendarConnected ? "Confirm and add to Google Calendar" : "Connect Google Calendar"}</button></div></div>`);
  $("planContent").innerHTML = parts.length ? parts.join("") : `
    <div class="empty-state">
      <div class="empty-icon">
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
          <rect x="8" y="8" width="32" height="32" rx="8" stroke="currentColor" stroke-width="1.5" stroke-dasharray="4 3"/>
          <path d="M16 24h16M24 16v16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
      </div>
      <p class="empty-title">No plan yet</p>
      <p class="empty-desc">Send your tasks in the chat and your AI-powered schedule will appear here.</p>
    </div>`;
}

async function refreshAuth() {
  authConfig = await api("/auth/config");
  const payload = await api("/auth/me");
  authState = { authenticated: !!payload.authenticated, csrfToken: payload.csrfToken || null, calendarConnected: !!payload.calendarConnected, user: payload.user || null };
  renderAuth();
  if (authState.authenticated) {
    const saved = loadPlan();
    if (saved?.plan?.length) {
      currentPlan = saved.plan;
      currentUnscheduled = saved.unscheduled || [];
      currentSummary = saved.summary || "";
      currentActionsProposed = saved.actions || [];
      currentDetectedState = saved.state || "normal";
      lastRequest = saved.request || null;
      renderPlan({ plan: currentPlan, unscheduled_tasks: currentUnscheduled, daily_summary: currentSummary, detected_state: currentDetectedState, adjustments_applied: [], actions_proposed: currentActionsProposed });
    } else {
      resetPlanView();
    }
  } else {
    currentPlan = [];
    currentUnscheduled = [];
    currentSummary = "";
    currentActionsProposed = [];
    currentDetectedState = "normal";
    lastRequest = null;
    resetPlanView();
  }
}

function initGoogle() {
  if (!authConfig.googleClientId) return;
  if (!window.google?.accounts?.id) {
    googleInitAttempts += 1;
    if (googleInitAttempts <= 20) {
      window.setTimeout(initGoogle, 500);
    } else {
      addMessage("Google Sign-In could not finish loading. Refresh the page once and try again.", "agent");
    }
    return;
  }
  const slot = $("googleSignInButton");
  slot.innerHTML = "";
  googleInitAttempts = 0;
  google.accounts.id.initialize({ client_id: authConfig.googleClientId, callback: onGoogleCredential, auto_select: false, use_fedcm_for_prompt: true });
  google.accounts.id.renderButton(slot, { theme: "outline", size: "large", shape: "pill", text: "signin_with", width: 220 });
}

async function onGoogleCredential(res) {
  try {
    const payload = await api("/auth/google", { method: "POST", body: JSON.stringify({ credential: res.credential }) });
    authState = { authenticated: !!payload.authenticated, csrfToken: payload.csrfToken || null, calendarConnected: !!payload.calendarConnected, user: payload.user || null };
    currentPlan = [];
    currentUnscheduled = [];
    currentSummary = "";
    currentActionsProposed = [];
    currentDetectedState = "normal";
    lastRequest = null;
    renderAuth();
    const saved = loadPlan();
    if (saved?.plan?.length) {
      currentPlan = saved.plan;
      currentUnscheduled = saved.unscheduled || [];
      currentSummary = saved.summary || "";
      currentActionsProposed = saved.actions || [];
      currentDetectedState = saved.state || "normal";
      lastRequest = saved.request || null;
      renderPlan({ plan: currentPlan, unscheduled_tasks: currentUnscheduled, daily_summary: currentSummary, detected_state: currentDetectedState, adjustments_applied: [], actions_proposed: currentActionsProposed });
    } else {
      resetPlanView();
    }
    addMessage("Signed in successfully. Your planning workspace is ready.", "agent");
  } catch (err) {
    addMessage(err.message || "Sign-in failed. Please try again.", "agent");
  }
}

async function sendMessage() {
  if (!authState.authenticated) return addMessage("Sign in with Google first so your plan stays private.", "agent");
  const text = $("userInput").value.trim();
  if (!text) return;
  $("userInput").value = "";
  addMessage(text, "user");
  if ($("taskCardMsg")) return;
  return processInput(text, false);
}

async function processInput(text, resetContext) {
  const loading = addLoadingMessage();
  $("sendBtn").disabled = true;
  try {
    if (coldStartPending) {
      const data = await api("/cold-start", { method: "POST", body: JSON.stringify({ response: text }) });
      coldStartPending = false;
      loading.remove();
      addMessage(data.message, "agent");
      return;
    }
    const payload = {
      input_text: text,
      state_inputs: $("energyInput").value ? { energy: $("energyInput").value } : null,
      confirm_actions: false,
      current_plan: resetContext ? [] : currentPlan,
      current_unscheduled: resetContext ? [] : currentUnscheduled,
      current_summary: resetContext ? "" : currentSummary,
    };
    lastRequest = payload;
    const data = await api("/plan", { method: "POST", body: JSON.stringify(payload) });
    loading.remove();
    addMessage(data.response_text, "agent");
    if (data.needs_clarification) {
      if (data.clarification_question === "cold_start") coldStartPending = true;
      else {
        const rawTasks = text.split(",").map((t) => t.replace(/\(.*?\)/g, "").trim()).filter((t) => t.length > 2);
        if (rawTasks.length) showTaskCards(rawTasks);
      }
    }
    currentPlan = data.plan || [];
    currentUnscheduled = data.unscheduled_tasks || [];
    currentSummary = data.daily_summary || "";
    currentDetectedState = data.detected_state || currentDetectedState || "normal";
    currentActionsProposed = data.actions_proposed ?? currentActionsProposed;
    savePlan({ plan: currentPlan, unscheduled: currentUnscheduled, summary: currentSummary, state: currentDetectedState, actions: currentActionsProposed, request: lastRequest });
    renderPlan(data);
  } catch (err) {
    loading.remove();
    if (err.status === 401) await refreshAuth();
    addMessage(err.message || "Connection error. Please try again.", "agent");
  } finally {
    $("sendBtn").disabled = !authState.authenticated;
  }
}

async function submitTaskCards() {
  if (pendingTasks.some((t) => !t.priority || !t.difficulty)) {
    return addMessage("Please select both priority and difficulty for every task before continuing.", "agent");
  }
  const text = pendingTasks.map((task) => {
    const parts = [task.priority, task.difficulty];
    if (task.deadline) parts.push(`due ${task.deadline}`);
    if (task.work_style === "progressive" && task.spread_days) parts.push(`spread:${task.spread_days}`);
    return `${task.title} (${parts.join(", ")})`;
  }).join(", ");
  $("taskCardMsg")?.remove();
  addMessage(`Configured ${pendingTasks.length} task${pendingTasks.length !== 1 ? "s" : ""}. Building your plan now.`, "user");
  await processInput(text, false);
}

async function confirmActions() {
  if (!authState.calendarConnected) return void (window.location.href = "/auth/calendar/start");
  if (!lastRequest) {
    lastRequest = buildSavedRequest();
  }
  const loading = addLoadingMessage();
  try {
    const data = await api("/confirm", { method: "POST", body: JSON.stringify({ ...lastRequest, confirm_actions: true }) });
    loading.remove();
    addMessage((data.tool_results || []).map((r) => r.message).join("\n") || "Calendar action finished successfully.", "agent");
    currentPlan = data.plan || currentPlan;
    currentUnscheduled = data.unscheduled_tasks || currentUnscheduled;
    currentSummary = data.daily_summary || currentSummary;
    currentDetectedState = data.detected_state || currentDetectedState || "normal";
    currentActionsProposed = data.actions_proposed ?? [];
    lastRequest = buildSavedRequest();
    savePlan({ plan: currentPlan, unscheduled: currentUnscheduled, summary: currentSummary, state: currentDetectedState, actions: currentActionsProposed, request: lastRequest });
    renderPlan(data);
  } catch (err) {
    loading.remove();
    if (err.status === 409) window.location.href = "/auth/calendar/start";
    else addMessage(err.message || "Failed to confirm calendar actions.", "agent");
  }
}

async function scheduleBacklog(taskId) {
  const select = document.querySelector(`select[data-task-id="${CSS.escape(taskId)}"]`);
  const data = await api("/api/schedule-task", {
    method: "POST",
    body: JSON.stringify({ task_id: taskId, target_day: parseInt(select.value, 10), current_plan: currentPlan, current_unscheduled: currentUnscheduled }),
  });
  currentPlan = data.plan || [];
  currentUnscheduled = data.unscheduled_tasks || [];
  lastRequest = buildSavedRequest();
  savePlan({ plan: currentPlan, unscheduled: currentUnscheduled, summary: currentSummary, state: currentDetectedState, actions: currentActionsProposed, request: lastRequest });
  renderPlan({ plan: currentPlan, unscheduled_tasks: currentUnscheduled, daily_summary: currentSummary, detected_state: currentDetectedState, adjustments_applied: [], actions_proposed: currentActionsProposed });
}

function handleActionClick(target) {
  const action = target.closest("[data-action]");
  if (!action) return;
  const i = Number(action.dataset.card || "0");
  if (action.dataset.action === "segment") {
    pendingTasks[i][action.dataset.field] = action.dataset.value;
    action.closest(".segmented-control").querySelectorAll("button").forEach((btn) => btn.classList.toggle("selected", btn === action));
    updatePreview(i);
  }
  if (action.dataset.action === "work-style") {
    pendingTasks[i].work_style = action.dataset.value;
    action.closest(".segmented-control").querySelectorAll("button").forEach((btn) => btn.classList.toggle("selected", btn === action));
    $(`spread-${i}`).style.display = action.dataset.value === "progressive" ? "block" : "none";
    updatePreview(i);
  }
  if (action.dataset.action === "step") {
    const input = $(`stepper-${i}`);
    const next = Math.max(2, Math.min(30, (parseInt(input.value, 10) || 2) + Number(action.dataset.delta || "0")));
    input.value = String(next);
    pendingTasks[i].spread_days = next;
    updatePreview(i);
  }
  if (action.dataset.action === "submit-cards") submitTaskCards();
  if (action.dataset.action === "confirm-calendar") confirmActions();
  if (action.dataset.action === "schedule") scheduleBacklog(action.dataset.taskId);
}

function handleActionChange(target) {
  if (target.dataset.action === "deadline") {
    const i = Number(target.dataset.card || "0");
    pendingTasks[i].deadline = target.value || null;
    if (target.value) {
      const diffDays = Math.ceil((new Date(target.value) - new Date()) / (1000 * 60 * 60 * 24));
      if (diffDays > 2) {
        const recommended = Math.max(2, Math.min(30, Math.floor(diffDays * 0.7)));
        $(`stepper-${i}`).value = String(recommended);
        pendingTasks[i].spread_days = recommended;
      }
    }
    updatePreview(i);
  }
  if (target.dataset.action === "step-input") {
    const i = Number(target.dataset.card || "0");
    const next = Math.max(2, Math.min(30, parseInt(target.value, 10) || 2));
    target.value = String(next);
    pendingTasks[i].spread_days = next;
    updatePreview(i);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  $("sendBtn").addEventListener("click", sendMessage);
  $("logoutBtn").addEventListener("click", async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch {}
    authState = { authenticated: false, csrfToken: null, calendarConnected: false, user: null };
    currentPlan = [];
    currentUnscheduled = [];
    currentSummary = "";
    currentActionsProposed = [];
    currentDetectedState = "normal";
    lastRequest = null;
    renderAuth();
    resetPlanView();
    initGoogle();
    addMessage("You have been signed out.", "agent");
  });
  $("userInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) sendMessage(); });
  document.addEventListener("click", (e) => handleActionClick(e.target));
  document.addEventListener("change", (e) => handleActionChange(e.target));
  resetPlanView();
  setPlannerEnabled(false);
  try {
    await refreshAuth();
    initGoogle();
    const params = new URLSearchParams(window.location.search);
    const calendar = params.get("calendar");
    const calendarReason = params.get("reason");
    if (calendar === "connected") addMessage("Google Calendar is connected. You can now confirm plans into your own calendar.", "agent");
    if (calendar === "error") {
      const suffix = calendarReason ? ` Details: ${calendarReason}` : "";
      addMessage(`Google Calendar could not be connected. Please try again.${suffix}`, "agent");
    }
    if (calendar) history.replaceState({}, document.title, window.location.pathname);
  } catch (err) {
    addMessage("Authentication could not be initialized. Check the server configuration and try again.", "agent");
  }
});
