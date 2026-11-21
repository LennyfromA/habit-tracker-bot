"""Repository for querying the local food catalog and calculating portions."""

import json
import re
from typing import Any, Dict, List, Optional

from bot.database.db import get_db
from bot.database.food_catalog_data import FOOD_CATALOG


async def seed_food_catalog_if_empty(db_path: Optional[str] = None) -> None:
    """Populate food_database table from catalog data if currently empty."""
    async with get_db(db_path) as db:
        async with db.execute("SELECT COUNT(id) AS cnt FROM food_database;") as cursor:
            row = await cursor.fetchone()
            if row and row["cnt"] > 0:
                return

        insert_sql = """
        INSERT INTO food_database (name, category, aliases, calories_100g, protein_100g, fat_100g, carbs_100g, portions_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """
        rows_to_insert = [
            (
                item["name"],
                item.get("category", "Разное"),
                item.get("aliases", item["name"].lower()),
                float(item["calories_100g"]),
                float(item.get("protein_100g", 0.0)),
                float(item.get("fat_100g", 0.0)),
                float(item.get("carbs_100g", 0.0)),
                json.dumps(item.get("portions", []), ensure_ascii=False),
            )
            for item in FOOD_CATALOG
        ]
        await db.executemany(insert_sql, rows_to_insert)
        await db.commit()


async def search_food(query: str, limit: int = 5, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Search food products by matching against name or aliases.
    Fully case-insensitive for Cyrillic and Latin using Python .casefold().
    Ranks exact matches, prefix matches, substring matches, and multi-word fallbacks.
    """
    clean_q = query.strip().casefold()
    if not clean_q or len(clean_q) < 2:
        return []

    q_words = [w for w in clean_q.split() if len(w) >= 1]

    async with get_db(db_path) as db:
        sql = "SELECT * FROM food_database ORDER BY id ASC;"
        async with db.execute(sql) as cursor:
            rows = await cursor.fetchall()

    scored_items = []
    for r in rows:
        item = dict(r)
        try:
            item["portions"] = json.loads(item["portions_json"])
        except Exception:
            item["portions"] = []

        name_cf = item["name"].casefold()
        aliases_raw = item.get("aliases", "") or ""
        aliases_cf = aliases_raw.casefold()
        aliases_list = [a.strip() for a in aliases_cf.split(",") if a.strip()]

        # Ranking tiers:
        # 1: Exact match with name or any alias
        if clean_q == name_cf or clean_q in aliases_list:
            rank = 1
            word_score = len(q_words)
        # 2: Name or alias starts with query
        elif name_cf.startswith(clean_q) or any(a.startswith(clean_q) for a in aliases_list):
            rank = 2
            word_score = len(q_words)
        # 3: Query is a substring of product name
        elif clean_q in name_cf:
            rank = 3
            word_score = len(q_words)
        # 4: Query is a substring of aliases
        elif clean_q in aliases_cf:
            rank = 4
            word_score = len(q_words)
        # 5: All words from query appear in name or aliases
        elif q_words and all(w in name_cf or w in aliases_cf for w in q_words):
            rank = 5
            word_score = len(q_words)
        else:
            # 6: Fallback - count matching words
            matching = [w for w in q_words if (len(w) >= 2 and (w in name_cf or w in aliases_cf))]
            if matching and len(matching) >= max(1, len(q_words) // 2):
                rank = 6
                word_score = len(matching)
            else:
                continue

        scored_items.append((rank, -word_score, len(item["name"]), item["id"], item))

    scored_items.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return [x[4] for x in scored_items[:limit]]


async def get_food_by_id(food_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve food product by primary key ID."""
    async with get_db(db_path) as db:
        query = "SELECT * FROM food_database WHERE id = ?;"
        async with db.execute(query, (food_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            try:
                item["portions"] = json.loads(item["portions_json"])
            except Exception:
                item["portions"] = []
            return item


def calculate_portion_macros(food: Dict[str, Any], grams: float) -> Dict[str, Any]:
    """Calculate exact calories and BJU for given grams of product."""
    cals = round(grams * float(food["calories_100g"]) / 100.0)
    prot = round(grams * float(food.get("protein_100g", 0.0)) / 100.0, 1)
    fat = round(grams * float(food.get("fat_100g", 0.0)) / 100.0, 1)
    carbs = round(grams * float(food.get("carbs_100g", 0.0)) / 100.0, 1)
    return {
        "calories": max(0, cals),
        "protein": max(0.0, prot),
        "fat": max(0.0, fat),
        "carbs": max(0.0, carbs),
        "grams": grams,
    }
