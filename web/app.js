/**
 * Telegram Habit Tracker & Health Widget Frontend App
 * Pure Vanilla ES6 JS with zero heavy dependencies
 */

(function () {
  "use strict";

  // Global Application State
  const state = {
    bootstrap: null,
    activeTab: "habits",
    isCheckinMode: false,
    wid: "",
    timerInterval: null,
    searchTimeout: null,
    expandedHabitId: null,
    editingHabit: null,
    showNewHabitForm: false,
    selectedMealType: "snack",
    selectedFoodItem: null,
    timerReflectionData: null,
    filterHabits: "today", // 'today' or 'all'
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
    state.isDev = params.get("dev") === "1" || params.get("preview") === "1";
    const viewParam = params.get("view") || "habits";

    if (viewParam === "checkin") {
      state.isCheckinMode = true;
      state.activeTab = "habits";
    } else if (["habits", "timer", "health", "stats", "settings"].includes(viewParam)) {
      state.activeTab = viewParam;
    } else {
      state.activeTab = "habits";
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

  // Unified API Fetch wrapper
  async function apiFetch(url, options = {}) {
    let finalUrl = url;
    if (state.isDev && !finalUrl.includes("dev=1")) {
      finalUrl += (finalUrl.includes("?") ? "&" : "?") + "dev=1";
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
      showToast(err.message || "Ошибка соединения", true);
      haptic("error");
      throw err;
    }
  }

  // Load Initial Bootstrap Data
  async function loadBootstrap() {
    try {
      const res = await apiFetch("/api/bootstrap");
      state.bootstrap = res.data;
      updateHeader();
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
      habits: state.isCheckinMode ? "Вечерний чек-ин" : "Мои привычки",
      timer: "Фокус-сессия",
      health: "Здоровье и КБЖУ",
      stats: "Итоги недели",
      settings: "Настройки",
    };

    titleEl.textContent = titles[state.activeTab] || "Привычки";

    if (state.bootstrap) {
      dateEl.textContent = `${state.bootstrap.local_date}`;
    }
  }

  // Tab switching
  function setupTabs() {
    const tabs = document.querySelectorAll(".nav-btn");
    tabs.forEach((tab) => {
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

    if (state.activeTab === "habits") {
      renderHabitsView(appView);
    } else if (state.activeTab === "timer") {
      renderTimerView(appView);
    } else if (state.activeTab === "health") {
      renderHealthView(appView);
    } else if (state.activeTab === "stats") {
      renderStatsView(appView);
    } else if (state.activeTab === "settings") {
      renderSettingsView(appView);
    }
  }

  // =========================================================================
  // 1. HABITS VIEW
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
  // 4. STATS VIEW (KPI cards, native SVG focus chart, habit breakdowns)
  // =========================================================================
  function renderStatsView(container) {
    const s7 = state.bootstrap?.stats_7d || {};
    const dailyFocus = s7.daily_focus || [];
    const habitsBreakdown = s7.habits_breakdown || [];
    const dailyCals = s7.daily_calories || [];

    const totalFocusMin = dailyFocus.reduce((sum, d) => sum + (d.minutes || 0), 0);
    const activeDaysCount = dailyFocus.filter((d) => (d.minutes || 0) > 0).length;

    // Build Native SVG 7-Day Bar Chart
    const svgWidth = 320;
    const svgHeight = 150;
    const paddingLeft = 30;
    const paddingRight = 10;
    const paddingTop = 20;
    const paddingBottom = 30;

    const maxMin = Math.max(30, ...dailyFocus.map((d) => d.minutes || 0));
    const plotWidth = svgWidth - paddingLeft - paddingRight;
    const plotHeight = svgHeight - paddingTop - paddingBottom;
    const colWidth = plotWidth / Math.max(1, dailyFocus.length);
    const barWidth = Math.max(12, colWidth - 8);

    let barsSvg = "";
    dailyFocus.forEach((d, idx) => {
      const minutes = d.minutes || 0;
      const barH = Math.round((minutes / maxMin) * plotHeight);
      const x = paddingLeft + idx * colWidth + (colWidth - barWidth) / 2;
      const y = paddingTop + (plotHeight - barH);

      // Date label (DD.MM)
      const dateParts = (d.date || "").split("-");
      const shortDate = dateParts.length === 3 ? `${dateParts[2]}.${dateParts[1]}` : d.date;

      barsSvg += `
        <rect class="chart-bar" x="${x}" y="${y}" width="${barWidth}" height="${barH}"></rect>
        <text class="chart-text" x="${x + barWidth / 2}" y="${y - 4}">${minutes > 0 ? minutes : ""}</text>
        <text class="chart-text" x="${x + barWidth / 2}" y="${svgHeight - 8}">${shortDate}</text>
      `;
    });

    let habitsBreakdownHtml = "";
    if (habitsBreakdown.length === 0) {
      habitsBreakdownHtml = `<p style="font-size:0.8rem; color:var(--hint); text-align:center;">Нет данных за неделю</p>`;
    } else {
      habitsBreakdownHtml = habitsBreakdown
        .map((h) => {
          const isChecklist = h.habit_type === "checklist";
          const valStr = isChecklist
            ? `${h.total_repeats || 0} повторений`
            : `${h.total_minutes || 0} мин`;
          return `
            <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid var(--surface-border);">
              <span style="font-weight:500; font-size:0.88rem;">${escapeHtml(h.name)}</span>
              <span class="badge">${valStr}</span>
            </div>
          `;
        })
        .join("");
    }

    container.innerHTML = `
      <!-- KPI Row -->
      <div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:8px;">
        <div class="card" style="align-items:center; text-align:center;">
          <span style="font-size:0.75rem; color:var(--hint); font-weight:600;">ФОКУС ЗА 7 ДНЕЙ</span>
          <span style="font-size:1.6rem; font-weight:700; color:var(--accent);">${totalFocusMin} мин</span>
        </div>
        <div class="card" style="align-items:center; text-align:center;">
          <span style="font-size:0.75rem; color:var(--hint); font-weight:600;">АКТИВНЫХ ДНЕЙ</span>
          <span style="font-size:1.6rem; font-weight:700; color:var(--success);">${activeDaysCount} / 7</span>
        </div>
      </div>

      <!-- Native SVG Focus Chart -->
      <div class="card">
        <span class="card-title">⏱ Минуты фокуса по дням</span>
        <div class="chart-container">
          <svg class="svg-chart" viewBox="0 0 ${svgWidth} ${svgHeight}">
            ${barsSvg}
          </svg>
        </div>
      </div>

      <!-- Habits Weekly Summary -->
      <div class="card">
        <span class="card-title">📋 Привычки за последние 7 дней</span>
        <div style="display:flex; flex-direction:column;">
          ${habitsBreakdownHtml}
        </div>
      </div>
    `;
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
    loadBootstrap();
  });
})();
