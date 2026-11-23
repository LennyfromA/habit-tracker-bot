/**
 * Telegram Habit Tracker & Health Widget Frontend App
 * Pure Vanilla ES6 JS with zero heavy dependencies
 */

(function () {
  "use strict";

  // Global Application State
  const state = {
    bootstrap: null,
    activeTab: "tasks",
    isCheckinMode: false,
    wid: "",
    timerInterval: null,
    floatingTimerInterval: null,
    searchTimeout: null,
    expandedHabitId: null,
    editingHabit: null,
    showNewHabitForm: false,
    selectedMealType: "snack",
    selectedFoodItem: null,
    timerReflectionData: null,
    filterHabits: "today", // 'today' or 'all'
    statsPeriod: 7, // 7, 15, 30, 90
    statsMetric: "focus", // 'focus', 'habits', 'calories', 'water', 'weight'
    statsCache: {},
    isLoadingStats: false,
    // Task Tracker state
    tasksFilter: "today", // 'today', 'inbox', 'all', or list_id
    tasksData: [],
    expandedTasks: new Set(),
    quickTaskDraftTitle: "",
    quickTaskDateType: "today", // 'today', 'tomorrow', 'custom', 'none'
    quickTaskCustomDate: "", // 'YYYY-MM-DD'
    quickTaskCustomTime: "", // 'HH:MM'
    isDatePickerOpen: false,
    quickTaskEstimate: null, // 15, 25, 45, 60
    quickTaskListId: null,
    autoOpenFocusOverlay: false,
  };

  const tg = window.Telegram?.WebApp;

  // Initialize WebApp
  function initTelegram() {
    if (tg) {
      tg.ready();
      tg.expand();
      if (tg.enableClosingConfirmation) {
        tg.enableClosingConfirmation();
      }
      tg.onEvent("themeChanged", () => {
        // Theme variables update automatically via CSS variables
      });
    }

    const params = new URLSearchParams(window.location.search);
    state.wid = params.get("wid") || "";
    // Automatically enable preview mode if opened in a regular browser outside Telegram
    state.isDev = params.get("dev") === "1" || params.get("preview") === "1" || !tg?.initData;
    const viewParam = params.get("view") || "tasks";

    if (viewParam === "checkin") {
      state.isCheckinMode = true;
      state.activeTab = "habits";
    } else if (["tasks", "habits", "health", "stats", "settings"].includes(viewParam)) {
      state.activeTab = viewParam;
    } else if (viewParam === "timer") {
      state.activeTab = "tasks";
      state.autoOpenFocusOverlay = true;
    } else {
      state.activeTab = "tasks";
    }
  }

  // Haptic feedback helper
  function haptic(type = "light") {
    try {
      if (tg?.HapticFeedback) {
        if (type === "success" || type === "error" || type === "warning") {
          tg.HapticFeedback.notificationOccurred(type);
        } else {
          tg.HapticFeedback.impactOccurred(type);
        }
      }
    } catch (_) {}
  }

  // Safe HTML string escaping
  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Toast notifications
  function showToast(msg, isError = false) {
    const toast = document.getElementById("toast");
    if (!toast) return;
    toast.textContent = msg;
    toast.className = isError ? "toast error" : "toast";
    setTimeout(() => {
      toast.className = "toast hidden";
    }, 2800);
  }

  // Visual Sync Pulse Indicator
  function triggerSyncAnimation() {
    const indicator = document.getElementById("sync-indicator");
    if (!indicator) return;
    indicator.classList.add("syncing");
    setTimeout(() => {
      indicator.classList.remove("syncing");
    }, 1200);
  }

  // Unified API Fetch wrapper with in-flight deduplication / debouncing
  const activePostRequests = new Set();

  async function apiFetch(url, options = {}) {
    let finalUrl = url;
    if (state.isDev && !finalUrl.includes("dev=1")) {
      finalUrl += (finalUrl.includes("?") ? "&" : "?") + "dev=1";
    }

    const method = (options.method || "GET").toUpperCase();
    const reqKey = `${method}:${url}:${options.body || ""}`;

    // Prevent duplicate concurrent mutation requests (anti-spam / anti-double-click)
    if (method !== "GET") {
      if (activePostRequests.has(reqKey)) {
        return Promise.reject(new Error("Подождите, запрос уже выполняется..."));
      }
      activePostRequests.add(reqKey);
    }

    const headers = Object.assign(
      {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": tg?.initData || "",
        "X-Widget-Id": state.wid || "",
        ...(state.isDev ? { "X-Dev-Preview": "1" } : {}),
      },
      options.headers || {}
    );

    const config = Object.assign({}, options, { headers });

    try {
      const response = await fetch(finalUrl, config);
      const data = await response.json();

      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Ошибка запроса к серверу");
      }

      if (data.synced) {
        triggerSyncAnimation();
      }

      return data;
    } catch (err) {
      if (!err.message?.includes("запрос уже выполняется")) {
        showToast(err.message || "Ошибка соединения", true);
        haptic("error");
      }
      throw err;
    } finally {
      if (method !== "GET") {
        setTimeout(() => activePostRequests.delete(reqKey), 350);
      }
    }
  }

  // Load Initial Bootstrap Data
  async function loadBootstrap() {
    try {
      const res = await apiFetch("/api/bootstrap");
      state.bootstrap = res.data;
      updateHeader();
      updateFloatingFocusBar();
      renderCurrentView();
    } catch (err) {
      const appView = document.getElementById("app-view");
      if (appView) {
        appView.innerHTML = `
          <div class="card" style="text-align:center; padding: 24px;">
            <p style="color: var(--danger); font-weight:600;">Не удалось загрузить данные</p>
            <p style="font-size:0.8rem; color:var(--hint); margin-top:8px;">${escapeHtml(err.message)}</p>
            <button type="button" class="btn-timer-ctrl btn-primary" style="margin-top:16px;" id="btn-retry">Повторить</button>
          </div>
        `;
        document.getElementById("btn-retry")?.addEventListener("click", () => {
          loadBootstrap();
        });
      }
    }
  }

  // Header update
  function updateHeader() {
    const titleEl = document.getElementById("header-title");
    const dateEl = document.getElementById("header-date");
    if (!titleEl || !dateEl) return;

    const titles = {
      tasks: "Задачи",
      habits: state.isCheckinMode ? "Вечерний чек-ин" : "Мои привычки",
      health: "Здоровье и КБЖУ",
      stats: `Итоги (${state.statsPeriod || 7} дней)`,
      settings: "Настройки",
    };

    titleEl.textContent = titles[state.activeTab] || "Задачи";

    if (state.bootstrap) {
      dateEl.textContent = `${state.bootstrap.local_date}`;
    }
  }

  // Tab switching
  function setupTabs() {
    const tabs = document.querySelectorAll(".nav-btn");
    tabs.forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.tab === state.activeTab);
      tab.addEventListener("click", () => {
        const target = tab.dataset.tab;
        if (target === state.activeTab && !state.isCheckinMode) return;
        haptic("light");
        state.activeTab = target;
        state.isCheckinMode = false;

        tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === target));
        updateHeader();
        renderCurrentView();
      });
    });
  }

  // Dispatch view rendering
  function renderCurrentView() {
    clearInterval(state.timerInterval);
    const appView = document.getElementById("app-view");
    if (!appView || !state.bootstrap) return;

    if (state.activeTab === "tasks") {
      renderTasksView(appView);
    } else if (state.activeTab === "habits") {
      renderHabitsView(appView);
    } else if (state.activeTab === "health") {
      renderHealthView(appView);
    } else if (state.activeTab === "stats") {
      renderStatsView(appView);
    } else if (state.activeTab === "settings") {
      renderSettingsView(appView);
    }
  }

  // =========================================================================
  // 0. FLOATING FOCUS BAR & FOCUS OVERLAY
  // =========================================================================
  function updateFloatingFocusBar() {
    const bar = document.getElementById("floating-focus-bar");
    if (!bar) return;

    const timer = state.bootstrap?.active_timer;
    if (!timer) {
      bar.classList.add("hidden");
      clearInterval(state.floatingTimerInterval);
      state.floatingTimerInterval = null;
      document.getElementById("focus-overlay")?.classList.add("hidden");
      return;
    }

    bar.classList.remove("hidden");
    const titleEl = document.getElementById("focus-bar-title");
    const subEl = document.getElementById("focus-bar-sub");
    const dotEl = document.getElementById("focus-bar-dot");
    const timeEl = document.getElementById("focus-bar-time");
    const pauseBtn = document.getElementById("focus-bar-pause-btn");

    const targetName = timer.target_name || timer.habit_name || "Фокус-сессия";
    const targetType = timer.target_type === "task" ? "📌 Задача" : "🎯 Привычка";
    const statusText = timer.is_paused ? "Пауза" : "В процессе";

    if (titleEl) titleEl.textContent = targetName;
    if (subEl) subEl.textContent = `${targetType} · ${statusText}`;
    if (dotEl) dotEl.classList.toggle("paused", Boolean(timer.is_paused));
    if (pauseBtn) pauseBtn.textContent = timer.is_paused ? "▶️" : "⏸";

    clearInterval(state.floatingTimerInterval);
    function tick() {
      const nowSec = Math.floor(Date.now() / 1000);
      const elapsed = timer.is_paused
        ? (timer.elapsed_before_pause || 0)
        : (timer.elapsed_before_pause || 0) + Math.max(0, nowSec - timer.started_at);
      const remaining = Math.max(0, timer.total_target_seconds - elapsed);
      const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
      const ss = String(remaining % 60).padStart(2, "0");
      const timeStr = `${mm}:${ss}`;

      if (timeEl) timeEl.textContent = timeStr;
      const overlayTimer = document.getElementById("focus-overlay-timer");
      if (overlayTimer) overlayTimer.textContent = timeStr;

      if (remaining <= 0 && !timer.is_paused) {
        clearInterval(state.floatingTimerInterval);
        loadBootstrap();
      }
    }
    tick();
    if (!timer.is_paused) {
      state.floatingTimerInterval = setInterval(tick, 1000);
    }

    if (state.autoOpenFocusOverlay) {
      state.autoOpenFocusOverlay = false;
      openFocusOverlay();
    }
  }

  function openFocusOverlay() {
    const overlay = document.getElementById("focus-overlay");
    if (!overlay) return;
    const timer = state.bootstrap?.active_timer;
    if (!timer) return;

    haptic("light");
    overlay.classList.remove("hidden");
    const titleEl = document.getElementById("focus-overlay-title");
    const badgeEl = document.getElementById("focus-overlay-badge");
    const metaEl = document.getElementById("focus-overlay-meta");
    const pauseCtrl = document.getElementById("focus-ctrl-pause");

    if (titleEl) titleEl.textContent = timer.target_name || timer.habit_name || "Фокус";
    if (badgeEl) badgeEl.textContent = timer.target_type === "task" ? "Задача" : "Привычка";
    if (metaEl) metaEl.textContent = `Цель: ${Math.round(timer.total_target_seconds / 60)} мин`;
    if (pauseCtrl) pauseCtrl.textContent = timer.is_paused ? "▶️ Продолжить" : "⏸ Пауза";
  }

  function closeFocusOverlay() {
    document.getElementById("focus-overlay")?.classList.add("hidden");
  }

  function setupFloatingFocusBarEvents() {
    document.getElementById("focus-bar-trigger")?.addEventListener("click", () => {
      openFocusOverlay();
    });

    document.getElementById("focus-bar-pause-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      haptic("medium");
      const timer = state.bootstrap?.active_timer;
      if (!timer) return;
      try {
        const endpoint = timer.is_paused ? "/api/timer/resume" : "/api/timer/pause";
        await apiFetch(endpoint, { method: "POST" });
        await loadBootstrap();
      } catch (_) {}
    });

    document.getElementById("focus-bar-stop-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      haptic("warning");
      await handleStopTimer();
    });

    document.getElementById("focus-overlay-dismiss-btn")?.addEventListener("click", closeFocusOverlay);
    document.getElementById("focus-overlay-close")?.addEventListener("click", closeFocusOverlay);

    document.getElementById("focus-ctrl-pause")?.addEventListener("click", async () => {
      haptic("medium");
      const timer = state.bootstrap?.active_timer;
      if (!timer) return;
      try {
        const endpoint = timer.is_paused ? "/api/timer/resume" : "/api/timer/pause";
        await apiFetch(endpoint, { method: "POST" });
        await loadBootstrap();
      } catch (_) {}
    });

    document.getElementById("focus-ctrl-add10")?.addEventListener("click", async () => {
      haptic("light");
      try {
        await apiFetch("/api/timer/add-10", { method: "POST" });
        showToast("+10 минут добавлено ⏱");
        await loadBootstrap();
      } catch (_) {}
    });

    document.getElementById("focus-ctrl-stop")?.addEventListener("click", async () => {
      haptic("warning");
      await handleStopTimer();
    });
  }

  async function handleStopTimer() {
    closeFocusOverlay();
    try {
      const res = await apiFetch("/api/timer/stop", { method: "POST" });
      if (res.data) {
        if (res.data.target_type === "task") {
          showToast(`Фокус завершен! +${res.data.actual_minutes} мин. ⏱`);
          if (confirm(`Сессия по задаче «${res.data.task_name}» завершена (${res.data.actual_minutes} мин).\nОтметить задачу выполненной?`)) {
            await apiFetch(`/api/tasks/${res.data.task_id}/toggle`, { method: "POST" });
          }
        } else {
          state.timerReflectionData = res.data;
          state.activeTab = "habits";
          document.querySelectorAll(".nav-btn").forEach((t) => t.classList.toggle("active", t.dataset.tab === "habits"));
          updateHeader();
          renderCurrentView();
        }
      }
      await loadBootstrap();
    } catch (_) {}
  }

  // =========================================================================
  // 1. TASKS VIEW (Todoist-Style with Subtasks & Lists)
  // =========================================================================
  async function loadTasksData() {
    try {
      let url = "/api/tasks";
      if (typeof state.tasksFilter === "number") {
        url += `?list_id=${state.tasksFilter}`;
      } else {
        url += `?view=${state.tasksFilter}`;
      }
      const res = await apiFetch(url);
      if (res.data) {
        state.tasksData = res.data.tasks || [];
        if (res.data.counts) {
          state.tasksCounts = res.data.counts;
        }
      }
    } catch (_) {
      state.tasksData = [];
    }
  }

  async function renderTasksView(container) {
    updateHeader();

    const lists = state.bootstrap?.task_lists || [];
    const counts = state.tasksCounts || state.bootstrap?.tasks_counts || {};
    const todayTasks = state.bootstrap?.tasks_today || [];

    const isToday = state.tasksFilter === "today";
    const isAll = state.tasksFilter === "all";
    const isInbox = state.tasksFilter === "inbox";

    let chipsHtml = `
      <div class="task-chips-bar">
        <button type="button" class="task-chip ${isToday ? "active" : ""}" data-filter="today">
          📅 Сегодня <span class="task-chip-count">(${counts.today_total !== undefined ? counts.today_total : todayTasks.length})</span>
        </button>
        <button type="button" class="task-chip ${isAll ? "active" : ""}" data-filter="all">
          📑 Все <span class="task-chip-count">(${counts.all_active || 0})</span>
        </button>
        <button type="button" class="task-chip ${isInbox ? "active" : ""}" data-filter="inbox">
          📥 Входящие
        </button>
    `;

    lists.forEach((l) => {
      if (l.is_inbox) return;
      const isActive = state.tasksFilter === l.id;
      chipsHtml += `
        <button type="button" class="task-chip ${isActive ? "active" : ""}" data-filter="${l.id}">
          ${escapeHtml(l.icon || "📁")} ${escapeHtml(l.name)}
        </button>
      `;
    });

    chipsHtml += `
        <button type="button" class="task-chip" id="btn-create-list" style="border-style:dashed;">
          ➕ Список
        </button>
      </div>
    `;

    let overdueBannerHtml = "";
    const overdueCount = counts.overdue || 0;
    if (isToday && overdueCount > 0) {
      overdueBannerHtml = `
        <div class="task-overdue-banner">
          <div class="task-overdue-text">
            ⚠️ <b>Просрочено задач: ${overdueCount}</b>
          </div>
          <button type="button" class="btn-defer-inline" id="btn-defer-overdue-tasks">Перенести на сегодня</button>
        </div>
      `;
    }

    function getDateLabel() {
      if (state.quickTaskDateType === "none") return "📅 Без даты";
      if (state.quickTaskDateType === "today") {
        return state.quickTaskCustomTime ? `📅 Сегодня, ${state.quickTaskCustomTime}` : "📅 Сегодня";
      }
      if (state.quickTaskDateType === "tomorrow") {
        return state.quickTaskCustomTime ? `📅 Завтра, ${state.quickTaskCustomTime}` : "📅 Завтра";
      }
      if (state.quickTaskDateType === "custom" && state.quickTaskCustomDate) {
        const parts = state.quickTaskCustomDate.split("-");
        const formatted = `${parts[2]}.${parts[1]}`;
        return state.quickTaskCustomTime ? `📅 ${formatted}, ${state.quickTaskCustomTime}` : `📅 ${formatted}`;
      }
      return "📅 Дата";
    }

    const estLabels = { 15: "⏱ 15м", 25: "⏱ 25м", 45: "⏱ 45м", 60: "⏱ 1ч", null: "⏱ Таймер" };

    const quickAddHtml = `
      <div class="task-quick-add-box">
        <div class="task-input-row">
          <input type="text" class="task-quick-input" id="task-quick-title-input" placeholder="+ Новая задача (например: Позвонить врачу завтра 14:00 #здоровье 25m)" value="${escapeHtml(state.quickTaskDraftTitle || "")}" autocomplete="off">
          <button type="button" class="btn-add-task" id="btn-submit-quick-task" title="Добавить">+</button>
        </div>
        <div class="task-add-chips-row">
          <button type="button" class="task-add-chip-btn ${state.quickTaskDateType !== "none" ? "selected" : ""}" id="chip-task-date">
            ${escapeHtml(getDateLabel())} ▾
          </button>

          <!-- Native Date & Time Picker Popover -->
          <div class="task-date-picker-popover ${state.isDatePickerOpen ? "" : "hidden"}" id="task-date-popover">
            <div class="date-popover-quick-grid">
              <button type="button" class="btn-date-quick ${state.quickTaskDateType === "today" ? "active" : ""}" data-date-quick="today">Сегодня</button>
              <button type="button" class="btn-date-quick ${state.quickTaskDateType === "tomorrow" ? "active" : ""}" data-date-quick="tomorrow">Завтра</button>
              <button type="button" class="btn-date-quick ${state.quickTaskDateType === "none" ? "active" : ""}" data-date-quick="none">Без даты</button>
            </div>
            <div class="date-popover-native-row">
              <span class="date-popover-native-label">Выбрать дату и время:</span>
              <div class="date-popover-inputs">
                <input type="date" class="input-native-date" id="input-task-custom-date" value="${state.quickTaskCustomDate || (state.quickTaskDateType === "today" ? (state.bootstrap?.local_date || "") : "")}">
                <input type="time" class="input-native-time" id="input-task-custom-time" value="${state.quickTaskCustomTime || ""}" placeholder="Время">
              </div>
            </div>
            <div class="date-popover-actions">
              <button type="button" class="btn-date-popover-ok" id="btn-date-popover-done">Готово</button>
            </div>
          </div>

          <button type="button" class="task-add-chip-btn ${state.quickTaskEstimate ? "selected" : ""}" id="chip-task-est">
            ${estLabels[state.quickTaskEstimate] || "⏱ Таймер"}
          </button>
          <select class="form-input" id="quick-task-list-select" style="padding: 3px 6px; font-size: 0.75rem; border-radius: var(--radius-full); width: auto; max-width: 140px; height: 26px;">
            <option value="">📥 Входящие</option>
            ${lists.filter(l => !l.is_inbox).map(l => `<option value="${l.id}" ${state.quickTaskListId === l.id ? "selected" : ""}>${escapeHtml(l.icon || "📁")} ${escapeHtml(l.name)}</option>`).join("")}
          </select>
        </div>
      </div>
    `;

    let tasksToRender = state.tasksData;
    if (isToday && (!state.tasksData || state.tasksData.length === 0)) {
      tasksToRender = todayTasks;
    }

    let tasksListHtml = "";
    if (!tasksToRender || tasksToRender.length === 0) {
      tasksListHtml = `
        <div style="text-align:center; padding: 40px 16px; color: var(--hint);">
          <span style="font-size: 2.2rem; display:block; margin-bottom:8px;">🎉</span>
          <p style="font-weight:600; color:var(--text);">Все задачи выполнены!</p>
          <p style="font-size:0.8rem; margin-top:4px;">Добавьте новую задачу в поле выше или через бота /t</p>
        </div>
      `;
    } else {
      tasksListHtml = '<div class="task-items-list">';
      tasksToRender.forEach((t) => {
        const isComp = Boolean(t.is_completed);
        const subtasks = t.subtasks || [];
        const isExpanded = state.expandedTasks.has(t.id);
        const subCompletedCount = subtasks.filter((s) => s.is_completed).length;

        let metaHtml = "";
        if (t.due_date) {
          const isOverdue = Boolean(t.is_overdue) && !isComp;
          const isDueToday = t.due_date === state.bootstrap.local_date;
          const cls = isOverdue ? "overdue" : (isDueToday ? "today" : "");
          const dLabel = isDueToday ? "Сегодня" : t.due_date;
          metaHtml += `<span class="task-meta-pill ${cls}">📅 ${escapeHtml(dLabel)}</span>`;
        }
        if (t.due_time) {
          metaHtml += `<span class="task-meta-pill">⏰ ${escapeHtml(t.due_time)}</span>`;
        }
        if (t.estimated_minutes) {
          metaHtml += `<span class="task-meta-pill">⏱ ${t.estimated_minutes}м</span>`;
        }
        if (t.spent_minutes > 0) {
          metaHtml += `<span class="task-meta-pill" style="color:var(--success);">🔥 ${t.spent_minutes}м</span>`;
        }
        if (t.list_name && !t.is_inbox) {
          metaHtml += `<span class="task-meta-pill">${escapeHtml(t.list_icon || "📁")} ${escapeHtml(t.list_name)}</span>`;
        }
        if (t.recurrence && t.recurrence !== "none") {
          metaHtml += `<span class="task-meta-pill">🔄</span>`;
        }

        let subtasksBadgeHtml = "";
        if (subtasks.length > 0) {
          subtasksBadgeHtml = `
            <button type="button" class="task-subtasks-toggle" data-task-id="${t.id}">
              ${isExpanded ? "▲" : "▼"} ${subCompletedCount}/${subtasks.length} подзадач
            </button>
          `;
        } else {
          subtasksBadgeHtml = `
            <button type="button" class="task-subtasks-toggle" data-task-id="${t.id}" style="color:var(--hint); font-weight:normal;">
              + подзадача
            </button>
          `;
        }

        let subtasksListHtml = "";
        if (isExpanded) {
          subtasksListHtml = `
            <div class="task-subtasks-wrap" id="subtasks-wrap-${t.id}">
              ${subtasks.map((s) => `
                <div class="subtask-item-row">
                  <span class="subtask-checkbox ${s.is_completed ? "checked" : ""}" data-subtask-id="${s.id}" data-task-id="${t.id}"></span>
                  <span class="subtask-title-text ${s.is_completed ? "completed" : ""}">${escapeHtml(s.title)}</span>
                  <button type="button" class="btn-subtask-del" data-subtask-del="${s.id}" data-task-id="${t.id}" title="Удалить">✕</button>
                </div>
              `).join("")}
              <div class="subtask-add-row">
                <input type="text" class="subtask-add-input" placeholder="+ Добавить подзадачу" data-subtask-add-input="${t.id}">
              </div>
            </div>
          `;
        }

        tasksListHtml += `
          <div class="task-card ${isComp ? "completed" : ""}" id="task-card-${t.id}">
            <div class="task-main-row">
              <span class="task-checkbox-custom ${isComp ? "checked" : ""}" data-task-toggle="${t.id}"></span>
              <div class="task-details-col">
                <div class="task-title-text">${escapeHtml(t.title)}</div>
                <div class="task-meta-row">
                  ${metaHtml}
                  ${subtasksBadgeHtml}
                </div>
              </div>
              <div class="task-actions-col">
                ${(!isComp && t.estimated_minutes) ? `<button type="button" class="btn-task-focus" data-task-focus="${t.id}" title="Фокус-таймер">⏱ Фокус</button>` : ""}
                <button type="button" class="btn-task-icon" data-task-del="${t.id}" title="Удалить задачу">🗑</button>
              </div>
            </div>
            ${subtasksListHtml}
          </div>
        `;
      });
      tasksListHtml += '</div>';
    }

    container.innerHTML = `
      ${chipsHtml}
      ${overdueBannerHtml}
      ${quickAddHtml}
      ${tasksListHtml}
    `;

    setupTasksEvents(container);
  }

  function setupTasksEvents(container) {
    // Filter chips
    container.querySelectorAll(".task-chip[data-filter]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        haptic("light");
        const f = btn.dataset.filter;
        state.tasksFilter = (f === "today" || f === "all" || f === "inbox") ? f : parseInt(f, 10);
        await loadTasksData();
        renderTasksView(container);
      });
    });

    // Create custom list
    document.getElementById("btn-create-list")?.addEventListener("click", async () => {
      haptic("light");
      const name = prompt("Введите название нового списка задач:");
      if (!name || !name.trim()) return;
      try {
        await apiFetch("/api/task-lists", {
          method: "POST",
          body: JSON.stringify({ name: name.trim(), icon: "📁" }),
        });
        showToast("Список создан! 📁");
        await loadBootstrap();
      } catch (_) {}
    });

    // Defer overdue tasks
    document.getElementById("btn-defer-overdue-tasks")?.addEventListener("click", async () => {
      haptic("medium");
      try {
        const res = await apiFetch("/api/tasks/defer-today", { method: "POST" });
        showToast(`Перенесено задач: ${res.deferred_count || 0} 📅`);
        await loadBootstrap();
        await loadTasksData();
        renderTasksView(container);
      } catch (_) {}
    });

    // Quick add chips & Date Popover
    const chipDateBtn = document.getElementById("chip-task-date");
    const datePopover = document.getElementById("task-date-popover");
    const titleInput = document.getElementById("task-quick-title-input");

    // Track input text dynamically so re-rendering keeps user input
    titleInput?.addEventListener("input", (e) => {
      state.quickTaskDraftTitle = e.target.value;
    });

    // Helper: validate whether chosen date + time is in the past
    function isChosenDateTimePast(dateStr, timeStr) {
      if (!dateStr || !timeStr) return false;
      const todayStr = state.bootstrap?.local_date;
      if (dateStr < todayStr) return true;
      if (dateStr === todayStr) {
        // Compare with local current time (HH:MM)
        const now = new Date();
        const curHM = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
        return timeStr < curHM;
      }
      return false;
    }

    chipDateBtn?.addEventListener("click", (e) => {
      e.stopPropagation();
      haptic("light");
      if (titleInput) state.quickTaskDraftTitle = titleInput.value;
      state.isDatePickerOpen = !state.isDatePickerOpen;
      if (datePopover) {
        datePopover.classList.toggle("hidden", !state.isDatePickerOpen);
      }
    });

    // Quick date selection buttons inside popover
    container.querySelectorAll(".btn-date-quick[data-date-quick]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        haptic("light");
        if (titleInput) state.quickTaskDraftTitle = titleInput.value;
        const type = btn.dataset.dateQuick;
        state.quickTaskDateType = type;
        if (type === "today") {
          state.quickTaskCustomDate = state.bootstrap?.local_date || "";
        } else if (type === "tomorrow") {
          const d = new Date(state.bootstrap?.local_date || new Date().toISOString().split("T")[0]);
          d.setDate(d.getDate() + 1);
          state.quickTaskCustomDate = d.toISOString().split("T")[0];
        } else if (type === "none") {
          state.quickTaskCustomDate = "";
          state.quickTaskCustomTime = "";
        }
        state.isDatePickerOpen = false;
        renderTasksView(container);
      });
    });

    // Native Date & Time input change handlers
    document.getElementById("input-task-custom-date")?.addEventListener("change", (e) => {
      const val = e.target.value;
      if (val) {
        state.quickTaskCustomDate = val;
        const todayStr = state.bootstrap?.local_date;
        if (val === todayStr) {
          state.quickTaskDateType = "today";
        } else {
          state.quickTaskDateType = "custom";
        }
      }
    });

    document.getElementById("input-task-custom-time")?.addEventListener("change", (e) => {
      const timeVal = e.target.value || "";
      const targetDate = state.quickTaskDateType === "today"
        ? state.bootstrap?.local_date
        : (state.quickTaskDateType === "tomorrow"
            ? (() => { const d = new Date(state.bootstrap?.local_date); d.setDate(d.getDate() + 1); return d.toISOString().split("T")[0]; })()
            : state.quickTaskCustomDate);

      if (timeVal && isChosenDateTimePast(targetDate, timeVal)) {
        showToast("⚠️ Выбранное время уже прошло!", true);
      }
      state.quickTaskCustomTime = timeVal;
    });

    document.getElementById("btn-date-popover-done")?.addEventListener("click", (e) => {
      e.stopPropagation();
      haptic("light");
      if (titleInput) state.quickTaskDraftTitle = titleInput.value;
      const dateVal = document.getElementById("input-task-custom-date")?.value;
      const timeVal = document.getElementById("input-task-custom-time")?.value;
      if (dateVal) {
        state.quickTaskCustomDate = dateVal;
        const todayStr = state.bootstrap?.local_date;
        if (dateVal === todayStr) {
          state.quickTaskDateType = "today";
        } else {
          state.quickTaskDateType = "custom";
        }
      }

      if (timeVal) {
        const targetDate = state.quickTaskDateType === "today"
          ? state.bootstrap?.local_date
          : (state.quickTaskDateType === "tomorrow"
              ? (() => { const d = new Date(state.bootstrap?.local_date); d.setDate(d.getDate() + 1); return d.toISOString().split("T")[0]; })()
              : state.quickTaskCustomDate);

        if (isChosenDateTimePast(targetDate, timeVal)) {
          showToast("⚠️ Время уже прошло! Укажите будущее время", true);
          return;
        }
      }

      state.quickTaskCustomTime = timeVal || "";
      state.isDatePickerOpen = false;
      renderTasksView(container);
    });

    // Close popover when clicking outside
    const handleDocumentClick = (e) => {
      if (state.isDatePickerOpen && !e.target.closest("#task-date-popover") && !e.target.closest("#chip-task-date")) {
        state.isDatePickerOpen = false;
        if (datePopover) datePopover.classList.add("hidden");
      }
    };
    document.removeEventListener("click", container._datePickerCloser);
    container._datePickerCloser = handleDocumentClick;
    document.addEventListener("click", handleDocumentClick);

    document.getElementById("chip-task-est")?.addEventListener("click", () => {
      haptic("light");
      if (titleInput) state.quickTaskDraftTitle = titleInput.value;
      const cycle = [null, 15, 25, 45, 60];
      const nextIdx = (cycle.indexOf(state.quickTaskEstimate) + 1) % cycle.length;
      state.quickTaskEstimate = cycle[nextIdx];
      renderTasksView(container);
    });

    document.getElementById("quick-task-list-select")?.addEventListener("change", (e) => {
      const val = e.target.value;
      state.quickTaskListId = val ? parseInt(val, 10) : null;
    });

    // Quick task submit
    async function submitQuickTask() {
      const input = document.getElementById("task-quick-title-input");
      const rawTitle = (input?.value || state.quickTaskDraftTitle || "").trim();
      if (!rawTitle) return;

      let dueDate = null;
      if (state.quickTaskDateType === "today") {
        dueDate = state.bootstrap?.local_date;
      } else if (state.quickTaskDateType === "tomorrow") {
        const d = new Date(state.bootstrap?.local_date || new Date().toISOString().split("T")[0]);
        d.setDate(d.getDate() + 1);
        dueDate = d.toISOString().split("T")[0];
      } else if (state.quickTaskDateType === "custom" && state.quickTaskCustomDate) {
        dueDate = state.quickTaskCustomDate;
      }

      const dueTime = state.quickTaskCustomTime || null;

      if (dueDate && dueTime && isChosenDateTimePast(dueDate, dueTime)) {
        haptic("error");
        showToast("⚠️ Нельзя установить время в прошлом!", true);
        return;
      }

      haptic("medium");
      const listSelect = document.getElementById("quick-task-list-select");
      const listId = listSelect?.value ? parseInt(listSelect.value, 10) : state.quickTaskListId;

      try {
        await apiFetch("/api/tasks", {
          method: "POST",
          body: JSON.stringify({
            title: rawTitle,
            list_id: listId,
            due_date: dueDate,
            due_time: dueTime,
            estimated_minutes: state.quickTaskEstimate,
          }),
        });
        showToast("Задача создана! ✅");
        state.quickTaskDraftTitle = "";
        if (input) input.value = "";
        state.isDatePickerOpen = false;
        await loadBootstrap();
        await loadTasksData();
        renderTasksView(container);
      } catch (_) {}
    }

    document.getElementById("btn-submit-quick-task")?.addEventListener("click", submitQuickTask);
    document.getElementById("task-quick-title-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        submitQuickTask();
      }
    });

    // Toggle task complete
    container.querySelectorAll("[data-task-toggle]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        haptic("success");
        const taskId = btn.dataset.taskToggle;
        try {
          const res = await apiFetch(`/api/tasks/${taskId}/toggle`, { method: "POST" });
          if (res.data?.recurrence && res.data.recurrence !== "none") {
            showToast("🔄 Задача перенесена на следующий цикл!");
          }
          await loadBootstrap();
          await loadTasksData();
          renderTasksView(container);
        } catch (_) {}
      });
    });

    // Start focus timer for task
    container.querySelectorAll("[data-task-focus]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        haptic("medium");
        const taskId = parseInt(btn.dataset.taskFocus, 10);
        try {
          await apiFetch("/api/timer/start", {
            method: "POST",
            body: JSON.stringify({ task_id: taskId }),
          });
          showToast("⏱ Фокус-сессия запущена!");
          await loadBootstrap();
        } catch (_) {}
      });
    });

    // Delete task
    container.querySelectorAll("[data-task-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const taskId = btn.dataset.taskDel;
        if (confirm("Удалить эту задачу?")) {
          haptic("warning");
          try {
            await apiFetch(`/api/tasks/${taskId}`, { method: "DELETE" });
            showToast("Задача удалена 🗑");
            await loadBootstrap();
            await loadTasksData();
            renderTasksView(container);
          } catch (_) {}
        }
      });
    });

    // Expand / collapse subtasks
    container.querySelectorAll(".task-subtasks-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        haptic("light");
        const taskId = parseInt(btn.dataset.taskId, 10);
        if (state.expandedTasks.has(taskId)) {
          state.expandedTasks.delete(taskId);
        } else {
          state.expandedTasks.add(taskId);
        }
        renderTasksView(container);
      });
    });

    // Toggle subtask checkbox
    container.querySelectorAll(".subtask-checkbox[data-subtask-id]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        haptic("light");
        const subId = btn.dataset.subtaskId;
        const isChecked = btn.classList.contains("checked");
        try {
          await apiFetch(`/api/subtasks/${subId}`, {
            method: "PATCH",
            body: JSON.stringify({ is_completed: !isChecked }),
          });
          await loadBootstrap();
          await loadTasksData();
          renderTasksView(container);
        } catch (_) {}
      });
    });

    // Delete subtask
    container.querySelectorAll("[data-subtask-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        haptic("light");
        const subId = btn.dataset.subtaskDel;
        try {
          await apiFetch(`/api/subtasks/${subId}`, { method: "DELETE" });
          await loadBootstrap();
          await loadTasksData();
          renderTasksView(container);
        } catch (_) {}
      });
    });

    // Add subtask on Enter
    container.querySelectorAll("[data-subtask-add-input]").forEach((inp) => {
      inp.addEventListener("keydown", async (e) => {
        if (e.key === "Enter") {
          const val = inp.value.trim();
          if (!val) return;
          const taskId = inp.dataset.subtaskAddInput;
          haptic("light");
          try {
            await apiFetch(`/api/tasks/${taskId}/subtasks`, {
              method: "POST",
              body: JSON.stringify({ title: val }),
            });
            inp.value = "";
            await loadBootstrap();
            await loadTasksData();
            renderTasksView(container);
          } catch (_) {}
        }
      });
    });
  }

  // =========================================================================
  // 2. HABITS VIEW
  // =========================================================================
  function renderHabitsView(container) {
    const b = state.bootstrap;
    const habits = state.filterHabits === "today" ? b.habits_today : b.habits_all;
    const completedCount = b.habits_today.filter((h) => h.is_completed || h.is_frozen_today).length;
    const totalToday = b.habits_today.length;
    const progressPercent = totalToday > 0 ? Math.round((completedCount / totalToday) * 100) : 100;
    const freezesLeft = b.user?.freezes_left || 0;

    let checkinBannerHtml = "";
    if (state.isCheckinMode) {
      const calPercent = b.nutrition.goals.target_calories > 0
        ? Math.round((b.nutrition.summary.total_calories / b.nutrition.goals.target_calories) * 100)
        : 0;
      const waterPercent = b.water.target_ml > 0
        ? Math.round((b.water.current_ml / b.water.target_ml) * 100)
        : 0;

      checkinBannerHtml = `
        <div class="card" style="border-left: 4px solid var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--surface));">
          <div class="card-header">
            <span class="card-title">🌙 Вечерний чек-ин</span>
            <span class="badge badge-success">${completedCount}/${totalToday} закрыто</span>
          </div>
          <p style="font-size:0.8rem; color:var(--hint);">
            Проверьте выполненные привычки за день. 
            Питание: <b>${b.nutrition.summary.total_calories} ккал</b> (${calPercent}%), 
            Вода: <b>${b.water.current_ml} мл</b> (${waterPercent}%).
          </p>
        </div>
      `;
    }

    let habitsListHtml = "";
    if (habits.length === 0) {
      habitsListHtml = `
        <div class="card" style="text-align:center; padding: 28px 14px; color: var(--hint);">
          <span style="font-size: 2rem;">🌴</span>
          <p style="margin-top: 6px; font-weight:500;">
            ${state.filterHabits === "today" ? "На сегодня нет запланированных привычек!" : "У вас пока нет созданных привычек"}
          </p>
          <p style="font-size: 0.78rem; margin-top: 4px;">Нажмите кнопку ниже, чтобы создать первую</p>
        </div>
      `;
    } else {
      habitsListHtml = habits
        .map((h) => {
          const isDone = Boolean(h.is_completed);
          const isFrozen = Boolean(h.is_frozen_today);
          const isExpanded = state.expandedHabitId === h.id;
          const isChecklist = h.habit_type === "checklist";

          let statusBadge = "";
          if (isFrozen) {
            statusBadge = `<span class="badge badge-ice">❄️ Заморожено</span>`;
          } else if (isDone) {
            statusBadge = `<span class="badge badge-success">✓ Выполнено</span>`;
          }

          let metaText = "";
          let actionControl = "";

          if (isChecklist) {
            const cur = h.spent_minutes || 0;
            const tgt = h.target_repeats || 1;
            metaText = `Цель: ${tgt} раз(а) в день • Стрик: 🔥 ${h.streak_days || 0} дн.`;
            actionControl = `
              <button type="button" class="habit-check-btn ${isDone ? "checked" : ""}" data-check-id="${h.id}" title="+1">
                ${isDone ? "✓" : `${cur}/${tgt}`}
              </button>
            `;
          } else {
            const spent = h.spent_minutes || 0;
            const tgt = h.target_minutes || 30;
            const percent = Math.min(100, Math.round((spent / tgt) * 100));
            metaText = `${spent}/${tgt} мин (${percent}%) • Стрик: 🔥 ${h.streak_days || 0} дн.`;
            actionControl = `
              <button type="button" class="habit-play-btn" data-play-id="${h.id}" title="Запустить таймер">
                ▶
              </button>
            `;
          }

          const actionPanelHtml = isExpanded
            ? `
            <div class="habit-actions-panel">
              <button type="button" class="action-chip-btn" data-action="check" data-id="${h.id}">✅ Выполнить</button>
              ${!isFrozen && !isDone ? `<button type="button" class="action-chip-btn" data-action="freeze" data-id="${h.id}">❄️ Заморозить</button>` : ""}
              <button type="button" class="action-chip-btn" data-action="edit" data-id="${h.id}">✏️ Изменить</button>
              <button type="button" class="action-chip-btn danger" data-action="delete" data-id="${h.id}">🗑 Удалить</button>
            </div>
          `
            : "";

          return `
            <div class="habit-item ${isDone ? "is-completed" : ""} ${isFrozen ? "is-frozen" : ""}" data-habit-row="${h.id}">
              <div class="habit-main-row">
                <div class="habit-info" data-toggle-expand="${h.id}">
                  <div class="habit-title-line">
                    <span>${escapeHtml(h.name)}</span>
                    ${statusBadge}
                  </div>
                  <div class="habit-meta-line">
                    <span>${metaText}</span>
                  </div>
                </div>
                ${actionControl}
              </div>
              ${actionPanelHtml}
            </div>
          `;
        })
        .join("");
    }

    // New habit form container
    let formHtml = "";
    if (state.showNewHabitForm || state.editingHabit) {
      const isEditing = Boolean(state.editingHabit);
      const habit = state.editingHabit || {};
      const formTitle = isEditing ? `Редактирование: ${escapeHtml(habit.name || "")}` : "Новая привычка";
      const htype = habit.habit_type || "duration";
      const nameVal = habit.name || "";
      const minVal = habit.target_minutes || 30;
      const repsVal = habit.target_repeats || 1;
      const daysVal = habit.active_days || "1,2,3,4,5,6,7";

      formHtml = `
        <div class="card" id="habit-form-card" style="border: 1px solid var(--accent);">
          <div class="card-header">
            <span class="card-title">${formTitle}</span>
            <button type="button" class="action-chip-btn" id="btn-close-form">✕</button>
          </div>
          <div class="form-group">
            <label class="form-label">Название привычки</label>
            <input type="text" class="form-input" id="form-habit-name" value="${escapeHtml(nameVal)}" placeholder="Например: Чтение книги" maxlength="64">
          </div>
          
          <div class="form-group">
            <label class="form-label">Тип привычки</label>
            <div class="pill-group" style="width: 100%;">
              <button type="button" class="pill-btn ${htype === "duration" ? "active" : ""}" style="flex:1;" id="btn-type-duration">⏱ Таймер</button>
              <button type="button" class="pill-btn ${htype === "checklist" ? "active" : ""}" style="flex:1;" id="btn-type-checklist">✅ Чек-лист</button>
            </div>
          </div>

          <div class="form-group" id="group-target-minutes" style="${htype === "checklist" ? "display:none;" : ""}">
            <label class="form-label">Цель (минут в день)</label>
            <input type="number" class="form-input" id="form-habit-minutes" value="${minVal}" min="1" max="720">
          </div>

          <div class="form-group" id="group-target-repeats" style="${htype === "duration" ? "display:none;" : ""}">
            <label class="form-label">Повторений в день</label>
            <input type="number" class="form-input" id="form-habit-repeats" value="${repsVal}" min="1" max="20">
          </div>

          <div class="form-group">
            <label class="form-label">Активные дни недели</label>
            <div class="pill-group" style="width: 100%; margin-bottom: 6px;">
              <button type="button" class="pill-btn" id="preset-days-all">Все</button>
              <button type="button" class="pill-btn" id="preset-days-work">Будни</button>
              <button type="button" class="pill-btn" id="preset-days-rest">Выходные</button>
            </div>
            <div style="display:flex; gap:4px; justify-content:space-between;" id="days-pills-row">
              ${[1, 2, 3, 4, 5, 6, 7]
                .map((d) => {
                  const labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
                  const isAct = daysVal.split(",").includes(String(d));
                  return `<button type="button" class="pill-btn ${isAct ? "active" : ""}" data-day="${d}" style="padding:5px 6px;">${labels[d - 1]}</button>`;
                })
                .join("")}
            </div>
          </div>

          <div style="display:flex; gap:8px; margin-top:8px;">
            <button type="button" class="btn-timer-ctrl btn-primary" id="btn-save-habit">Сохранить</button>
            <button type="button" class="btn-timer-ctrl btn-secondary" id="btn-cancel-habit">Отмена</button>
          </div>
        </div>
      `;
    }

    container.innerHTML = `
      ${checkinBannerHtml}
      
      <!-- Summary Card -->
      <div class="card">
        <div class="card-header">
          <div class="card-title">
            <span>Прогресс сегодня:</span>
            <span class="badge badge-success">${completedCount} / ${totalToday}</span>
          </div>
          <span class="badge badge-ice" title="Заморозок стрика доступно">❄️ ${freezesLeft}</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill ${progressPercent === 100 ? "completed" : ""}" style="width: ${progressPercent}%;"></div>
        </div>
        <div class="habits-filter-row">
          <div class="pill-group">
            <button type="button" class="pill-btn ${state.filterHabits === "today" ? "active" : ""}" id="filter-today">Сегодня</button>
            <button type="button" class="pill-btn ${state.filterHabits === "all" ? "active" : ""}" id="filter-all">Все привычки</button>
          </div>
          <button type="button" class="btn-add-habit" id="btn-open-new-habit">
            <span>＋ Привычка</span>
          </button>
        </div>
      </div>

      ${formHtml}

      <!-- Habits List -->
      <div style="display:flex; flex-direction:column; gap:8px;">
        ${habitsListHtml}
      </div>
    `;

    // Bind event handlers for Habits
    document.getElementById("filter-today")?.addEventListener("click", () => {
      state.filterHabits = "today";
      renderHabitsView(container);
    });

    document.getElementById("filter-all")?.addEventListener("click", () => {
      state.filterHabits = "all";
      renderHabitsView(container);
    });

    document.getElementById("btn-open-new-habit")?.addEventListener("click", () => {
      haptic("light");
      state.showNewHabitForm = !state.showNewHabitForm;
      state.editingHabit = null;
      renderHabitsView(container);
    });

    // Check habit button
    container.querySelectorAll("[data-check-id]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        haptic("medium");
        const hid = btn.dataset.checkId;
        try {
          await apiFetch(`/api/habits/${hid}/check`, { method: "POST" });
          haptic("success");
          await loadBootstrap();
        } catch (_) {}
      });
    });

    // Play button for duration habit -> launches timer
    container.querySelectorAll("[data-play-id]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        haptic("medium");
        const hid = btn.dataset.playId;
        try {
          await apiFetch("/api/timer/start", {
            method: "POST",
            body: JSON.stringify({ habit_id: parseInt(hid, 10) }),
          });
          haptic("success");
          state.activeTab = "timer";
          document.querySelectorAll(".nav-btn").forEach((t) => t.classList.toggle("active", t.dataset.tab === "timer"));
          updateHeader();
          await loadBootstrap();
        } catch (_) {}
      });
    });

    // Toggle expand action panel
    container.querySelectorAll("[data-toggle-expand]").forEach((el) => {
      el.addEventListener("click", () => {
        haptic("light");
        const hid = parseInt(el.dataset.toggleExpand, 10);
        state.expandedHabitId = state.expandedHabitId === hid ? null : hid;
        renderHabitsView(container);
      });
    });

    // Action panel buttons
    container.querySelectorAll(".action-chip-btn[data-action]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const action = btn.dataset.action;
        const hid = parseInt(btn.dataset.id, 10);

        if (action === "check") {
          haptic("medium");
          try {
            await apiFetch(`/api/habits/${hid}/check`, { method: "POST" });
            haptic("success");
            await loadBootstrap();
          } catch (_) {}
        } else if (action === "freeze") {
          haptic("medium");
          try {
            const res = await apiFetch(`/api/habits/${hid}/freeze`, { method: "POST" });
            showToast(res.message || "Заморожено! ❄️");
            haptic("success");
            await loadBootstrap();
          } catch (_) {}
        } else if (action === "edit") {
          haptic("light");
          const habit = b.habits_all.find((h) => h.id === hid);
          if (habit) {
            state.editingHabit = habit;
            state.showNewHabitForm = false;
            renderHabitsView(container);
          }
        } else if (action === "delete") {
          if (!confirm("Удалить привычку?")) return;
          haptic("warning");
          try {
            await apiFetch(`/api/habits/${hid}`, { method: "DELETE" });
            showToast("Привычка удалена");
            await loadBootstrap();
          } catch (_) {}
        }
      });
    });

    // Form handlers
    if (state.showNewHabitForm || state.editingHabit) {
      setupHabitFormHandlers(container);
    }
  }

  function setupHabitFormHandlers(container) {
    let currentType = state.editingHabit?.habit_type || "duration";
    let selectedDays = new Set(
      (state.editingHabit?.active_days || "1,2,3,4,5,6,7").split(",").map((s) => s.trim())
    );

    document.getElementById("btn-close-form")?.addEventListener("click", () => {
      state.showNewHabitForm = false;
      state.editingHabit = null;
      renderHabitsView(container);
    });

    document.getElementById("btn-cancel-habit")?.addEventListener("click", () => {
      state.showNewHabitForm = false;
      state.editingHabit = null;
      renderHabitsView(container);
    });

    document.getElementById("btn-type-duration")?.addEventListener("click", () => {
      currentType = "duration";
      document.getElementById("btn-type-duration")?.classList.add("active");
      document.getElementById("btn-type-checklist")?.classList.remove("active");
      document.getElementById("group-target-minutes").style.display = "flex";
      document.getElementById("group-target-repeats").style.display = "none";
    });

    document.getElementById("btn-type-checklist")?.addEventListener("click", () => {
      currentType = "checklist";
      document.getElementById("btn-type-checklist")?.classList.add("active");
      document.getElementById("btn-type-duration")?.classList.remove("active");
      document.getElementById("group-target-minutes").style.display = "none";
      document.getElementById("group-target-repeats").style.display = "flex";
    });

    // Day presets
    document.getElementById("preset-days-all")?.addEventListener("click", () => {
      selectedDays = new Set(["1", "2", "3", "4", "5", "6", "7"]);
      updateDaysPills();
    });

    document.getElementById("preset-days-work")?.addEventListener("click", () => {
      selectedDays = new Set(["1", "2", "3", "4", "5"]);
      updateDaysPills();
    });

    document.getElementById("preset-days-rest")?.addEventListener("click", () => {
      selectedDays = new Set(["6", "7"]);
      updateDaysPills();
    });

    function updateDaysPills() {
      container.querySelectorAll("#days-pills-row [data-day]").forEach((p) => {
        p.classList.toggle("active", selectedDays.has(p.dataset.day));
      });
    }

    container.querySelectorAll("#days-pills-row [data-day]").forEach((p) => {
      p.addEventListener("click", () => {
        const d = p.dataset.day;
        if (selectedDays.has(d)) {
          if (selectedDays.size > 1) selectedDays.delete(d);
        } else {
          selectedDays.add(d);
        }
        updateDaysPills();
      });
    });

    // Save habit
    document.getElementById("btn-save-habit")?.addEventListener("click", async () => {
      const name = document.getElementById("form-habit-name")?.value.trim();
      if (!name) {
        showToast("Укажите название привычки", true);
        return;
      }

      const activeDaysStr = Array.from(selectedDays).sort().join(",");
      const minutes = parseInt(document.getElementById("form-habit-minutes")?.value, 10) || 30;
      const repeats = parseInt(document.getElementById("form-habit-repeats")?.value, 10) || 1;

      const payload = {
        name,
        habit_type: currentType,
        target_minutes: currentType === "duration" ? minutes : 0,
        target_repeats: currentType === "checklist" ? repeats : 1,
        active_days: activeDaysStr,
      };

      try {
        if (state.editingHabit) {
          await apiFetch(`/api/habits/${state.editingHabit.id}`, {
            method: "PATCH",
            body: JSON.stringify(payload),
          });
          showToast("Привычка обновлена");
        } else {
          await apiFetch("/api/habits", {
            method: "POST",
            body: JSON.stringify(payload),
          });
          showToast("Привычка создана! 🎉");
        }
        state.showNewHabitForm = false;
        state.editingHabit = null;
        await loadBootstrap();
      } catch (_) {}
    });
  }

  // =========================================================================
  // 2. FOCUS TIMER VIEW
  // =========================================================================
  function renderTimerView(container) {
    const timer = state.bootstrap?.active_timer;

    if (state.timerReflectionData) {
      renderReflectionCard(container);
      return;
    }

    if (!timer) {
      // No active timer -> Show duration habits list to launch
      const durationHabits = (state.bootstrap?.habits_today || []).filter(
        (h) => h.habit_type !== "checklist"
      );

      let habitsHtml = "";
      if (durationHabits.length === 0) {
        habitsHtml = `
          <div style="text-align:center; padding: 24px; color: var(--hint);">
            <p>Нет привычек с таймером на сегодня</p>
            <p style="font-size:0.75rem; margin-top:4px;">Создайте привычку с таймером во вкладке «Привычки»</p>
          </div>
        `;
      } else {
        habitsHtml = durationHabits
          .map(
            (h) => `
          <div class="habit-item">
            <div class="habit-main-row">
              <div class="habit-info">
                <span class="habit-title-line">${escapeHtml(h.name)}</span>
                <span class="habit-meta-line">Цель: ${h.target_minutes} мин. • Сделано: ${h.spent_minutes || 0} мин.</span>
              </div>
              <button type="button" class="btn-timer-ctrl btn-primary" style="flex:0 0 auto; padding:6px 14px;" data-start-timer-id="${h.id}">
                ▶ Старт
              </button>
            </div>
          </div>
        `
          )
          .join("");
      }

      container.innerHTML = `
        <div class="card" style="text-align:center; padding:24px 14px;">
          <span style="font-size: 2.2rem;">⏱</span>
          <h2 style="font-size: 1.15rem; margin-top: 6px;">Фокус-сессия не запущена</h2>
          <p style="font-size: 0.82rem; color: var(--hint); margin-top: 4px;">
            Выберите привычку ниже, чтобы запустить таймер глубокой концентрации:
          </p>
        </div>
        <div style="display:flex; flex-direction:column; gap:8px;">
          ${habitsHtml}
        </div>
      `;

      container.querySelectorAll("[data-start-timer-id]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          haptic("medium");
          const hid = btn.dataset.startTimerId;
          try {
            await apiFetch("/api/timer/start", {
              method: "POST",
              body: JSON.stringify({ habit_id: parseInt(hid, 10) }),
            });
            haptic("success");
            await loadBootstrap();
          } catch (_) {}
        });
      });
      return;
    }

    // Active or paused timer screen
    const isPaused = Boolean(timer.is_paused);
    const totalTargetSec = timer.total_target_seconds || 1800;
    const radius = 95;
    const circumference = 2 * Math.PI * radius;

    container.innerHTML = `
      <div class="card timer-container">
        <div class="timer-ring-wrapper">
          <svg class="timer-svg" viewBox="0 0 220 220">
            <circle class="timer-svg-bg" cx="110" cy="110" r="${radius}"></circle>
            <circle class="timer-svg-progress" id="timer-ring" cx="110" cy="110" r="${radius}" 
              stroke-dasharray="${circumference}" stroke-dashoffset="0"></circle>
          </svg>
          <div class="timer-display">
            <span class="timer-counter" id="timer-clock">00:00</span>
            <span class="timer-habit-name">${escapeHtml(timer.habit_name || "Фокус")}</span>
            <span class="badge ${isPaused ? "" : "badge-success"}" style="margin-top:2px;" id="timer-status-badge">
              ${isPaused ? "⏸ Пауза" : "▶️ Фокус"}
            </span>
          </div>
        </div>

        <div class="timer-controls-row">
          <button type="button" class="btn-timer-ctrl ${isPaused ? "btn-primary" : "btn-secondary"}" id="btn-toggle-pause">
            ${isPaused ? "▶ Продолжить" : "⏸ Пауза"}
          </button>
          <button type="button" class="btn-timer-ctrl btn-secondary" id="btn-add-10">
            +10 мин
          </button>
          <button type="button" class="btn-timer-ctrl btn-danger" id="btn-stop-timer">
            ⏹ Завершить
          </button>
        </div>
      </div>
    `;

    const clockEl = document.getElementById("timer-clock");
    const ringEl = document.getElementById("timer-ring");

    // Client-side ticking
    function tick() {
      const now = Math.floor(Date.now() / 1000);
      let elapsed = timer.elapsed_before_pause || 0;
      if (!timer.is_paused && timer.started_at) {
        elapsed += Math.max(0, now - timer.started_at);
      }

      const remainingSec = Math.max(0, totalTargetSec - elapsed);
      const m = Math.floor(remainingSec / 60);
      const s = remainingSec % 60;
      if (clockEl) {
        clockEl.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
      }

      if (ringEl) {
        const ratio = Math.min(1.0, elapsed / totalTargetSec);
        const offset = circumference * (1 - ratio);
        ringEl.style.strokeDashoffset = offset;
      }
    }

    tick();
    state.timerInterval = setInterval(tick, 1000);

    // Controls
    document.getElementById("btn-toggle-pause")?.addEventListener("click", async () => {
      haptic("medium");
      const endpoint = isPaused ? "/api/timer/resume" : "/api/timer/pause";
      try {
        await apiFetch(endpoint, { method: "POST" });
        await loadBootstrap();
      } catch (_) {}
    });

    document.getElementById("btn-add-10")?.addEventListener("click", async () => {
      haptic("light");
      try {
        await apiFetch("/api/timer/add-10", { method: "POST" });
        showToast("+10 минут добавлено!");
        await loadBootstrap();
      } catch (_) {}
    });

    document.getElementById("btn-stop-timer")?.addEventListener("click", async () => {
      if (!confirm("Завершить текущую сессию?")) return;
      haptic("medium");
      try {
        const res = await apiFetch("/api/timer/stop", { method: "POST" });
        haptic("success");
        if (res.data?.actual_minutes > 0) {
          state.timerReflectionData = res.data;
        }
        await loadBootstrap();
      } catch (_) {}
    });
  }

  function renderReflectionCard(container) {
    const data = state.timerReflectionData;
    container.innerHTML = `
      <div class="card" style="text-align:center; padding: 24px 14px;">
        <span style="font-size: 2.5rem;">🎉</span>
        <h2 style="font-size: 1.2rem; margin-top: 8px;">Сессия завершена!</h2>
        <p style="font-size: 0.9rem; margin-top: 4px;">
          Засчитано: <b>+${data.actual_minutes} мин</b> для «${escapeHtml(data.habit_name)}»
        </p>
        <p style="font-size: 0.8rem; color: var(--hint); margin-top: 4px;">
          Всего сегодня: ${data.spent_today}/${data.target_minutes} мин. ${data.is_completed ? "🏆 Цель выполнена!" : ""}
        </p>

        <div class="form-group" style="margin-top: 16px; text-align:left;">
          <label class="form-label">Заметка о сессии (рефлексия)</label>
          <input type="text" class="form-input" id="reflection-note-input" placeholder="Что удалось сделать? Какие мысли?">
        </div>

        <div style="display:flex; gap:8px; margin-top:14px;">
          <button type="button" class="btn-timer-ctrl btn-primary" id="btn-save-reflection">Сохранить</button>
          <button type="button" class="btn-timer-ctrl btn-secondary" id="btn-skip-reflection">Пропустить</button>
        </div>
      </div>
    `;

    document.getElementById("btn-save-reflection")?.addEventListener("click", async () => {
      const note = document.getElementById("reflection-note-input")?.value.trim();
      haptic("light");
      if (note) {
        try {
          await apiFetch("/api/timer/reflection", {
            method: "POST",
            body: JSON.stringify({
              habit_id: data.habit_id,
              logged_date: data.logged_date,
              note,
            }),
          });
          showToast("Заметка сохранена! 📝");
        } catch (_) {}
      }
      state.timerReflectionData = null;
      renderTimerView(container);
    });

    document.getElementById("btn-skip-reflection")?.addEventListener("click", () => {
      haptic("light");
      state.timerReflectionData = null;
      renderTimerView(container);
    });
  }

  // =========================================================================
  // 3. HEALTH VIEW (Nutrition, Water, Weight)
  // =========================================================================
  function renderHealthView(container) {
    const b = state.bootstrap;
    const nutr = b.nutrition;
    const goals = nutr.goals;
    const summary = nutr.summary;
    const meals = nutr.meals;
    const frequent = nutr.frequent_meals;
    const water = b.water;
    const weight = b.weight?.stats;

    const calCurrent = summary.total_calories || 0;
    const calTarget = goals.target_calories || 2000;
    const calRemaining = Math.max(0, calTarget - calCurrent);
    const calPercent = Math.min(100, Math.round((calCurrent / calTarget) * 100));

    const showBju = Boolean(goals.track_bju);
    const showWater = Boolean(goals.track_water);

    // BJU Block
    let bjuHtml = "";
    if (showBju) {
      bjuHtml = `
        <div class="nutrition-grid">
          <div class="macro-item">
            <span class="macro-label">БЕЛКИ</span>
            <span class="macro-val">${summary.total_protein}г</span>
            <span style="font-size:0.68rem; color:var(--hint);">${goals.target_protein > 0 ? `из ${goals.target_protein}г` : ""}</span>
          </div>
          <div class="macro-item">
            <span class="macro-label">ЖИРЫ</span>
            <span class="macro-val">${summary.total_fat}г</span>
            <span style="font-size:0.68rem; color:var(--hint);">${goals.target_fat > 0 ? `из ${goals.target_fat}г` : ""}</span>
          </div>
          <div class="macro-item">
            <span class="macro-label">УГЛЕВОДЫ</span>
            <span class="macro-val">${summary.total_carbs}г</span>
            <span style="font-size:0.68rem; color:var(--hint);">${goals.target_carbs > 0 ? `из ${goals.target_carbs}г` : ""}</span>
          </div>
        </div>
      `;
    }

    // Water Block
    let waterHtml = "";
    if (showWater) {
      const wCur = water.current_ml || 0;
      const wTgt = water.target_ml || 2000;
      const wPercent = Math.min(100, Math.round((wCur / wTgt) * 100));

      waterHtml = `
        <div class="card">
          <div class="card-header">
            <span class="card-title">💧 Водный баланс</span>
            <span class="badge" style="background-color:color-mix(in srgb, #00b4d8 20%, transparent); color:#00b4d8;">
              ${wCur} / ${wTgt} мл (${wPercent}%)
            </span>
          </div>
          <div class="progress-track">
            <div class="progress-fill" style="width: ${wPercent}%; background-color:#00b4d8;"></div>
          </div>
          <div class="water-controls-row">
            <button type="button" class="btn-water-chip" data-water-add="250">+250 мл</button>
            <button type="button" class="btn-water-chip" data-water-add="500">+500 мл</button>
            <button type="button" class="btn-water-chip" id="btn-water-custom">+ Своё</button>
            <button type="button" class="btn-water-chip" style="flex:0 0 36px; color:var(--hint); border-color:var(--surface-border);" id="btn-water-undo" title="Отменить последнее">↩️</button>
          </div>
        </div>
      `;
    }

    // Weight Block
    let weightHtml = "";
    const todayWeight = weight?.today_weight;
    const diffWeek = weight?.diff_week;
    const diffStr = diffWeek !== null && diffWeek !== undefined
      ? `${diffWeek > 0 ? "+" : ""}${diffWeek.toFixed(1)} кг за 7д`
      : "";

    weightHtml = `
      <div class="card" style="display:flex; flex-direction:row; justify-content:space-between; align-items:center;">
        <div>
          <span style="font-weight:600; font-size:0.92rem;">⚖️ Вес:</span>
          <span style="font-weight:700; margin-left:4px;">${todayWeight ? `${todayWeight} кг` : "Не записан"}</span>
          <span style="font-size:0.75rem; color:var(--hint); margin-left:6px;">${diffStr}</span>
        </div>
        <button type="button" class="btn-add-habit" id="btn-log-weight">Записать</button>
      </div>
    `;

    // Frequent Meals Chips
    let frequentChipsHtml = "";
    if (frequent && frequent.length > 0) {
      frequentChipsHtml = `
        <div class="frequent-meals-row">
          ${frequent
            .map(
              (f) => `
            <div class="chip-meal" data-repeat-id="${f.id}" title="Добавить в 1 клик">
              + ${escapeHtml(f.title)} (${f.calories} ккал)
            </div>
          `
            )
            .join("")}
        </div>
      `;
    }

    // Meals List
    let mealsListHtml = "";
    if (meals.length === 0) {
      mealsListHtml = `
        <div style="text-align:center; padding:18px; color:var(--hint); font-size:0.82rem;">
          Сегодня приёмов пищи ещё не записано
        </div>
      `;
    } else {
      mealsListHtml = meals
        .map((m) => {
          const macroSub = showBju
            ? ` • Б: ${m.protein || 0}г | Ж: ${m.fat || 0}г | У: ${m.carbs || 0}г`
            : "";
          return `
            <div class="meal-list-item">
              <div class="meal-title-group">
                <span class="meal-title">${escapeHtml(m.title)}</span>
                <span class="meal-sub">${m.time_hm || ""} • ${m.calories} ккал${macroSub}</span>
              </div>
              <button type="button" class="btn-del-meal" data-del-meal="${m.id}" title="Удалить">✕</button>
            </div>
          `;
        })
        .join("");
    }

    container.innerHTML = `
      <!-- Daily Calories & Macros Card -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">🍎 Калории сегодня</span>
          <span class="badge ${calRemaining === 0 ? "badge-success" : ""}">${calCurrent} / ${calTarget} ккал</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill ${calCurrent >= calTarget ? "completed" : ""}" style="width: ${calPercent}%;"></div>
        </div>
        <div style="display:flex; justify-content:space-between; font-size:0.75rem; color:var(--hint);">
          <span>Съедено: ${calCurrent} ккал</span>
          <span>Осталось: ${calRemaining} ккал</span>
        </div>
        ${bjuHtml}
      </div>

      ${waterHtml}
      ${weightHtml}

      <!-- Smart Food Input Card -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">➕ Добавить еду</span>
          <div class="pill-group">
            <button type="button" class="pill-btn ${state.selectedMealType === "breakfast" ? "active" : ""}" data-mtype="breakfast">Завтрак</button>
            <button type="button" class="pill-btn ${state.selectedMealType === "lunch" ? "active" : ""}" data-mtype="lunch">Обед</button>
            <button type="button" class="pill-btn ${state.selectedMealType === "dinner" ? "active" : ""}" data-mtype="dinner">Ужин</button>
            <button type="button" class="pill-btn ${state.selectedMealType === "snack" ? "active" : ""}" data-mtype="snack">Перекус</button>
          </div>
        </div>

        ${frequentChipsHtml}

        <div class="form-group" style="position:relative;">
          <input type="text" class="form-input" id="food-search-input" placeholder="🔍 Начните вводить блюдо или быстрый ввод (Банан 105)">
          <div class="search-dropdown" id="search-dropdown" style="display:none;"></div>
        </div>

        <!-- Portion selection container when food selected -->
        <div id="portion-selection-box" style="display:none; flex-direction:column; gap:8px;"></div>
      </div>

      <!-- Meals Log Card -->
      <div class="card">
        <span class="card-title">📋 Съедено за сегодня (${meals.length})</span>
        <div style="display:flex; flex-direction:column;">
          ${mealsListHtml}
        </div>
      </div>
    `;

    // Bind meal type pills
    container.querySelectorAll("[data-mtype]").forEach((btn) => {
      btn.addEventListener("click", () => {
        haptic("light");
        state.selectedMealType = btn.dataset.mtype;
        container.querySelectorAll("[data-mtype]").forEach((b) => b.classList.toggle("active", b.dataset.mtype === state.selectedMealType));
      });
    });

    // Repeat frequent meal
    container.querySelectorAll("[data-repeat-id]").forEach((chip) => {
      chip.addEventListener("click", async () => {
        haptic("medium");
        const mealId = parseInt(chip.dataset.repeatId, 10);
        try {
          await apiFetch("/api/meals", {
            method: "POST",
            body: JSON.stringify({
              mode: "repeat",
              meal_id: mealId,
              meal_type: state.selectedMealType,
            }),
          });
          showToast("Блюдо добавлено! 🍏");
          await loadBootstrap();
        } catch (_) {}
      });
    });

    // Delete meal
    container.querySelectorAll("[data-del-meal]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Удалить этот приём пищи?")) return;
        haptic("warning");
        const mealId = parseInt(btn.dataset.delMeal, 10);
        try {
          await apiFetch(`/api/meals/${mealId}`, { method: "DELETE" });
          showToast("Запись удалена");
          await loadBootstrap();
        } catch (_) {}
      });
    });

    // Water add chips
    container.querySelectorAll("[data-water-add]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        haptic("medium");
        const amount = parseInt(btn.dataset.waterAdd, 10);
        try {
          await apiFetch("/api/water", {
            method: "POST",
            body: JSON.stringify({ amount_ml: amount }),
          });
          showToast(`+${amount} мл воды 💧`);
          await loadBootstrap();
        } catch (_) {}
      });
    });

    // Custom water input
    document.getElementById("btn-water-custom")?.addEventListener("click", async () => {
      const val = prompt("Введите объем воды в мл:", "300");
      if (!val) return;
      const amount = parseInt(val, 10);
      if (isNaN(amount) || amount <= 0 || amount > 5000) {
        showToast("Введите число от 1 до 5000 мл", true);
        return;
      }
      try {
        await apiFetch("/api/water", {
          method: "POST",
          body: JSON.stringify({ amount_ml: amount }),
        });
        showToast(`+${amount} мл воды 💧`);
        await loadBootstrap();
      } catch (_) {}
    });

    // Undo water
    document.getElementById("btn-water-undo")?.addEventListener("click", async () => {
      haptic("light");
      try {
        await apiFetch("/api/water/undo", { method: "POST" });
        showToast("Последняя запись воды отменена");
        await loadBootstrap();
      } catch (_) {}
    });

    // Weight log prompt
    document.getElementById("btn-log-weight")?.addEventListener("click", async () => {
      const val = prompt("Введите ваш текущий вес (кг):", todayWeight || "70.0");
      if (!val) return;
      const weightNum = parseFloat(val.replace(",", "."));
      if (isNaN(weightNum) || weightNum < 20 || weightNum > 300) {
        showToast("Укажите вес от 20 до 300 кг", true);
        return;
      }
      try {
        await apiFetch("/api/weight", {
          method: "POST",
          body: JSON.stringify({ weight: weightNum }),
        });
        showToast("Вес записан! ⚖️");
        await loadBootstrap();
      } catch (_) {}
    });

    // Food search with 220ms debounce
    setupFoodSearch(container);
  }

  function setupFoodSearch(container) {
    const searchInput = document.getElementById("food-search-input");
    const dropdown = document.getElementById("search-dropdown");
    const portionBox = document.getElementById("portion-selection-box");

    if (!searchInput || !dropdown || !portionBox) return;

    searchInput.addEventListener("input", () => {
      clearTimeout(state.searchTimeout);
      const q = searchInput.value.trim();
      if (q.length < 2) {
        dropdown.style.display = "none";
        return;
      }

      state.searchTimeout = setTimeout(async () => {
        try {
          const res = await apiFetch(`/api/food/search?q=${encodeURIComponent(q)}`);
          const items = res.data || [];
          if (items.length === 0) {
            dropdown.innerHTML = `
              <div style="padding:10px; font-size:0.8rem; color:var(--hint); text-align:center;">
                В каталоге не найдено. Нажмите Enter для быстрого ввода
              </div>
            `;
          } else {
            dropdown.innerHTML = items
              .map(
                (item) => `
              <div class="search-item" data-food-id="${item.id}">
                <div>
                  <div style="font-weight:600; font-size:0.88rem;">${escapeHtml(item.name)}</div>
                  <div style="font-size:0.72rem; color:var(--hint);">100г: ${item.calories_per_100g} ккал • Б:${item.protein_per_100g} Ж:${item.fat_per_100g} У:${item.carbs_per_100g}</div>
                </div>
                <span class="badge" style="font-size:0.75rem;">Выбрать</span>
              </div>
            `
              )
              .join("");

            dropdown.querySelectorAll("[data-food-id]").forEach((row) => {
              row.addEventListener("click", () => {
                const fid = parseInt(row.dataset.foodId, 10);
                const item = items.find((it) => it.id === fid);
                if (item) {
                  selectFoodProduct(item);
                }
              });
            });
          }
          dropdown.style.display = "flex";
        } catch (_) {}
      }, 220);
    });

    // Enter key triggers quick_text mode
    searchInput.addEventListener("keydown", async (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const text = searchInput.value.trim();
        if (!text) return;
        haptic("medium");
        try {
          await apiFetch("/api/meals", {
            method: "POST",
            body: JSON.stringify({
              mode: "quick_text",
              text,
              meal_type: state.selectedMealType,
            }),
          });
          showToast("Блюдо записано! 🍏");
          searchInput.value = "";
          dropdown.style.display = "none";
          await loadBootstrap();
        } catch (_) {}
      }
    });

    function selectFoodProduct(item) {
      dropdown.style.display = "none";
      portionBox.style.display = "flex";

      let portions = [];
      try {
        if (typeof item.portions === "string") {
          portions = JSON.parse(item.portions);
        } else if (Array.isArray(item.portions)) {
          portions = item.portions;
        }
      } catch (_) {}

      let portionChipsHtml = `
        <button type="button" class="btn-water-chip" data-grams="100">100 г</button>
      `;

      portions.forEach((p) => {
        portionChipsHtml += `
          <button type="button" class="btn-water-chip" data-grams="${p.grams}" data-pname="${escapeHtml(p.name)}">
            ${escapeHtml(p.name)} (${p.grams}г)
          </button>
        `;
      });

      portionBox.innerHTML = `
        <div style="background:var(--surface-hover); border-radius:var(--radius-sm); padding:10px; margin-top:6px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-weight:600;">${escapeHtml(item.name)}</span>
            <button type="button" class="btn-del-meal" id="btn-cancel-portion">✕</button>
          </div>
          <div class="portion-chips-row">
            ${portionChipsHtml}
          </div>
          <div style="display:flex; gap:6px; margin-top:8px;">
            <input type="number" class="form-input" id="custom-grams-input" placeholder="Свой вес (г)" style="width:130px;" min="1" max="5000">
            <button type="button" class="btn-timer-ctrl btn-primary" id="btn-add-portion">Добавить</button>
          </div>
        </div>
      `;

      document.getElementById("btn-cancel-portion")?.addEventListener("click", () => {
        portionBox.style.display = "none";
      });

      portionBox.querySelectorAll("[data-grams]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const grams = parseFloat(btn.dataset.grams);
          const pname = btn.dataset.pname || null;
          await submitCatalogMeal(item.id, grams, pname);
        });
      });

      document.getElementById("btn-add-portion")?.addEventListener("click", async () => {
        const customGrams = parseFloat(document.getElementById("custom-grams-input")?.value);
        if (isNaN(customGrams) || customGrams <= 0) {
          showToast("Введите вес в граммах", true);
          return;
        }
        await submitCatalogMeal(item.id, customGrams, null);
      });
    }

    async function submitCatalogMeal(foodId, grams, portionName) {
      haptic("medium");
      try {
        await apiFetch("/api/meals", {
          method: "POST",
          body: JSON.stringify({
            mode: "catalog",
            food_id: foodId,
            grams,
            portion_name: portionName,
            meal_type: state.selectedMealType,
          }),
        });
        showToast("Блюдо добавлено! 🍏");
        searchInput.value = "";
        portionBox.style.display = "none";
        await loadBootstrap();
      } catch (_) {}
    }
  }

  // =========================================================================
  // 4. STATS VIEW (Periods: 7, 15, 30, 90 days; Metrics: focus, habits, calories, water, weight)
  // =========================================================================
  async function loadStatsPeriod(period) {
    if (state.isLoadingStats) return;
    state.isLoadingStats = true;
    try {
      const res = await apiFetch(`/api/stats?period=${period}`);
      if (res && res.data) {
        state.statsCache[period] = res.data;
      }
    } catch (e) {
      showToast("Не удалось загрузить аналитику", true);
    } finally {
      state.isLoadingStats = false;
      const appView = document.getElementById("app-view");
      if (state.activeTab === "stats" && appView) {
        renderStatsView(appView);
      }
    }
  }

  function renderStatsView(container) {
    updateHeader();
    const period = state.statsPeriod || 7;
    const metric = state.statsMetric || "focus";

    let statsData = state.statsCache[period];
    if (!statsData && period === 7 && state.bootstrap?.stats_7d) {
      statsData = state.bootstrap.stats_7d;
    }

    if (!statsData) {
      container.innerHTML = `
        <div class="period-selector">
          <button type="button" class="period-pill ${period === 7 ? "active" : ""}" data-period="7">7 дней</button>
          <button type="button" class="period-pill ${period === 15 ? "active" : ""}" data-period="15">15 дней</button>
          <button type="button" class="period-pill ${period === 30 ? "active" : ""}" data-period="30">30 дней</button>
          <button type="button" class="period-pill ${period === 90 ? "active" : ""}" data-period="90">90 дней</button>
        </div>
        <div class="loading-state" style="margin-top:40px;">
          <div class="spinner"></div>
          <p>Загрузка аналитики за ${period} дней...</p>
        </div>
      `;
      setupStatsEvents(container);
      loadStatsPeriod(period);
      return;
    }

    const dates = statsData.dates || statsData.dates_7 || [];
    const habitsBreakdown = statsData.habits_breakdown || [];

    // 1. Compute Metric Values
    let kpiHtml = "";
    let chartSvg = "";
    let detailSectionHtml = "";

    const svgWidth = 330;
    const svgHeight = 160;
    const paddingLeft = 34;
    const paddingRight = 12;
    const paddingTop = 22;
    const paddingBottom = 26;
    const plotWidth = svgWidth - paddingLeft - paddingRight;
    const plotHeight = svgHeight - paddingTop - paddingBottom;
    const numDays = Math.max(1, dates.length);
    const colWidth = plotWidth / numDays;

    function getXLabelStep(len) {
      if (len <= 7) return 1;
      if (len <= 15) return 2;
      if (len <= 30) return 5;
      return 15;
    }
    const labelStep = getXLabelStep(numDays);

    if (metric === "focus") {
      const dailyFocusDict = statsData.daily_focus || {};
      const focusVals = dates.map((d) => (typeof dailyFocusDict[d] === "number" ? dailyFocusDict[d] : 0));
      const totalFocusMin = focusVals.reduce((acc, v) => acc + v, 0);
      const activeDays = focusVals.filter((v) => v > 0).length;
      const avgMin = Math.round(totalFocusMin / numDays);
      const maxMin = Math.max(30, ...focusVals);

      const hours = Math.floor(totalFocusMin / 60);
      const mins = totalFocusMin % 60;
      const totalStr = hours > 0 ? `${hours}ч ${mins}м` : `${mins} мин`;

      kpiHtml = `
        <div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:8px;">
          <div class="card" style="align-items:center; text-align:center;">
            <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">ВСЕГО В ФОКУСЕ</span>
            <span style="font-size:1.45rem; font-weight:700; color:var(--accent);">${totalStr}</span>
            <span style="font-size:0.72rem; color:var(--hint);">~${avgMin} мин / день</span>
          </div>
          <div class="card" style="align-items:center; text-align:center;">
            <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">АКТИВНЫХ ДНЕЙ</span>
            <span style="font-size:1.45rem; font-weight:700; color:var(--success);">${activeDays} / ${numDays}</span>
            <span style="font-size:0.72rem; color:var(--hint);">Рекорд: ${maxMin} мин</span>
          </div>
        </div>
      `;

      const barWidth = Math.max(3, colWidth - (numDays > 30 ? 1 : numDays > 15 ? 3 : 6));
      let bars = "";
      focusVals.forEach((val, idx) => {
        const barH = maxMin > 0 ? Math.round((val / maxMin) * plotHeight) : 0;
        const x = paddingLeft + idx * colWidth + (colWidth - barWidth) / 2;
        const y = paddingTop + (plotHeight - barH);

        const showLabel = (idx % labelStep === 0) || (idx === numDays - 1);
        const dParts = dates[idx].split("-");
        const shortDate = dParts.length === 3 ? `${dParts[2]}.${dParts[1]}` : dates[idx];

        bars += `
          <rect class="chart-bar" x="${x}" y="${y}" width="${barWidth}" height="${barH}">
            <title>${dates[idx]}: ${val} мин</title>
          </rect>
          ${val > 0 && numDays <= 15 ? `<text class="chart-text" x="${x + barWidth / 2}" y="${y - 4}" font-size="9">${val}</text>` : ""}
          ${showLabel ? `<text class="chart-text" x="${x + barWidth / 2}" y="${svgHeight - 6}" font-size="9">${shortDate}</text>` : ""}
        `;
      });

      chartSvg = `
        <svg class="svg-chart" viewBox="0 0 ${svgWidth} ${svgHeight}">
          <line x1="${paddingLeft}" y1="${paddingTop}" x2="${svgWidth - paddingRight}" y2="${paddingTop}" stroke="var(--surface-border)" stroke-dasharray="2 3"></line>
          <line x1="${paddingLeft}" y1="${paddingTop + plotHeight / 2}" x2="${svgWidth - paddingRight}" y2="${paddingTop + plotHeight / 2}" stroke="var(--surface-border)" stroke-dasharray="2 3"></line>
          <line x1="${paddingLeft}" y1="${paddingTop + plotHeight}" x2="${svgWidth - paddingRight}" y2="${paddingTop + plotHeight}" stroke="var(--surface-border)"></line>
          <text class="chart-text" x="${paddingLeft - 6}" y="${paddingTop + 4}" text-anchor="end" font-size="8">${maxMin}м</text>
          <text class="chart-text" x="${paddingLeft - 6}" y="${paddingTop + plotHeight}" text-anchor="end" font-size="8">0</text>
          ${bars}
        </svg>
      `;

      let habitsRows = habitsBreakdown.length === 0
        ? `<p style="font-size:0.8rem; color:var(--hint); text-align:center; padding:10px 0;">Нет данных по привычкам за этот период</p>`
        : habitsBreakdown.map((h) => `
            <div style="display:flex; justify-content:space-between; align-items:center; padding:7px 0; border-bottom:1px solid var(--surface-border);">
              <span style="font-weight:500; font-size:0.88rem;">${escapeHtml(h.name)}</span>
              <span class="badge" style="font-weight:600;">${h.habit_type === "checklist" ? `${h.total_repeats || 0} повт.` : `${h.total_spent || h.total_minutes || 0} мин`}</span>
            </div>
          `).join("");

      detailSectionHtml = `
        <div class="card">
          <span class="card-title">📋 Привычки за выбранный период</span>
          <div style="display:flex; flex-direction:column;">${habitsRows}</div>
        </div>
      `;

    } else if (metric === "habits") {
      const dailyCompletedDict = statsData.daily_completed || {};
      const completedVals = dates.map((d) => (typeof dailyCompletedDict[d] === "number" ? dailyCompletedDict[d] : 0));
      const totalCompleted = completedVals.reduce((acc, v) => acc + v, 0);
      const activeDays = completedVals.filter((v) => v > 0).length;
      const maxCompleted = Math.max(4, ...completedVals);

      kpiHtml = `
        <div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:8px;">
          <div class="card" style="align-items:center; text-align:center;">
            <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">ВЫПОЛНЕНО ПРИВЫЧЕК</span>
            <span style="font-size:1.45rem; font-weight:700; color:var(--success);">${totalCompleted}</span>
            <span style="font-size:0.72rem; color:var(--hint);">~${(totalCompleted / numDays).toFixed(1)} / день</span>
          </div>
          <div class="card" style="align-items:center; text-align:center;">
            <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">ПРОДУКТИВНЫХ ДНЕЙ</span>
            <span style="font-size:1.45rem; font-weight:700; color:var(--accent);">${activeDays} / ${numDays}</span>
            <span style="font-size:0.72rem; color:var(--hint);">Всего привычек: ${habitsBreakdown.length}</span>
          </div>
        </div>
      `;

      const barWidth = Math.max(3, colWidth - (numDays > 30 ? 1 : numDays > 15 ? 3 : 6));
      let bars = "";
      completedVals.forEach((val, idx) => {
        const barH = maxCompleted > 0 ? Math.round((val / maxCompleted) * plotHeight) : 0;
        const x = paddingLeft + idx * colWidth + (colWidth - barWidth) / 2;
        const y = paddingTop + (plotHeight - barH);

        const showLabel = (idx % labelStep === 0) || (idx === numDays - 1);
        const dParts = dates[idx].split("-");
        const shortDate = dParts.length === 3 ? `${dParts[2]}.${dParts[1]}` : dates[idx];

        bars += `
          <rect class="chart-bar" style="fill:var(--success);" x="${x}" y="${y}" width="${barWidth}" height="${barH}">
            <title>${dates[idx]}: ${val} выполнено</title>
          </rect>
          ${val > 0 && numDays <= 15 ? `<text class="chart-text" x="${x + barWidth / 2}" y="${y - 4}" font-size="9">${val}</text>` : ""}
          ${showLabel ? `<text class="chart-text" x="${x + barWidth / 2}" y="${svgHeight - 6}" font-size="9">${shortDate}</text>` : ""}
        `;
      });

      chartSvg = `
        <svg class="svg-chart" viewBox="0 0 ${svgWidth} ${svgHeight}">
          <line x1="${paddingLeft}" y1="${paddingTop}" x2="${svgWidth - paddingRight}" y2="${paddingTop}" stroke="var(--surface-border)" stroke-dasharray="2 3"></line>
          <line x1="${paddingLeft}" y1="${paddingTop + plotHeight / 2}" x2="${svgWidth - paddingRight}" y2="${paddingTop + plotHeight / 2}" stroke="var(--surface-border)" stroke-dasharray="2 3"></line>
          <line x1="${paddingLeft}" y1="${paddingTop + plotHeight}" x2="${svgWidth - paddingRight}" y2="${paddingTop + plotHeight}" stroke="var(--surface-border)"></line>
          <text class="chart-text" x="${paddingLeft - 6}" y="${paddingTop + 4}" text-anchor="end" font-size="8">${maxCompleted}</text>
          <text class="chart-text" x="${paddingLeft - 6}" y="${paddingTop + plotHeight}" text-anchor="end" font-size="8">0</text>
          ${bars}
        </svg>
      `;

      let habitsRows = habitsBreakdown.length === 0
        ? `<p style="font-size:0.8rem; color:var(--hint); text-align:center; padding:10px 0;">Нет активных привычек</p>`
        : habitsBreakdown.map((h) => `
            <div style="display:flex; justify-content:space-between; align-items:center; padding:7px 0; border-bottom:1px solid var(--surface-border);">
              <div>
                <span style="font-weight:500; font-size:0.88rem;">${escapeHtml(h.name)}</span>
                <span style="font-size:0.75rem; color:var(--hint); margin-left:6px;">🔥 ${h.streak_days || 0} дн.</span>
              </div>
              <span class="badge" style="background-color:color-mix(in srgb, var(--success) 15%, transparent); color:var(--success); font-weight:600;">
                ${h.completed_days || 0} / ${numDays} дн.
              </span>
            </div>
          `).join("");

      detailSectionHtml = `
        <div class="card">
          <span class="card-title">🎯 Выполнение по каждой привычке</span>
          <div style="display:flex; flex-direction:column;">${habitsRows}</div>
        </div>
      `;

    } else if (metric === "calories") {
      const dailyCalDict = statsData.daily_calories || {};
      const calVals = dates.map((d) => (typeof dailyCalDict[d] === "number" ? dailyCalDict[d] : 0));
      const targetCals = statsData.nutrition_goals?.target_calories || state.bootstrap?.nutrition?.goals?.target_calories || 2000;
      const loggedDays = calVals.filter((v) => v > 0).length;
      const totalCals = calVals.reduce((acc, v) => acc + v, 0);
      const avgCals = loggedDays > 0 ? Math.round(totalCals / loggedDays) : 0;
      const maxCals = Math.max(targetCals * 1.15, ...calVals, 1000);

      kpiHtml = `
        <div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:8px;">
          <div class="card" style="align-items:center; text-align:center;">
            <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">СРЕДНЯЯ КАЛОРИЙНОСТЬ</span>
            <span style="font-size:1.45rem; font-weight:700; color:#f43f5e;">${avgCals} ккал</span>
            <span style="font-size:0.72rem; color:var(--hint);">Цель: ${targetCals} ккал</span>
          </div>
          <div class="card" style="align-items:center; text-align:center;">
            <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">ДНЕЙ С ЗАПИСЯМИ</span>
            <span style="font-size:1.45rem; font-weight:700; color:var(--accent);">${loggedDays} / ${numDays}</span>
            <span style="font-size:0.72rem; color:var(--hint);">Всего: ${totalCals} ккал</span>
          </div>
        </div>
      `;

      const targetY = paddingTop + (plotHeight - Math.round((targetCals / maxCals) * plotHeight));
      const barWidth = Math.max(3, colWidth - (numDays > 30 ? 1 : numDays > 15 ? 3 : 6));
      let bars = "";
      calVals.forEach((val, idx) => {
        const barH = maxCals > 0 ? Math.round((val / maxCals) * plotHeight) : 0;
        const x = paddingLeft + idx * colWidth + (colWidth - barWidth) / 2;
        const y = paddingTop + (plotHeight - barH);

        const showLabel = (idx % labelStep === 0) || (idx === numDays - 1);
        const dParts = dates[idx].split("-");
        const shortDate = dParts.length === 3 ? `${dParts[2]}.${dParts[1]}` : dates[idx];

        bars += `
          <rect class="chart-bar" style="fill:#f43f5e;" x="${x}" y="${y}" width="${barWidth}" height="${barH}">
            <title>${dates[idx]}: ${val} ккал</title>
          </rect>
          ${val > 0 && numDays <= 15 ? `<text class="chart-text" x="${x + barWidth / 2}" y="${y - 4}" font-size="8">${val}</text>` : ""}
          ${showLabel ? `<text class="chart-text" x="${x + barWidth / 2}" y="${svgHeight - 6}" font-size="9">${shortDate}</text>` : ""}
        `;
      });

      chartSvg = `
        <svg class="svg-chart" viewBox="0 0 ${svgWidth} ${svgHeight}">
          <line x1="${paddingLeft}" y1="${targetY}" x2="${svgWidth - paddingRight}" y2="${targetY}" class="chart-target-line"></line>
          <text x="${paddingLeft + 4}" y="${targetY - 3}" font-size="8" fill="var(--warning)">Цель: ${targetCals}</text>

          <line x1="${paddingLeft}" y1="${paddingTop + plotHeight}" x2="${svgWidth - paddingRight}" y2="${paddingTop + plotHeight}" stroke="var(--surface-border)"></line>
          <text class="chart-text" x="${paddingLeft - 6}" y="${paddingTop + 4}" text-anchor="end" font-size="8">${Math.round(maxCals)}</text>
          <text class="chart-text" x="${paddingLeft - 6}" y="${paddingTop + plotHeight}" text-anchor="end" font-size="8">0</text>
          ${bars}
        </svg>
      `;

    } else if (metric === "water") {
      const dailyWaterDict = statsData.daily_water || {};
      const waterVals = dates.map((d) => (typeof dailyWaterDict[d] === "number" ? dailyWaterDict[d] : 0));
      const targetWater = statsData.water_target || state.bootstrap?.water?.target_ml || 2000;
      const loggedDays = waterVals.filter((v) => v > 0).length;
      const goalReachedDays = waterVals.filter((v) => v >= targetWater).length;
      const totalWater = waterVals.reduce((acc, v) => acc + v, 0);
      const avgWater = loggedDays > 0 ? Math.round(totalWater / loggedDays) : 0;
      const maxWater = Math.max(targetWater * 1.15, ...waterVals, 1000);

      kpiHtml = `
        <div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:8px;">
          <div class="card" style="align-items:center; text-align:center;">
            <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">СРЕДНЕЕ ПОТРЕБЛЕНИЕ</span>
            <span style="font-size:1.45rem; font-weight:700; color:#0ea5e9;">${avgWater} мл</span>
            <span style="font-size:0.72rem; color:var(--hint);">Норма: ${targetWater} мл</span>
          </div>
          <div class="card" style="align-items:center; text-align:center;">
            <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">НОРМА ДОСТИГНУТА</span>
            <span style="font-size:1.45rem; font-weight:700; color:var(--success);">${goalReachedDays} дн.</span>
            <span style="font-size:0.72rem; color:var(--hint);">${Math.round((goalReachedDays / numDays) * 100)}% дней</span>
          </div>
        </div>
      `;

      const targetY = paddingTop + (plotHeight - Math.round((targetWater / maxWater) * plotHeight));
      const barWidth = Math.max(3, colWidth - (numDays > 30 ? 1 : numDays > 15 ? 3 : 6));
      let bars = "";
      waterVals.forEach((val, idx) => {
        const barH = maxWater > 0 ? Math.round((val / maxWater) * plotHeight) : 0;
        const x = paddingLeft + idx * colWidth + (colWidth - barWidth) / 2;
        const y = paddingTop + (plotHeight - barH);

        const showLabel = (idx % labelStep === 0) || (idx === numDays - 1);
        const dParts = dates[idx].split("-");
        const shortDate = dParts.length === 3 ? `${dParts[2]}.${dParts[1]}` : dates[idx];

        bars += `
          <rect class="chart-bar" style="fill:#0ea5e9;" x="${x}" y="${y}" width="${barWidth}" height="${barH}">
            <title>${dates[idx]}: ${val} мл</title>
          </rect>
          ${val > 0 && numDays <= 15 ? `<text class="chart-text" x="${x + barWidth / 2}" y="${y - 4}" font-size="8">${val}</text>` : ""}
          ${showLabel ? `<text class="chart-text" x="${x + barWidth / 2}" y="${svgHeight - 6}" font-size="9">${shortDate}</text>` : ""}
        `;
      });

      chartSvg = `
        <svg class="svg-chart" viewBox="0 0 ${svgWidth} ${svgHeight}">
          <line x1="${paddingLeft}" y1="${targetY}" x2="${svgWidth - paddingRight}" y2="${targetY}" class="chart-target-line"></line>
          <text x="${paddingLeft + 4}" y="${targetY - 3}" font-size="8" fill="var(--warning)">Цель: ${targetWater} мл</text>

          <line x1="${paddingLeft}" y1="${paddingTop + plotHeight}" x2="${svgWidth - paddingRight}" y2="${paddingTop + plotHeight}" stroke="var(--surface-border)"></line>
          <text class="chart-text" x="${paddingLeft - 6}" y="${paddingTop + 4}" text-anchor="end" font-size="8">${Math.round(maxWater)}</text>
          <text class="chart-text" x="${paddingLeft - 6}" y="${paddingTop + plotHeight}" text-anchor="end" font-size="8">0</text>
          ${bars}
        </svg>
      `;

    } else if (metric === "weight") {
      const dailyWeightDict = statsData.daily_weight || {};
      const measuredEntries = [];
      dates.forEach((d, idx) => {
        if (typeof dailyWeightDict[d] === "number") {
          measuredEntries.push({ date: d, weight: dailyWeightDict[d], idx });
        }
      });

      if (measuredEntries.length === 0) {
        kpiHtml = `
          <div class="card" style="text-align:center; padding:18px;">
            <span style="font-size:1.1rem; font-weight:600;">⚖️ Нет записей веса</span>
            <p style="font-size:0.8rem; color:var(--hint); margin-top:6px;">За выбранный период (${numDays} дней) вес не был зафиксирован. Запишите вес на вкладке «Здоровье».</p>
          </div>
        `;
        chartSvg = "";
      } else {
        const weights = measuredEntries.map((e) => e.weight);
        const latestW = weights[weights.length - 1];
        const firstW = weights[0];
        const minW = Math.min(...weights);
        const maxW = Math.max(...weights);
        const diff = (latestW - firstW).toFixed(1);
        const diffStr = diff > 0 ? `+${diff} кг` : `${diff} кг`;
        const diffColor = diff < 0 ? "var(--success)" : diff > 0 ? "var(--warning)" : "var(--hint)";

        kpiHtml = `
          <div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:8px;">
            <div class="card" style="align-items:center; text-align:center;">
              <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">ТЕКУЩИЙ ВЕС</span>
              <span style="font-size:1.45rem; font-weight:700; color:var(--text);">${latestW} кг</span>
              <span style="font-size:0.72rem; color:${diffColor}; font-weight:600;">${diffStr} за период</span>
            </div>
            <div class="card" style="align-items:center; text-align:center;">
              <span style="font-size:0.72rem; color:var(--hint); font-weight:600;">ДИАПАЗОН ВЕСА</span>
              <span style="font-size:1.45rem; font-weight:700; color:var(--accent);">${minW} — ${maxW}</span>
              <span style="font-size:0.72rem; color:var(--hint);">Записей: ${measuredEntries.length}</span>
            </div>
          </div>
        `;

        const wRange = Math.max(1.0, maxW - minW);
        const plotMin = minW - wRange * 0.15;
        const plotMax = maxW + wRange * 0.15;
        const fullWRange = plotMax - plotMin;

        const points = measuredEntries.map((e) => {
          const x = paddingLeft + e.idx * colWidth + colWidth / 2;
          const y = paddingTop + (plotHeight - ((e.weight - plotMin) / fullWRange) * plotHeight);
          return { x, y, weight: e.weight, date: e.date };
        });

        const pointsStr = points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
        const firstX = points[0].x.toFixed(1);
        const lastX = points[points.length - 1].x.toFixed(1);
        const bottomY = (paddingTop + plotHeight).toFixed(1);
        const areaStr = `${firstX},${bottomY} ${pointsStr} ${lastX},${bottomY}`;

        let dotsSvg = "";
        points.forEach((p) => {
          dotsSvg += `
            <circle class="chart-point" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3.5">
              <title>${p.date}: ${p.weight} кг</title>
            </circle>
            <text class="chart-text" x="${p.x.toFixed(1)}" y="${(p.y - 6).toFixed(1)}" font-size="8">${p.weight}</text>
          `;
        });

        let xAxisLabels = "";
        dates.forEach((d, idx) => {
          if ((idx % labelStep === 0) || (idx === numDays - 1)) {
            const x = paddingLeft + idx * colWidth + colWidth / 2;
            const dParts = d.split("-");
            const shortDate = dParts.length === 3 ? `${dParts[2]}.${dParts[1]}` : d;
            xAxisLabels += `<text class="chart-text" x="${x}" y="${svgHeight - 6}" font-size="9">${shortDate}</text>`;
          }
        });

        chartSvg = `
          <svg class="svg-chart" viewBox="0 0 ${svgWidth} ${svgHeight}">
            <polygon class="chart-area" points="${areaStr}"></polygon>
            <polyline class="chart-line" points="${pointsStr}"></polyline>
            ${dotsSvg}
            <line x1="${paddingLeft}" y1="${paddingTop + plotHeight}" x2="${svgWidth - paddingRight}" y2="${paddingTop + plotHeight}" stroke="var(--surface-border)"></line>
            ${xAxisLabels}
          </svg>
        `;

        let historyRows = measuredEntries.slice().reverse().slice(0, 5).map((e) => `
          <div style="display:flex; justify-content:space-between; align-items:center; padding:7px 0; border-bottom:1px solid var(--surface-border);">
            <span style="font-weight:500; font-size:0.88rem;">${e.date}</span>
            <span class="badge" style="font-weight:600;">${e.weight} кг</span>
          </div>
        `).join("");

        detailSectionHtml = `
          <div class="card">
            <span class="card-title">⚖️ Последние измерения веса</span>
            <div style="display:flex; flex-direction:column;">${historyRows}</div>
          </div>
        `;
      }
    }

    container.innerHTML = `
      <!-- Period Selector Pills -->
      <div class="period-selector">
        <button type="button" class="period-pill ${period === 7 ? "active" : ""}" data-period="7">7 дней</button>
        <button type="button" class="period-pill ${period === 15 ? "active" : ""}" data-period="15">15 дней</button>
        <button type="button" class="period-pill ${period === 30 ? "active" : ""}" data-period="30">30 дней</button>
        <button type="button" class="period-pill ${period === 90 ? "active" : ""}" data-period="90">90 дней</button>
      </div>

      <!-- Metric Switcher Tabs -->
      <div class="metric-tabs">
        <button type="button" class="metric-tab-btn ${metric === "focus" ? "active" : ""}" data-metric="focus">⏱ Фокус</button>
        <button type="button" class="metric-tab-btn ${metric === "habits" ? "active" : ""}" data-metric="habits">🎯 Привычки</button>
        <button type="button" class="metric-tab-btn ${metric === "calories" ? "active" : ""}" data-metric="calories">🔥 Калории</button>
        <button type="button" class="metric-tab-btn ${metric === "water" ? "active" : ""}" data-metric="water">💧 Вода</button>
        <button type="button" class="metric-tab-btn ${metric === "weight" ? "active" : ""}" data-metric="weight">⚖️ Вес</button>
      </div>

      <!-- KPI Summary Cards -->
      ${kpiHtml}

      <!-- Interactive Native SVG Chart -->
      ${chartSvg ? `
        <div class="card">
          <span class="card-title">${
            metric === "focus" ? "⏱ Минуты концентрации по дням" :
            metric === "habits" ? "🎯 Выполнение привычек по дням" :
            metric === "calories" ? "🔥 Калории относительно цели (ккал)" :
            metric === "water" ? "💧 Выпитая вода относительно нормы (мл)" :
            "⚖️ Динамика изменения веса (кг)"
          }</span>
          <div class="chart-container">
            ${chartSvg}
          </div>
        </div>
      ` : ""}

      <!-- Detail Breakdowns -->
      ${detailSectionHtml}
    `;

    setupStatsEvents(container);
  }

  function setupStatsEvents(container) {
    container.querySelectorAll(".period-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        const p = parseInt(pill.dataset.period, 10);
        if (p === state.statsPeriod) return;
        haptic("light");
        state.statsPeriod = p;
        renderStatsView(container);
      });
    });

    container.querySelectorAll(".metric-tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const m = btn.dataset.metric;
        if (m === state.statsMetric) return;
        haptic("light");
        state.statsMetric = m;
        renderStatsView(container);
      });
    });
  }

  // =========================================================================
  // 5. SETTINGS VIEW (Timezone, Checkin, Weight Reminder, Nutrition Goals, Mute)
  // =========================================================================
  function renderSettingsView(container) {
    const user = state.bootstrap?.user || {};
    const goals = state.bootstrap?.nutrition?.goals || {};
    const water = state.bootstrap?.water || {};

    const tz = user.utc_offset !== undefined ? user.utc_offset : 3;
    const checkinTime = user.checkin_time || "21:00";
    const weightRemEnabled = Boolean(user.weight_reminder_enabled);
    const weightRemTime = user.weight_reminder_time || "08:00";
    const muteUntil = user.notifications_muted_until || 0;
    const nowTs = Math.floor(Date.now() / 1000);
    const isMuted = muteUntil > nowTs || muteUntil === 2147483647;

    container.innerHTML = `
      <!-- Timezone & Checkin Time -->
      <div class="card">
        <span class="card-title">⏰ Часовой пояс и напоминания</span>
        <div class="form-group">
          <label class="form-label">Часовой пояс (UTC)</label>
          <div style="display:flex; gap:6px;">
            <select class="form-input" id="set-tz-select" style="flex:1;">
              ${Array.from({ length: 27 }, (_, i) => i - 12)
                .map((off) => `<option value="${off}" ${off === tz ? "selected" : ""}>UTC${off >= 0 ? "+" : ""}${off}</option>`)
                .join("")}
            </select>
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">Время вечернего чек-ина (ЧЧ:ММ)</label>
          <input type="time" class="form-input" id="set-checkin-time" value="${checkinTime}">
        </div>

        <div class="form-group" style="padding-top:6px; border-top:1px solid var(--surface-border);">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <label class="form-label" style="margin:0;">Утреннее напоминание о весе</label>
            <input type="checkbox" id="set-weight-rem-toggle" ${weightRemEnabled ? "checked" : ""} style="width:18px; height:18px; accent-color:var(--accent);">
          </div>
          <div id="set-weight-time-box" style="margin-top:6px; ${weightRemEnabled ? "display:block;" : "display:none;"}">
            <label class="form-label">Время взвешивания</label>
            <input type="time" class="form-input" id="set-weight-time" value="${weightRemTime}">
          </div>
        </div>

        <button type="button" class="btn-timer-ctrl btn-primary" id="btn-save-general-settings" style="margin-top:6px;">Сохранить параметры</button>
      </div>

      <!-- Nutrition & Water Goals -->
      <div class="card">
        <span class="card-title">🎯 Цели питания и трекинга</span>
        <div class="form-group">
          <label class="form-label">Дневная норма калорий (ккал)</label>
          <input type="number" class="form-input" id="set-goal-cals" value="${goals.target_calories || 2000}" min="500" max="10000">
        </div>

        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px;">
          <label class="form-label" style="margin:0;">Следить за БЖУ (белки, жиры, углеводы)</label>
          <input type="checkbox" id="set-track-bju-toggle" ${goals.track_bju ? "checked" : ""} style="width:18px; height:18px; accent-color:var(--accent);">
        </div>

        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px;">
          <label class="form-label" style="margin:0;">Следить за водным балансом</label>
          <input type="checkbox" id="set-track-water-toggle" ${goals.track_water ? "checked" : ""} style="width:18px; height:18px; accent-color:var(--accent);">
        </div>

        <div class="form-group" id="set-water-target-box" style="margin-top:6px; ${goals.track_water ? "display:block;" : "display:none;"}">
          <label class="form-label">Цель воды в день (мл)</label>
          <input type="number" class="form-input" id="set-goal-water" value="${water.target_ml || 2000}" min="500" max="10000" step="100">
        </div>

        <button type="button" class="btn-timer-ctrl btn-primary" id="btn-save-nutrition-goals" style="margin-top:8px;">Сохранить цели</button>
      </div>

      <!-- Notifications Mute Card -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">🔕 Уведомления</span>
          <span class="badge ${isMuted ? "badge-ice" : "badge-success"}">
            ${isMuted ? "Заглушены" : "Активны"}
          </span>
        </div>
        <div class="pill-group" style="width:100%; display:grid; grid-template-columns:repeat(4, 1fr); gap:2px;">
          <button type="button" class="pill-btn ${!isMuted ? "active" : ""}" data-mute="unmute">Вкл</button>
          <button type="button" class="pill-btn" data-mute="3600">1 час</button>
          <button type="button" class="pill-btn" data-mute="86400">24ч</button>
          <button type="button" class="pill-btn ${muteUntil === 2147483647 ? "active" : ""}" data-mute="forever">Всегда</button>
        </div>
      </div>
    `;

    document.getElementById("set-weight-rem-toggle")?.addEventListener("change", (e) => {
      document.getElementById("set-weight-time-box").style.display = e.target.checked ? "block" : "none";
    });

    document.getElementById("set-track-water-toggle")?.addEventListener("change", (e) => {
      document.getElementById("set-water-target-box").style.display = e.target.checked ? "block" : "none";
    });

    // Save general settings
    document.getElementById("btn-save-general-settings")?.addEventListener("click", async () => {
      haptic("medium");
      const offset = parseInt(document.getElementById("set-tz-select")?.value, 10);
      const ciTime = document.getElementById("set-checkin-time")?.value;
      const weightRem = document.getElementById("set-weight-rem-toggle")?.checked;
      const weightTime = document.getElementById("set-weight-time")?.value;

      try {
        await apiFetch("/api/settings", {
          method: "PATCH",
          body: JSON.stringify({
            utc_offset: offset,
            checkin_time: ciTime,
            weight_reminder_enabled: weightRem,
            weight_reminder_time: weightTime,
          }),
        });
        showToast("Настройки сохранены! ⚙️");
        await loadBootstrap();
      } catch (_) {}
    });

    // Save nutrition goals
    document.getElementById("btn-save-nutrition-goals")?.addEventListener("click", async () => {
      haptic("medium");
      const cals = parseInt(document.getElementById("set-goal-cals")?.value, 10);
      const trackBju = document.getElementById("set-track-bju-toggle")?.checked;
      const trackWater = document.getElementById("set-track-water-toggle")?.checked;
      const waterTgt = parseInt(document.getElementById("set-goal-water")?.value, 10);

      try {
        await apiFetch("/api/nutrition/goals", {
          method: "PATCH",
          body: JSON.stringify({
            target_calories: cals,
            track_bju: trackBju,
            track_water: trackWater,
            water_target_ml: waterTgt,
          }),
        });
        showToast("Цели обновлены! 🎯");
        await loadBootstrap();
      } catch (_) {}
    });

    // Mute actions
    container.querySelectorAll("[data-mute]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        haptic("medium");
        const action = btn.dataset.mute;
        const payload = {};
        if (action === "unmute") {
          payload.mute_action = "unmute";
        } else if (action === "forever") {
          payload.mute_action = "forever";
        } else {
          payload.mute_action = parseInt(action, 10);
        }

        try {
          await apiFetch("/api/settings", {
            method: "PATCH",
            body: JSON.stringify(payload),
          });
          showToast("Статус уведомлений обновлен");
          await loadBootstrap();
        } catch (_) {}
      });
    });
  }

  // App Bootstrap
  document.addEventListener("DOMContentLoaded", () => {
    initTelegram();
    setupTabs();
    setupFloatingFocusBarEvents();
    loadBootstrap();
  });
})();
