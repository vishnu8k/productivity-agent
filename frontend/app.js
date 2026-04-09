const API_BASE = "";
let lastRequest = null;
let coldStartPending = false;
let currentPlan = [];
let currentUnscheduled = [];
let currentSummary = "";
let pendingTasks = [];

function getUserId() {
  return document.getElementById("userId").value.trim() || "demo_user";
}

function getEnergy() {
  return document.getElementById("energyInput").value || null;
}

function updateUserDisplay() {
  const uid = getUserId();
  const uidDisplay = document.getElementById("userIdDisplay");
  const uidAvatar = document.getElementById("userAvatar");
  if (uidDisplay) uidDisplay.textContent = uid;
  if (uidAvatar) uidAvatar.textContent = uid.charAt(0).toUpperCase();
  loadUserPlan(uid);
}

function addMessage(text, role) {
  const container = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const avatarText = role === "agent" ? "AI" : getUserId().charAt(0).toUpperCase();
  div.innerHTML = `<div class="msg-avatar">${avatarText}</div><div class="msg-bubble">${text}</div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function addLoadingMessage() {
  const container = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = "msg agent";
  div.innerHTML = `<div class="msg-avatar">AI</div><div class="msg-bubble loading">Thinking...</div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function setStateBadge(state) {
  const badge = document.getElementById("stateBadge");
  const display = document.getElementById("stateDisplay");
  const emojis = {
    normal: "😊 Normal", constrained: "⏰ Constrained",
    fatigued: "😴 Fatigued", overwhelmed: "😤 Overwhelmed", energetic: "⚡ Energetic"
  };
  badge.textContent = emojis[state] || state;
  badge.className = `state-badge ${state}`;
  display.style.display = "flex";
}

function getCapacityClass(pct) {
  if (pct <= 50) return "low";
  if (pct <= 80) return "medium";
  return "high";
}

function savePlanToStorage(userId, plan, unscheduled, summary, state) {
  try {
    localStorage.setItem(`plan_${userId}`, JSON.stringify({ plan, unscheduled, summary, state, ts: Date.now() }));
  } catch(e) {}
}

function loadPlanFromStorage(userId) {
  try {
    const raw = localStorage.getItem(`plan_${userId}`);
    if (!raw) return null;
    const data = JSON.parse(raw);
    const age = (Date.now() - data.ts) / 1000 / 60 / 60;
    if (age > 24) { localStorage.removeItem(`plan_${userId}`); return null; }
    return data;
  } catch(e) { return null; }
}

async function loadUserPlan(userId) {
  const saved = loadPlanFromStorage(userId);
  if (saved && saved.plan && saved.plan.length > 0) {
    currentPlan = saved.plan;
    currentUnscheduled = saved.unscheduled || [];
    currentSummary = saved.summary || "";
    renderPlanFromData({
      plan: saved.plan,
      unscheduled_tasks: saved.unscheduled || [],
      daily_summary: saved.summary || "",
      detected_state: saved.state || "normal",
      adjustments_applied: [],
      actions_proposed: []
    });
    addMessage("Welcome back! I've restored your previous plan. You can add more tasks or ask me to modify anything.", "agent");
  }
}

const EFFORT_MAP = { easy: 1, medium: 2, hard: 3, tough: 4 };

function showTaskCards(tasksNeedingInfo) {
  const container = document.getElementById("chatMessages");
  const div = document.createElement("div");
  div.className = "msg agent";
  div.id = "taskCardMsg";
  
  const cards = tasksNeedingInfo.map((task, i) => `
    <div class="task-card" id="tcard-${i}">
      <div class="task-card-title">${task.title || task}</div>
      <div class="task-card-fields">
        
        <div class="field-group">
          <label class="field-label">Priority</label>
          <div class="segmented-control">
            <button class="segment-btn" onclick="selectSegment(${i},'priority','high')" data-field="priority" data-val="high">High</button>
            <button class="segment-btn" onclick="selectSegment(${i},'priority','medium')" data-field="priority" data-val="medium">Medium</button>
            <button class="segment-btn" onclick="selectSegment(${i},'priority','low')" data-field="priority" data-val="low">Low</button>
          </div>
        </div>
        
        <div class="field-group">
          <label class="field-label">Difficulty</label>
          <div class="segmented-control">
            <button class="segment-btn" onclick="selectSegment(${i},'difficulty','easy')" data-field="difficulty" data-val="easy">Easy</button>
            <button class="segment-btn" onclick="selectSegment(${i},'difficulty','medium')" data-field="difficulty" data-val="medium">Medium</button>
            <button class="segment-btn" onclick="selectSegment(${i},'difficulty','hard')" data-field="difficulty" data-val="hard">Hard</button>
            <button class="segment-btn" onclick="selectSegment(${i},'difficulty','tough')" data-field="difficulty" data-val="tough">Tough</button>
          </div>
        </div>

        <div class="field-group">
          <label class="field-label">Deadline (optional)</label>
          <input type="date" class="field-date" id="deadline-${i}" onchange="handleDeadlineChange(${i})" />
        </div>

        <div class="field-group" style="margin-top: 4px;">
          <label class="field-label">Work Style</label>
          <div class="segmented-control">
            <button class="segment-btn selected" onclick="selectWorkStyle(${i},'single')" data-field="work_style" data-val="single">📍 One sitting</button>
            <button class="segment-btn" onclick="selectWorkStyle(${i},'progressive')" data-field="work_style" data-val="progressive">📈 Progressive work</button>
          </div>
          
          <div class="spread-days-wrap" id="spread-${i}" style="display:none; background:transparent; border:none; padding:0;">
            <div class="stepper-wrap">
              <div style="display:flex; flex-direction:column; gap:4px;">
                <label class="field-label">Spread across days</label>
                <div class="stepper-control">
                  <button class="stepper-btn" onclick="updateStepper(${i}, -1)">-</button>
                  <input type="number" class="stepper-input" id="stepper-val-${i}" value="2" min="2" max="30" onchange="manualStepperInput(${i})" />
                  <button class="stepper-btn" onclick="updateStepper(${i}, 1)">+</button>
                </div>
              </div>
              <div class="effort-preview" id="effort-preview-${i}">~0 pts/day</div>
            </div>
          </div>
        </div>

      </div>
    </div>
  `).join("");

  div.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="msg-bubble" style="max-width:95%;width:100%">
      <div style="margin-bottom:10px;font-size:13px;">I noticed a few missing details. Let's quickly configure these:</div>
      <div class="task-cards-container">${cards}</div>
      <button class="submit-cards-btn" onclick="submitTaskCards()">Build My Plan →</button>
    </div>
  `;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;

  pendingTasks = tasksNeedingInfo.map(t => ({
    title: t.title || t, priority: null, difficulty: null, deadline: null,
    work_style: 'single', spread_days: 2
  }));
}

function selectSegment(cardIndex, field, value) {
  pendingTasks[cardIndex][field] = value;
  const card = document.getElementById(`tcard-${cardIndex}`);
  card.querySelectorAll(`[data-field="${field}"]`).forEach(btn => {
    btn.classList.remove("selected");
    if (btn.dataset.val === value) btn.classList.add("selected");
  });
  updateEffortPreview(cardIndex);
}

function selectWorkStyle(cardIndex, value) {
  pendingTasks[cardIndex].work_style = value;
  const card = document.getElementById(`tcard-${cardIndex}`);
  card.querySelectorAll(`[data-field="work_style"]`).forEach(btn => {
    btn.classList.remove("selected");
    if (btn.dataset.val === value) btn.classList.add("selected");
  });
  
  const spreadWrap = document.getElementById(`spread-${cardIndex}`);
  if (value === 'progressive') {
    spreadWrap.style.display = 'block';
    updateEffortPreview(cardIndex);
  } else {
    spreadWrap.style.display = 'none';
  }
}

function handleDeadlineChange(idx) {
  const dateInput = document.getElementById(`deadline-${idx}`).value;
  if (!dateInput) return;
  
  const due = new Date(dateInput);
  const today = new Date();
  const diffTime = Math.abs(due - today);
  const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
  
  if (diffDays > 2) {
    const recommended = Math.max(2, Math.floor(diffDays * 0.7));
    document.getElementById(`stepper-val-${idx}`).value = Math.min(recommended, 30);
    pendingTasks[idx].spread_days = Math.min(recommended, 30);
    
    if (pendingTasks[idx].work_style === 'single') {
        selectWorkStyle(idx, 'progressive');
    }
  }
  updateEffortPreview(idx);
}

function updateStepper(idx, delta) {
  const input = document.getElementById(`stepper-val-${idx}`);
  let val = parseInt(input.value) || 2;
  val += delta;
  if (val < 2) val = 2;
  if (val > 30) val = 30;
  input.value = val;
  pendingTasks[idx].spread_days = val;
  updateEffortPreview(idx);
}

function manualStepperInput(idx) {
  const input = document.getElementById(`stepper-val-${idx}`);
  let val = parseInt(input.value) || 2;
  if (val < 2) val = 2;
  if (val > 30) val = 30;
  input.value = val;
  pendingTasks[idx].spread_days = val;
  updateEffortPreview(idx);
}

function updateEffortPreview(idx) {
  const task = pendingTasks[idx];
  const diff = task.difficulty;
  const preview = document.getElementById(`effort-preview-${idx}`);
  if (!diff) {
    preview.textContent = "Select difficulty";
    return;
  }
  const totalPts = EFFORT_MAP[diff];
  const days = task.spread_days || 2;
  const perDay = Math.max(1, Math.ceil(totalPts / days));
  preview.textContent = `~${perDay} pt/day`;
}

async function submitTaskCards() {
  const incomplete = pendingTasks.filter(t => !t.priority || !t.difficulty);
  if (incomplete.length > 0) {
    addMessage("Please select a Priority and Difficulty for all tasks before continuing.", "agent");
    return;
  }
  
  currentPlan = [];
  currentUnscheduled = [];
  currentSummary = "";

  const taskStrings = pendingTasks.map((t, i) => {
    const deadlineEl = document.getElementById(`deadline-${i}`);
    const deadlineStr = deadlineEl ? deadlineEl.value : "";
    const spreadStr = t.work_style === 'progressive' && t.spread_days ? `, spread:${t.spread_days}` : '';
    return `${t.title} (${t.priority}, ${t.difficulty}${deadlineStr ? ', due ' + deadlineStr : ''}${spreadStr})`;
  }).join(", ");

  const cardMsg = document.getElementById("taskCardMsg");
  if (cardMsg) cardMsg.remove();
  
  const confirmDiv = document.createElement("div");
  confirmDiv.className = "msg user";
  confirmDiv.innerHTML = `<div class="msg-avatar">${getUserId().charAt(0).toUpperCase()}</div>
    <div class="msg-bubble">✅ ${pendingTasks.length} task${pendingTasks.length > 1 ? 's' : ''} configured — building plan...</div>`;
  document.getElementById("chatMessages").appendChild(confirmDiv);
  document.getElementById("chatMessages").scrollTop = 99999;

  const sendBtn = document.getElementById("sendBtn");
  sendBtn.disabled = true;
  const loadingDiv = addLoadingMessage();
  const userId = getUserId();
  const energy = getEnergy();
  
  const payload = {
    user_id: userId, input_text: taskStrings, state_inputs: energy ? { energy } : null,
    confirm_actions: false, current_plan: [], current_unscheduled: [], current_summary: ""
  };
  lastRequest = payload;
  
  try {
    const res = await fetch(`${API_BASE}/plan`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    loadingDiv.remove();
    addMessage(data.response_text, "agent");
    if (data.plan && data.plan.length > 0) {
      currentPlan = data.plan;
      currentUnscheduled = data.unscheduled_tasks || [];
      currentSummary = data.daily_summary || "";
      savePlanToStorage(userId, currentPlan, currentUnscheduled, currentSummary, data.detected_state);
      renderPlanFromData(data);
    }
  } catch(e) {
    loadingDiv.remove();
    addMessage("Something went wrong creating your plan. Please try again.", "agent");
    console.error(e);
  }
  sendBtn.disabled = false;
}

function renderPlanFromData(data) {
  if (data.detected_state) setStateBadge(data.detected_state);

  const totalTasks = (data.plan || []).reduce((s, d) => s + d.tasks.length, 0);
  document.getElementById("planMeta").textContent =
    `${totalTasks} task${totalTasks !== 1 ? "s" : ""} · ${(data.plan||[]).length} day${(data.plan||[]).length !== 1 ? "s" : ""} · ${data.detected_state || ""}`;

  const content = document.getElementById("planContent");
  content.innerHTML = "";

  if (data.daily_summary) {
    const card = document.createElement("div");
    card.className = "section-card summary-card";
    card.innerHTML = `
      <div class="section-header">
        <div class="section-icon">📋</div>
        <span class="section-title">Daily Summary</span>
      </div>
      <div class="summary-text">${data.daily_summary}</div>`;
    content.appendChild(card);
  }

  if (data.adjustments_applied && data.adjustments_applied.length > 0) {
    const card = document.createElement("div");
    card.className = "section-card";
    card.innerHTML = `
      <div class="section-header">
        <div class="section-icon">⚙️</div>
        <span class="section-title">Adjustments Applied</span>
        <span class="section-count" style="background:var(--surface-2);color:var(--text-2);border:1px solid var(--border)">${data.adjustments_applied.length}</span>
      </div>
      <div class="adjustments-list">
        ${data.adjustments_applied.map(a => `<div class="adjustment-item">${a}</div>`).join("")}
      </div>`;
    content.appendChild(card);
  }

  if (data.plan && data.plan.length > 0) {
    const section = document.createElement("div");
    section.className = "section-card";
    section.innerHTML = `
      <div class="section-header">
        <div class="section-icon">📅</div>
        <span class="section-title">Schedule</span>
        <span class="section-count" style="background:var(--surface-2);color:var(--text-2);border:1px solid var(--border)">${data.plan.length} day${data.plan.length > 1 ? "s" : ""}</span>
      </div>
      <div class="days-section">
        ${data.plan.map(day => `
          <div class="day-card">
            <div class="day-header">
              <div class="day-number">D${day.day}</div>
              <div class="day-info">
                <div class="day-label">Day ${day.day}</div>
                <div class="day-date">${day.date || ""}</div>
              </div>
              <div class="day-capacity-info">
                <div class="day-pts">${day.total_effort_points} pts</div>
                <div class="capacity-bar-wrap">
                  <div class="capacity-bar">
                    <div class="capacity-fill ${getCapacityClass(day.capacity_percentage)}" style="width:${Math.min(day.capacity_percentage,100)}%"></div>
                  </div>
                  <span class="capacity-pct">${day.capacity_percentage}%</span>
                </div>
              </div>
            </div>
            <div class="task-list">
              ${day.tasks.map(task => `
                <div class="task-item">
                  <div class="priority-indicator ${task.effective_priority}"></div>
                  <div class="task-info">
                    <div class="task-title">${task.title}</div>
                    <div class="task-meta">${task.effective_priority} priority${task.domain_added ? " · auto-added" : ""}</div>
                  </div>
                  <div class="task-badges">
                    <span class="badge badge-diff">${task.difficulty}</span>
                    <span class="badge badge-pts">${task.effort_points}pt</span>
                    ${task.deadline ? `<span class="badge badge-dl">${task.deadline}</span>` : ""}
                    ${task.domain_added ? `<span class="badge badge-domain">auto</span>` : ""}
                    ${task.session_number ? `<span class="badge badge-session">${task.session_number}/${task.total_sessions}</span>` : ""}
                  </div>
                </div>`).join("")}
            </div>
          </div>`).join("")}
      </div>`;
    content.appendChild(section);
  }

  // CLEARED AND FIXED UNSCHEDULED LIST RENDERER
  if (data.unscheduled_tasks && data.unscheduled_tasks.length > 0) {
    const card = document.createElement("div");
    card.className = "section-card unscheduled-card";
    card.innerHTML = `
      <div class="section-header">
        <div class="section-icon">📌</div>
        <span class="section-title">Unscheduled Tasks</span>
        <span class="section-count">${data.unscheduled_tasks.length}</span>
      </div>
      <div class="unscheduled-list">
        ${data.unscheduled_tasks.map(t => `
          <div class="unscheduled-item" style="display: flex; justify-content: space-between; align-items: center; padding: 10px 16px; border-bottom: 1px solid var(--border);">
            <div style="display: flex; flex-direction: column; flex: 1; min-width: 0;">
              <span class="unscheduled-title" style="font-weight: 500; color: var(--text-1);">${t.title}</span>
              <span class="unscheduled-reason" style="font-size: 10px; color: var(--amber);">${t.unscheduled_reason || "unscheduled"}</span>
            </div>
            <div style="display: flex; gap: 8px; align-items: center; margin-left: 12px;">
              <select id="day-${t.task_id}" style="background: var(--surface); color: var(--text-1); border: 1px solid var(--border-strong); border-radius: 4px; padding: 2px 6px; font-size: 11px; outline: none;">
                ${Array.from({length: 30}, (_, i) => `<option value="${i + 1}">Day ${i + 1}</option>`).join("")}
              </select>
              <button class="unscheduled-btn" style="background: var(--amber-dim); color: var(--amber); border: 1px solid var(--amber-border); padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; font-weight: 600;" onclick="scheduleBacklogTask('${t.task_id}')">Add</button>
            </div>
          </div>`).join("")}
      </div>
      <div style="padding:10px 16px;border-top:1px solid var(--border)">
        <div style="font-size:11px;color:var(--text-3)">💡 Tip: Select a day and click Add to move it to your schedule.</div>
      </div>`;
    content.appendChild(card);
  }

  if (data.actions_proposed && data.actions_proposed.length > 0) {
    const card = document.createElement("div");
    card.className = "section-card actions-card";
    card.innerHTML = `
      <div class="section-header">
        <div class="section-icon">⚡</div>
        <span class="section-title">Proposed Actions</span>
        <span class="section-count">${data.actions_proposed.length}</span>
      </div>
      <div class="actions-list">
        ${data.actions_proposed.map(a => `
          <div class="action-item">
            <div class="action-dot"></div>
            <span>${a.tool} — ${a.action_type}</span>
          </div>`).join("")}
      </div>
      <div class="actions-footer">
        <button class="confirm-btn-full" onclick="confirmActions()">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M2 7l4 4 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          Confirm & Add to Google Calendar
        </button>
      </div>`;
    content.appendChild(card);
    const topBtn = document.getElementById("planActionsTop");
    if (topBtn) topBtn.style.display = "flex";
  } else {
    const topBtn = document.getElementById("planActionsTop");
    if (topBtn) topBtn.style.display = "none";
  }
}

async function processInput(text) {
  const sendBtn = document.getElementById("sendBtn");
  sendBtn.disabled = true;
  const loadingDiv = addLoadingMessage();
  const userId = getUserId();
  const energy = getEnergy();

  if (coldStartPending) {
    try {
      const res = await fetch(`${API_BASE}/cold-start/${userId}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ response: text })
      });
      const data = await res.json();
      loadingDiv.remove();
      addMessage(data.message, "agent");
      coldStartPending = false;
    } catch(e) {
      loadingDiv.remove();
      addMessage("Something went wrong. Please try again.", "agent");
    }
    sendBtn.disabled = false;
    return;
  }

  const payload = {
    user_id: userId,
    input_text: text,
    state_inputs: energy ? { energy } : null,
    confirm_actions: false,
    current_plan: currentPlan,
    current_unscheduled: currentUnscheduled,
    current_summary: currentSummary
  };

  lastRequest = payload;

  try {
    const res = await fetch(`${API_BASE}/plan`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    loadingDiv.remove();

    if (data.needs_clarification) {
      if (data.clarification_question === "cold_start") {
        coldStartPending = true;
        addMessage(data.response_text, "agent");
      } else {
        addMessage(data.response_text, "agent");
        const rawTasks = text.split(",").map(t => {
          return t.replace(/\(.*?\)/g, '').trim();
        }).filter(t => t.length > 2);
        if (rawTasks.length > 0) {
          showTaskCards(rawTasks);
        }
        if (data.plan && data.plan.length > 0) {
          currentPlan = data.plan;
          currentUnscheduled = data.unscheduled_tasks || [];
          renderPlanFromData(data);
        }
      }
    } else {
      addMessage(data.response_text, "agent");
      currentPlan = data.plan || [];
      currentUnscheduled = data.unscheduled_tasks || [];
      currentSummary = data.daily_summary || "";
      savePlanToStorage(userId, currentPlan, currentUnscheduled, currentSummary, data.detected_state);
      renderPlanFromData(data);
    }
  } catch(e) {
    loadingDiv.remove();
    addMessage("Connection error. Please try again.", "agent");
  }
  sendBtn.disabled = false;
}

async function sendMessage() {
  const input = document.getElementById("userInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMessage(text, "user");
  if (!document.getElementById("taskCardMsg")) {
    await processInput(text);
  }
}

async function confirmActions() {
  if (!lastRequest) return;
  const payload = { ...lastRequest, confirm_actions: true };
  const loadingDiv = addLoadingMessage();
  try {
    const res = await fetch(`${API_BASE}/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    loadingDiv.remove();
    addMessage("✅ Done! Your tasks have been added to Google Calendar.", "agent");
    document.getElementById("planActionsTop").style.display = "none";
    renderPlanFromData(data);
  } catch(e) {
    loadingDiv.remove();
    addMessage("Failed to confirm actions.", "agent");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("userInput").addEventListener("keydown", e => {
    if (e.key === "Enter" && e.ctrlKey) sendMessage();
  });
  document.getElementById("userId").addEventListener("change", updateUserDisplay);
  const uid = getUserId();
  const uidDisplay = document.getElementById("userIdDisplay");
  const uidAvatar = document.getElementById("userAvatar");
  if (uidDisplay) uidDisplay.textContent = uid;
  if (uidAvatar) uidAvatar.textContent = uid.charAt(0).toUpperCase();
  loadUserPlan(uid);
});

async function handleClarification(data, originalText) {
  if (data.clarification_question === "cold_start") {
    coldStartPending = true;
    addMessage(data.response_text, "agent");
    return;
  }

  const taskNames = originalText.split(",").map(t => t.trim()).filter(Boolean);
  if (taskNames.length > 0) {
    addMessage(data.response_text, "agent");
    showTaskCards(taskNames);
  } else {
    addMessage(data.response_text, "agent");
  }
}

// --- DIRECT SCHEDULING LOGIC FIXED ---
async function scheduleBacklogTask(taskId) {
  const select = document.getElementById(`day-${taskId}`);
  const targetDay = parseInt(select.value, 10);
  
  const payload = {
    task_id: taskId,
    target_day: targetDay,
    current_plan: currentPlan,
    current_unscheduled: currentUnscheduled
  };

  const btn = select.nextElementSibling;
  btn.textContent = "...";
  btn.disabled = true;

  try {
    const res = await fetch(`${API_BASE}/api/schedule-task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    
    const data = await res.json();
    if (data.status === "success") {
      currentPlan = data.plan;
      currentUnscheduled = data.unscheduled_tasks;
      
      const userId = getUserId();
      const stateBadge = document.getElementById("stateBadge");
      const currentStateMatch = stateBadge ? stateBadge.className.match(/state-badge (\w+)/) : null;
      const currentState = currentStateMatch ? currentStateMatch[1] : "normal";
      
      savePlanToStorage(userId, currentPlan, currentUnscheduled, currentSummary, currentState);
      
      renderPlanFromData({
        plan: currentPlan,
        unscheduled_tasks: currentUnscheduled,
        daily_summary: currentSummary,
        detected_state: currentState
      });
    } else {
      btn.textContent = "Error";
      setTimeout(() => { btn.textContent = "Add"; btn.disabled = false; }, 2000);
    }
  } catch (e) {
    console.error(e);
    btn.textContent = "Error";
    setTimeout(() => { btn.textContent = "Add"; btn.disabled = false; }, 2000);
  }
}