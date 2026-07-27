#!/usr/bin/env python3
"""从 CHAGEE SQLite 数据库导出完整 CSV 和可筛选的营养 Excel。"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.sax.saxutils import escape


DETAIL_FIELDS = (
    ("cup_size", "productSpec"),
    ("temperature", "productTemperature"),
    ("sweetness", "productBrix"),
    ("topping", "productBubble"),
    ("extra", "productExtra"),
    ("tea_variant", "teaDregs"),
    ("milk_variant", "milkDregs"),
    ("energy_kcal_per_cup", "productQuantity"),
    ("protein_g_per_cup", "productProtein"),
    ("trans_fat_g_per_cup", "tfa"),
    ("carbohydrate_g_per_cup", "productCarbohydrate"),
    ("fat_g_per_cup", "productFat"),
    ("caffeine_mg_per_cup", "productCaffeine"),
    ("tea_polyphenols_mg_per_cup", "productTeaPolyphenol"),
    ("gi", "gi"),
    ("nutrition_grade", "greenGrade"),
    ("test_report_url", "productTestReport"),
)
OPTION_COLUMNS = (
    ("available_cup_sizes", "productSpec"),
    ("available_temperatures", "productTemperature"),
    ("available_sweetness", "productBrix"),
    ("available_toppings", "productBubble"),
    ("available_extras", "productExtra"),
    ("available_tea_variants", "teaDregs"),
    ("available_milk_variants", "milkDregs"),
)
CSV_COLUMNS = (
    "product_key", "product_id", "category_id", "category_code", "category_name",
    "product_name", "first_seen_at", "last_seen_at", "last_fetched_at", "is_current",
    "detail_kind", "selection_json", *[name for name, _ in DETAIL_FIELDS],
    *[name for name, _ in OPTION_COLUMNS], "listing_json", "detail_json",
)
EXCEL_COLUMNS = (
    ("品类", "category_name", "text"),
    ("产品名称", "product_name", "text"),
    ("杯型", "cup_size", "text"),
    ("冰度/温度", "temperature", "text"),
    ("糖分", "sweetness", "text"),
    ("加料", "topping", "text"),
    ("附加项", "extra", "text"),
    ("茶底", "tea_variant", "text"),
    ("奶底", "milk_variant", "text"),
    ("热量 (kcal/杯)", "energy_kcal_per_cup", "number"),
    ("蛋白质 (g/杯)", "protein_g_per_cup", "number"),
    ("反式脂肪酸 (g/杯)", "trans_fat_g_per_cup", "number"),
    ("碳水化合物 (g/杯)", "carbohydrate_g_per_cup", "number"),
    ("脂肪 (g/杯)", "fat_g_per_cup", "number"),
    ("咖啡因 (mg/杯)", "caffeine_mg_per_cup", "number"),
    ("茶多酚 (mg/杯)", "tea_polyphenols_mg_per_cup", "number"),
    ("GI", "gi", "number"),
    ("营养等级", "nutrition_grade", "text"),
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_rows(database: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        options: dict[str, dict[str, list[str]]] = {}
        for row in connection.execute(
            "SELECT product_key, option_field, option_value FROM product_options ORDER BY product_key, option_field, position"
        ):
            options.setdefault(row["product_key"], {}).setdefault(row["option_field"], []).append(row["option_value"])
        query = """
            SELECT p.*, d.detail_kind, d.selection_json, d.detail_json
            FROM products AS p
            LEFT JOIN product_details AS d USING (product_key)
            ORDER BY p.category_name, p.product_name, d.selection_key
        """
        rows: list[dict[str, Any]] = []
        for source in connection.execute(query):
            row = dict(source)
            detail = json.loads(row.pop("detail_json") or "{}")
            product_options = options.get(row["product_key"], {})
            for column, source_key in DETAIL_FIELDS:
                row[column] = detail.get(source_key, "")
            for column, source_key in OPTION_COLUMNS:
                row[column] = " | ".join(product_options.get(source_key, []))
            row["listing_json"] = row.get("listing_json", "")
            row["detail_json"] = json.dumps(detail, ensure_ascii=False, sort_keys=True)
            rows.append(row)
        return rows
    finally:
        connection.close()


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cell_xml(reference: str, value: Any, kind: str, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if kind == "number":
        number = number_or_none(value)
        if number is not None:
            return f'<c r="{reference}"{style_attr}><v>{number}</v></c>'
    text = escape(str(value if value is not None else ""))
    return f'<c r="{reference}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'


def worksheet_xml(rows: Sequence[Mapping[str, Any]]) -> str:
    xml_rows = []
    header_cells = [cell_xml(f"{column_name(i)}1", title, "text", 1) for i, (title, _, _) in enumerate(EXCEL_COLUMNS, 1)]
    xml_rows.append(f'<row r="1">{"".join(header_cells)}</row>')
    # 默认按热量升序，Excel 的各营养数值列仍可在筛选下拉菜单中重新排序。
    ordered = sorted(rows, key=lambda row: (number_or_none(row.get("energy_kcal_per_cup")) is None, number_or_none(row.get("energy_kcal_per_cup")) or 0, row["product_name"]))
    for row_number, row in enumerate(ordered, 2):
        cells = [
            cell_xml(f"{column_name(i)}{row_number}", row.get(key, ""), kind, 2 if kind == "number" else 0)
            for i, (_, key, kind) in enumerate(EXCEL_COLUMNS, 1)
        ]
        xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    last_cell = f"{column_name(len(EXCEL_COLUMNS))}{max(1, len(rows) + 1)}"
    widths = "".join(
        f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
        for i, width in enumerate((15, 18, 10, 12, 14, 12, 12, 12, 12, 16, 16, 19, 18, 14, 17, 17, 10, 12), 1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <cols>{widths}</cols>
  <sheetData>{"".join(xml_rows)}</sheetData>
  <autoFilter ref="A1:{last_cell}"/>
</worksheet>'''


def write_xlsx(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    files = {
        "[Content_Types].xml": '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>''',
        "_rels/.rels": '''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''',
        "xl/workbook.xml": '''<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="营养数据" sheetId="1" r:id="rId1"/></sheets></workbook>''',
        "xl/_rels/workbook.xml.rels": '''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''',
        "xl/styles.xml": '''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="0.00"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="3"><xf xfId="0"/><xf fontId="1" fillId="1" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" xfId="0" applyNumberFormat="1"/></cellXfs></styleSheet>''',
        "xl/worksheets/sheet1.xml": worksheet_xml(rows),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode("utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data/chagee/chagee_drinks.sqlite3")
    parser.add_argument("--csv", type=Path, default=PROJECT_ROOT / "data/chagee/chagee_drinks.csv")
    parser.add_argument("--excel", type=Path, default=PROJECT_ROOT / "data/chagee/chagee_nutrition.xlsx")
    args = parser.parse_args(argv)
    if not args.database.exists():
        parser.error(f"数据库不存在：{args.database}")
    rows = load_rows(args.database)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.excel.parent.mkdir(parents=True, exist_ok=True)
    write_csv(args.csv, rows)
    write_xlsx(args.excel, rows)
    print(f"已导出 {len(rows)} 条详情：{args.csv}；{args.excel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
