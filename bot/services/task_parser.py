"""Quick task input string parser for Todoist-style task creation."""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


def parse_quick_task(raw: str, local_date_str: str) -> Optional[Dict[str, Any]]:
    """
    Parse a quick task string into structured task attributes.
    
    Extracts:
    - #list_name
    - due_date ('сегодня', 'завтра', 'послезавтра', 'DD.MM', 'DD.MM.YYYY', 'YYYY-MM-DD')
    - due_time ('HH:MM')
    - estimated_minutes ('25m', '45мин', '1h', '1.5ч')
    - recurrence ('каждый день'/'ежедневно', 'по будням'/'будни', 'каждую неделю'/'еженедельно')
    
    Returns a dictionary or None if resulting title is empty.
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()
    base_date = datetime.strptime(local_date_str, "%Y-%m-%d").date()

    list_name: Optional[str] = None
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    estimated_minutes: Optional[int] = None
    recurrence: str = "none"

    # 1. Recurrence
    rec_patterns = [
        (r"(?i)\bкаждый\s+день\b|\bежедневно\b", "daily"),
        (r"(?i)\bпо\s+будням\b|\bбудни\b", "weekdays"),
        (r"(?i)\bкаждую\s+неделю\b|\bеженедельно\b", "weekly"),
    ]
    for pat, rec_val in rec_patterns:
        match = re.search(pat, text)
        if match:
            recurrence = rec_val
            text = text[:match.start()] + " " + text[match.end():]
            break

    # 2. List Name (#list_name)
    list_match = re.search(r"#([a-zA-Zа-яА-ЯёЁ0-9_-]+)", text)
    if list_match:
        list_name = list_match.group(1).strip()
        text = text[:list_match.start()] + " " + text[list_match.end():]

    # 3. Relative or absolute date
    # Relative keywords
    if re.search(r"(?i)\bпослезавтра\b", text):
        due_date = (base_date + timedelta(days=2)).strftime("%Y-%m-%d")
        text = re.sub(r"(?i)\bпослезавтра\b", " ", text)
    elif re.search(r"(?i)\bзавтра\b", text):
        due_date = (base_date + timedelta(days=1)).strftime("%Y-%m-%d")
        text = re.sub(r"(?i)\bзавтра\b", " ", text)
    elif re.search(r"(?i)\bсегодня\b", text):
        due_date = base_date.strftime("%Y-%m-%d")
        text = re.sub(r"(?i)\bсегодня\b", " ", text)
    else:
        # Absolute dates: YYYY-MM-DD or DD.MM.YYYY or DD.MM
        iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
        if iso_match:
            try:
                d = datetime.strptime(iso_match.group(0), "%Y-%m-%d").date()
                due_date = d.strftime("%Y-%m-%d")
                text = text[:iso_match.start()] + " " + text[iso_match.end():]
            except ValueError:
                pass

        if not due_date:
            dmy_match = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b", text)
            if dmy_match:
                day = int(dmy_match.group(1))
                month = int(dmy_match.group(2))
                raw_year = dmy_match.group(3)
                if raw_year:
                    year = int(raw_year) if len(raw_year) == 4 else 2000 + int(raw_year)
                else:
                    year = base_date.year
                    # If date already passed in current year, assign current or next year
                    try:
                        candidate = datetime(year, month, day).date()
                        if candidate < base_date:
                            year += 1
                    except ValueError:
                        pass
                try:
                    due_date = datetime(year, month, day).strftime("%Y-%m-%d")
                    text = text[:dmy_match.start()] + " " + text[dmy_match.end():]
                except ValueError:
                    pass

    # 4. Time: HH:MM
    time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if time_match:
        hh = int(time_match.group(1))
        mm = int(time_match.group(2))
        due_time = f"{hh:02d}:{mm:02d}"
        text = text[:time_match.start()] + " " + text[time_match.end():]

    # 5. Estimated Duration: e.g. 25m, 45мин, 1h, 1.5ч
    dur_match = re.search(
        r"(?i)\b(\d+(?:\.\d+)?)\s*(m|min|мин|минут(?:ы|а)?|h|ч|час(?:а|ов)?)\b",
        text,
    )
    if dur_match:
        val = float(dur_match.group(1))
        unit = dur_match.group(2).lower()
        if unit in ("h", "ч") or unit.startswith("час"):
            estimated_minutes = max(1, int(val * 60))
        else:
            estimated_minutes = max(1, int(val))
        text = text[:dur_match.start()] + " " + text[dur_match.end():]

    # Clean title
    title = re.sub(r"\s+", " ", text).strip(" ,.-;")
    if not title:
        return None

    # If recurring task had no date specified, default to today
    if recurrence != "none" and not due_date:
        due_date = base_date.strftime("%Y-%m-%d")

    return {
        "title": title,
        "list_name": list_name,
        "due_date": due_date,
        "due_time": due_time,
        "estimated_minutes": estimated_minutes,
        "recurrence": recurrence,
    }
