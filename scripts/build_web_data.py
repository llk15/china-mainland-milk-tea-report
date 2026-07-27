#!/usr/bin/env python3
"""从公开 CSV 结果生成网页使用的精简 JSON。"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


NUMBER_FIELDS = {
    "energy": "energy_kcal_per_cup",
    "protein": "protein_g_per_cup",
    "transFat": "trans_fat_g_per_cup",
    "carbs": "carbohydrate_g_per_cup",
    "fat": "fat_g_per_cup",
    "caffeine": "caffeine_mg_per_cup",
    "polyphenols": "tea_polyphenols_mg_per_cup",
    "gi": "gi",
}
TEXT_FIELDS = {
    "category": "category_name",
    "name": "product_name",
    "cup": "cup_size",
    "temperature": "temperature",
    "sweetness": "sweetness",
    "topping": "topping",
    "extra": "extra",
    "tea": "tea_variant",
    "milk": "milk_variant",
    "grade": "nutrition_grade",
    "report": "test_report_url",
}


def number(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def build(source: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    products: set[str] = set()
    categories: set[str] = set()
    updated_at = ""

    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            record = {target: row.get(source_name, "").strip() for target, source_name in TEXT_FIELDS.items()}
            record.update({target: number(row.get(source_name, "")) for target, source_name in NUMBER_FIELDS.items()})
            records.append(record)
            products.add(record["name"])
            categories.add(record["category"])
            updated_at = max(updated_at, row.get("last_fetched_at", ""))

    return {
        "meta": {
            "brand": "霸王茶姬",
            "records": len(records),
            "products": len(products),
            "categories": len(categories),
            "updatedAt": updated_at,
        },
        "records": records,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "data/chagee/chagee_drinks.csv")
    parser.add_argument("--output", type=Path, default=root / "data/chagee/chagee_drinks.json")
    args = parser.parse_args()

    payload = build(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"已生成 {args.output}："
        f"{payload['meta']['records']} 条规格，{payload['meta']['products']} 款饮品"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
