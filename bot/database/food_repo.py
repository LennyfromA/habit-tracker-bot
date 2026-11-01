"""Repository for querying the local food catalog and calculating portions."""

import json
import re
from typing import Any, Dict, List, Optional

from bot.database.db import get_db
from bot.database.food_catalog_data import FOOD_CATALOG


async def seed_food_catalog_if_empty() -> None:
    """Populate food_database table from catalog data if currently empty."""
    async with get_db() as db:
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


async def search_food(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search food products by matching against name or aliases.
    Matches full phrase or individual words.
    """
    clean_q = query.strip().lower()
    if not clean_q or len(clean_q) < 2:
        return []

    words = [re.escape(w) for w in clean_q.split() if len(w) >= 2]
    if not words:
        words = [re.escape(clean_q)]

    async with get_db() as db:
        # Match either exact substring or all words
        pattern = f"%{clean_q}%"
        sql = """
        SELECT * FROM food_database
        WHERE LOWER(name) LIKE ? OR LOWER(aliases) LIKE ?
        ORDER BY
            CASE
                WHEN LOWER(name) = ? THEN 1
                WHEN LOWER(name) LIKE (? || '%') THEN 2
                ELSE 3
            END,
            id ASC
        LIMIT ?;
        """
        async with db.execute(sql, (pattern, pattern, clean_q, clean_q, limit)) as cursor:
            rows = await cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                try:
                    item["portions"] = json.loads(item["portions_json"])
                except Exception:
                    item["portions"] = []
                results.append(item)

            if results:
                return results

            # Fallback: search by primary word if multi-word query didn't match
            if len(words) > 1:
                first_word_pattern = f"%{words[0]}%"
                async with db.execute(sql, (first_word_pattern, first_word_pattern, words[0], words[0], limit)) as cursor2:
                    rows2 = await cursor2.fetchall()
                    for r in rows2:
                        item = dict(r)
                        try:
                            item["portions"] = json.loads(item["portions_json"])
                        except Exception:
                            item["portions"] = []
                        results.append(item)

            return results


async def get_food_by_id(food_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve food product by primary key ID."""
    async with get_db() as db:
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
