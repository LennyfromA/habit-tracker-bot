"""Matplotlib-based in-memory chart generation for productivity statistics."""

import io
from datetime import datetime, timedelta
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt


def build_weekly_stats_chart(
    daily_stats: Dict[str, int],
    habits_stats: List[Dict[str, Any]],
    end_date_str: str,
) -> io.BytesIO:
    """
    Generate a 2-subplot dark-themed weekly progress chart in-memory.

    Subplot 1: Bar chart of daily focus minutes for the last 7 days.
    Subplot 2: Horizontal bar chart of focus minutes per habit.
    """
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
    dates_7 = [(end_dt - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]

    # Prepare data for Subplot 1
    day_labels = [datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m") for d in dates_7]
    day_minutes = [daily_stats.get(d, 0) for d in dates_7]

    # Prepare data for Subplot 2
    habit_names = [h["name"] for h in habits_stats] if habits_stats else ["Нет привычек"]
    habit_minutes = [h.get("total_spent", 0) for h in habits_stats] if habits_stats else [0]

    # Matplotlib styling (Dark theme)
    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 8), dpi=130)
    fig.patch.set_facecolor("#181825")

    # --- Subplot 1: Daily Focus Minutes ---
    ax1.set_facecolor("#1E1E2E")
    bars1 = ax1.bar(day_labels, day_minutes, color="#89B4FA", width=0.55, edgecolor="#B4BEFE", linewidth=1.2)
    ax1.set_title("Фокус по дням (последние 7 дней)", fontsize=13, fontweight="bold", color="#CDD6F4", pad=12)
    ax1.set_ylabel("Минуты", fontsize=11, color="#BAC2DE")
    ax1.tick_params(colors="#A6ADC8", labelsize=10)
    ax1.grid(axis="y", linestyle="--", alpha=0.2, color="#6C7086")

    # Add data labels on top of bars
    for bar in bars1:
        height = bar.get_height()
        if height > 0:
            ax1.annotate(
                f"{int(height)}",
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 4),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
                color="#A6E3A1",
            )

    # --- Subplot 2: Per Habit Breakdown ---
    ax2.set_facecolor("#1E1E2E")
    y_pos = range(len(habit_names))
    colors = ["#A6E3A1", "#F38BA8", "#FAB387", "#F9E2AF", "#CBA6F7", "#94E2D5", "#89DCEB"]
    bar_colors = [colors[i % len(colors)] for i in range(len(habit_names))]

    bars2 = ax2.barh(y_pos, habit_minutes, color=bar_colors, height=0.55, edgecolor="#CDD6F4", linewidth=0.8)
    ax2.set_yticks(list(y_pos))
    ax2.set_yticklabels(habit_names, fontsize=10, color="#CDD6F4")
    ax2.set_title("Время по привычкам за неделю (мин)", fontsize=13, fontweight="bold", color="#CDD6F4", pad=12)
    ax2.set_xlabel("Суммарно минут", fontsize=11, color="#BAC2DE")
    ax2.tick_params(colors="#A6ADC8", labelsize=10)
    ax2.grid(axis="x", linestyle="--", alpha=0.2, color="#6C7086")
    ax2.invert_yaxis()  # top-down habit order

    # Add labels on horizontal bars
    for bar in bars2:
        width = bar.get_width()
        if width > 0:
            ax2.annotate(
                f"{int(width)} м",
                xy=(width, bar.get_y() + bar.get_height() / 2),
                xytext=(5, 0),
                textcoords="offset points",
                ha="left",
                va="center",
                fontsize=9,
                fontweight="bold",
                color="#F9E2AF",
            )

    plt.tight_layout(pad=2.5)

    buffer = io.BytesIO()
    plt.savefig(buffer, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return buffer
