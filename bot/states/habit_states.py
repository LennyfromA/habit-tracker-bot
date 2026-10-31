"""FSM state groups for onboarding, habit creation, and timer reflections."""

from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    """States for user registration and timezone configuration."""

    waiting_for_timezone = State()
    waiting_for_checkin_time = State()


class HabitCreationStates(StatesGroup):
    """States for interactive multi-step habit creation."""

    waiting_for_name = State()
    waiting_for_type = State()
    waiting_for_target_minutes = State()
    waiting_for_repeats = State()
    waiting_for_reminder_time = State()
    waiting_for_active_days = State()


class TimerStates(StatesGroup):
    """States for active timer session reflections."""

    waiting_for_reflection = State()


class HabitEditStates(StatesGroup):
    """States for modifying existing habit properties."""

    waiting_for_name = State()
    waiting_for_target_minutes = State()
    waiting_for_repeats = State()
    waiting_for_reminder_time = State()
    waiting_for_active_days = State()
