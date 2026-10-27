"""FSM state groups for food logging and nutrition goal setup."""

from aiogram.fsm.state import State, StatesGroup


class NutritionStates(StatesGroup):
    """States for adding meals, water, weight, and configuring nutrition/reminder goals."""

    waiting_for_meal_text = State()
    waiting_for_calories_goal = State()
    waiting_for_bju_goal = State()
    waiting_for_water_amount = State()
    waiting_for_water_goal = State()
    waiting_for_weight_input = State()
    waiting_for_weight_reminder_time = State()
