# Habit Tracker & Focus Timer Telegram Bot 🚀

Production-ready, multi-user Telegram bot for habit tracking, deep focus sessions with interactive timers, daily evening reviews, streak protection via freezes, and visual progress reports.

Built with **Python 3.11+**, **aiogram 3.x**, **aiosqlite**, **APScheduler**, and **matplotlib**.

---

## 🌟 Key Features

1. **Smart Onboarding & Timezone Handling**:
   - Timezone detection via preset buttons (`UTC+2`, `UTC+3`, `UTC+5`, `UTC+7`) or freeform current local time string (e.g. `19:40`).
   - Automatically computes user's UTC offset without requiring geolocation permissions.
   - Configurable evening check-in time (default `21:00`).

2. **Habit Management & Active Days**:
   - Active day filtering (`1=Mon` through `7=Sun`), e.g., weekdays vs daily.
   - Interactive multi-step creation (FSM) or one-line quick syntax:
     - `/new Английский 45 weekdays`
     - `/new Бег 30 1,3,5`
     - `/new Чтение 20`
   - Visual progress bars in habit list: `[■■■■□□□□] 30/60 мин (Осталось 30 мин) | 🔥 Стрик: 5 дней`.
   - **Streak Freeze (Заморозка дня)**: 1 freeze per week (resets on Monday). Protects habit streak from resetting if missed.

3. **Interactive Focus Timer (`timer.py`)**:
   - In-memory & DB persistent timer sessions using Unix epoch timestamps (immune to sleep drift and server restarts).
   - In-message controls:
     - `[⏸ Пауза]` / `[▶ Возобновить]`
     - `[➕ +10 мин]` (extends duration on the fly)
     - `[⏹ Завершить досрочно]` (calculates actual minutes spent and records progress)
   - Auto-finish alerts with sound notifications when timer runs out.
   - Post-session reflection note saved directly to daily productivity log.

4. **Background Scheduler (APScheduler)**:
   - 60-second global tick:
     - Scans running timers and finishes completed sessions.
     - Dispatches interactive evening check-in dashboard at user's local `checkin_time`.
     - Executes local midnight reset (00:00) per user: evaluates completed/frozen habits, increments or resets streaks, clears daily freeze status, and refills freezes on Sunday midnight (Monday 00:00).

5. **Statistics & Reports**:
   - `/stats`: Summary text with completion rate % + dark-themed **matplotlib** chart rendered in-memory (daily bar chart + habit focus distribution).
   - `/export`: Exports weekly report as a `.md` (Markdown) file with checkmarks, durations, and notes.

---

## 📂 Project Structure

```text
habit_tracker_bot/
├── bot/
│   ├── config.py             # Pydantic Settings (BOT_TOKEN, DB_PATH, LOG_LEVEL)
│   ├── database/
│   │   ├── db.py             # SQLite WAL connection pool & DDL scripts
│   │   ├── users_repo.py     # User queries, timezone, weekly freezes
│   │   ├── habits_repo.py    # Habit definitions, daily logs, streaks
│   │   └── timers_repo.py    # Active timer session persistence
│   ├── handlers/
│   │   ├── start.py          # /start, timezone & checkin setup FSM
│   │   ├── habits.py         # List, create (FSM + quick syntax), delete, freeze
│   │   ├── timer.py          # Start focus, pause, resume, add time, early stop
│   │   ├── checkin.py        # Evening review interactive dashboard
│   │   └── stats.py          # Text summary, matplotlib chart, .md export
│   ├── keyboards/
│   │   ├── inline.py         # Inline keyboards (timers, checkin, days selector)
│   │   └── reply.py          # Main persistent navigation menu
│   ├── middlewares/
│   │   └── user_check.py     # Auth check & automatic user timezone injection
│   ├── services/
│   │   ├── scheduler.py      # APScheduler 60s tick, timers, checkin, midnight reset
│   │   └── chart_builder.py  # In-memory dark-themed matplotlib chart renderer
│   └── states/
│       └── habit_states.py   # Aiogram FSM StatesGroups
├── .env.example
├── .env
├── main.py                   # Bot entrypoint & lifecycle management
├── pytest.ini
├── requirements.txt
└── run_tests.py              # Verification test suite
```

---

## 🛠 Database Schema

- `users`: Stores Telegram user profile, calculated `utc_offset`, evening `checkin_time`, and weekly freeze balances.
- `habits`: Stores habit definitions, daily target minutes, active days mask (`'1,2,3,4,5,6,7'`), current streak count, and today's freeze flag.
- `habit_logs`: Daily execution records (`logged_date`, `spent_minutes`, `is_completed`, `notes`) with `UNIQUE(habit_id, logged_date)`.
- `active_timers`: Persists in-flight focus timers (`user_id`, `habit_id`, `started_at`, `total_target_seconds`, `elapsed_before_pause`, `is_paused`, `message_id`, `chat_id`).

---

## 🚀 Quick Start

### 1. Clone & Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and provide your Telegram Bot Token:
```env
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
DB_PATH=habit_tracker.db
LOG_LEVEL=INFO
```

### 3. Run Test Suite
```bash
python run_tests.py
# or
python -m pytest tests/test_all.py -v
```

### 4. Start the Bot
```bash
python main.py
```
