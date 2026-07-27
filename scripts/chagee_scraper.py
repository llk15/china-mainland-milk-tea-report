#!/usr/bin/env python3
"""将霸王茶姬健康计算器数据持续保存到 SQLite。

每完成一个产品列表项、规格项或营养组合，就会提交一个 SQLite 事务；因而
网络中断或 Ctrl-C 后直接以相同命令重跑即可继续，不会丢失已完成的数据。
"""

from __future__ import annotations

import argparse
from http.client import HTTPException
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

SOURCE_URL = "https://qmproductcomput.bwcj.com/"
API_BASE_URL = f"{SOURCE_URL.rstrip('/')}/api"
OPTION_FIELDS = (
    "productSpec", "productTemperature", "productBrix", "productBubble",
    "productExtra", "teaDregs", "milkDregs",
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ChageeAPIError(RuntimeError):
    """官方接口请求或响应异常。"""


class ChageeClient:
    """带限速和重试的官方健康计算器 API 客户端。"""

    def __init__(self, *, timeout: float, retries: int, delay: float) -> None:
        self.timeout, self.retries, self.delay = timeout, retries, delay
        self._last_request_at = 0.0

    def post(self, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        authorization = os.environ.get("CHAGEE_AUTHORIZATION")
        if not authorization:
            raise ChageeAPIError(
                "缺少 CHAGEE_AUTHORIZATION 环境变量；请从公开健康计算器的网络请求中获取当前 Authorization 请求头。"
            )
        request = Request(
            f"{API_BASE_URL}/{path.lstrip('/')}",
            data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/json", "Authorization": authorization,
                "Content-Type": "application/json", "Origin": SOURCE_URL.rstrip("/"),
                "Referer": SOURCE_URL, "User-Agent": "chagee-sqlite-scraper/1.0",
            },
        )
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self.delay - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    envelope = json.loads(response.read().decode("utf-8"))
                self._last_request_at = time.monotonic()
                if not isinstance(envelope, dict) or str(envelope.get("code")) != "1000":
                    raise ChageeAPIError(f"接口业务错误：{envelope!r}")
                return envelope.get("data")
            except (HTTPError, URLError, HTTPException, TimeoutError, json.JSONDecodeError, ChageeAPIError) as exc:
                self._last_request_at = time.monotonic()
                last_error = exc
                retryable = not isinstance(exc, HTTPError) or exc.code == 429 or exc.code >= 500
                if attempt >= self.retries or not retryable:
                    break
                time.sleep(min(2**attempt, 8))
        raise ChageeAPIError(f"请求失败：{request.full_url}；{last_error}") from last_error

    def list_categories(self) -> list[dict[str, Any]]:
        data = self.post("product/listTypeAll")
        if not isinstance(data, list):
            raise ChageeAPIError("品类接口 data 字段不是列表")
        return data

    def list_products(self, category_name: str, page_size: int = 100) -> list[dict[str, Any]]:
        products: dict[tuple[Any, ...], dict[str, Any]] = {}
        current = 1
        while True:
            data = self.post("product/productTypeList", {"productType": category_name, "productName": "", "current": current, "size": page_size})
            if not isinstance(data, dict) or not isinstance(data.get("records"), list):
                raise ChageeAPIError(f"饮品列表接口返回异常：{category_name}")
            for product in data["records"]:
                key = ("id", product["id"]) if product.get("id") is not None else ("name", product.get("productName"))
                products[key] = product
            if current >= int(data.get("pages") or 1):
                return list(products.values())
            current += 1

    def get_options(self, product_name: str) -> dict[str, list[str]]:
        data = self.post(f"product/space?productName={quote(product_name)}")
        if not isinstance(data, dict):
            raise ChageeAPIError(f"规格接口返回异常：{product_name}")
        return {field: split_options(data.get(field)) for field in OPTION_FIELDS}

    def get_detail(self, product_name: str, selection: Mapping[str, str] | None = None) -> dict[str, Any]:
        payload: dict[str, str] = {"productName": product_name}
        if selection:
            payload.update(selection)
        data = self.post("product/newProductDetail", payload)
        if not isinstance(data, dict):
            raise ChageeAPIError(f"营养详情接口返回异常：{product_name}")
        return data


def split_options(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def option_combinations(options: Mapping[str, Sequence[str]]):
    active = [(field, list(options.get(field, []))) for field in OPTION_FIELDS]
    active = [(field, values) for field, values in active if values]
    if not active:
        yield {}
        return
    fields = [field for field, _ in active]
    import itertools
    for values in itertools.product(*(values for _, values in active)):
        yield dict(zip(fields, values))


def matches_filter(value: str, filters: Sequence[str]) -> bool:
    return not filters or any(term.casefold() in value.casefold() for term in filters)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  detail_mode TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  product_key TEXT PRIMARY KEY,
  product_id TEXT,
  category_id TEXT,
  category_code TEXT,
  category_name TEXT NOT NULL,
  product_name TEXT NOT NULL,
  listing_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_fetched_at TEXT,
  is_current INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS products_name_idx ON products(product_name);
CREATE TABLE IF NOT EXISTS product_options (
  product_key TEXT NOT NULL REFERENCES products(product_key) ON DELETE CASCADE,
  option_field TEXT NOT NULL,
  option_value TEXT NOT NULL,
  position INTEGER NOT NULL,
  PRIMARY KEY (product_key, option_field, option_value)
);
CREATE TABLE IF NOT EXISTS product_details (
  product_key TEXT NOT NULL REFERENCES products(product_key) ON DELETE CASCADE,
  selection_key TEXT NOT NULL,
  detail_kind TEXT NOT NULL,
  selection_json TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (product_key, selection_key)
);
CREATE TABLE IF NOT EXISTS fetch_states (
  product_key TEXT NOT NULL REFERENCES products(product_key) ON DELETE CASCADE,
  detail_mode TEXT NOT NULL,
  status TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (product_key, detail_mode)
);
CREATE TABLE IF NOT EXISTS fetch_errors (
  error_id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(run_id),
  product_key TEXT,
  product_name TEXT,
  stage TEXT NOT NULL,
  selection_json TEXT,
  error TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def product_key(listing: Mapping[str, Any], category: Mapping[str, Any]) -> str:
    if listing.get("id") is not None:
        return f"id:{listing['id']}"
    return f"name:{category.get('typeCode', category.get('typeName', ''))}:{listing.get('productName', '')}"


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.executescript(SCHEMA)
    return connection


def save_product(
    connection: sqlite3.Connection, key: str, category: Mapping[str, Any], listing: Mapping[str, Any], timestamp: str
) -> None:
    connection.execute(
        """INSERT INTO products (
               product_key, product_id, category_id, category_code, category_name,
               product_name, listing_json, first_seen_at, last_seen_at, is_current)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
             ON CONFLICT(product_key) DO UPDATE SET
               product_id=excluded.product_id, category_id=excluded.category_id,
               category_code=excluded.category_code, category_name=excluded.category_name,
               product_name=excluded.product_name, listing_json=excluded.listing_json,
               last_seen_at=excluded.last_seen_at, is_current=1""",
        (key, listing.get("id"), category.get("id"), category.get("typeCode"), category.get("typeName"),
         listing.get("productName", ""), stable_json(listing), timestamp, timestamp),
    )
    connection.commit()


def save_options(connection: sqlite3.Connection, key: str, options: Mapping[str, Sequence[str]]) -> None:
    with connection:
        connection.execute("DELETE FROM product_options WHERE product_key = ?", (key,))
        for field in OPTION_FIELDS:
            for position, value in enumerate(options.get(field, [])):
                connection.execute(
                    "INSERT INTO product_options VALUES (?, ?, ?, ?)", (key, field, value, position)
                )


def save_detail(
    connection: sqlite3.Connection,
    key: str,
    detail_kind: str,
    selection: Mapping[str, str],
    detail: Mapping[str, Any],
    *,
    fetched_at: str | None = None,
) -> None:
    selection_json = stable_json(selection)
    with connection:
        connection.execute(
            """INSERT INTO product_details VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_key, selection_key) DO UPDATE SET
                 detail_kind=excluded.detail_kind, detail_json=excluded.detail_json, fetched_at=excluded.fetched_at""",
            (key, selection_json, detail_kind, selection_json, stable_json(detail), fetched_at or now()),
        )


def set_state(connection: sqlite3.Connection, key: str, detail_mode: str, status: str) -> None:
    with connection:
        connection.execute(
            """INSERT INTO fetch_states VALUES (?, ?, ?, ?)
               ON CONFLICT(product_key, detail_mode) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at""",
            (key, detail_mode, status, now()),
        )


def is_complete(connection: sqlite3.Connection, key: str, detail_mode: str) -> bool:
    row = connection.execute(
        "SELECT status FROM fetch_states WHERE product_key = ? AND detail_mode = ?", (key, detail_mode)
    ).fetchone()
    return row is not None and row[0] == "complete"


def has_detail(connection: sqlite3.Connection, key: str, selection: Mapping[str, str]) -> bool:
    return connection.execute(
        "SELECT 1 FROM product_details WHERE product_key = ? AND selection_key = ?",
        (key, stable_json(selection)),
    ).fetchone() is not None


def record_error(
    connection: sqlite3.Connection, run_id: int, key: str, product_name: str, stage: str, error: Exception,
    selection: Mapping[str, str] | None = None,
) -> None:
    with connection:
        connection.execute(
            "INSERT INTO fetch_errors (run_id, product_key, product_name, stage, selection_json, error, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, key, product_name, stage, stable_json(selection) if selection else None, str(error), now()),
        )


def selection_from_detail(
    detail: Mapping[str, Any], options: Mapping[str, Sequence[str]] | None = None
) -> dict[str, str]:
    """由详情字段还原实际组合键；只保留该产品启用且匹配的规格。"""
    return {
        field: str(detail[field])
        for field in OPTION_FIELDS
        if detail.get(field) not in (None, "")
        and (options is None or (detail.get(field) in options.get(field, [])))
    }


def options_for_product(connection: sqlite3.Connection, key: str) -> dict[str, list[str]]:
    values = {field: [] for field in OPTION_FIELDS}
    for field, value in connection.execute(
        "SELECT option_field, option_value FROM product_options WHERE product_key = ? ORDER BY option_field, position",
        (key,),
    ):
        values[field].append(value)
    return values


def details_match(left: str, right: str) -> bool:
    """比较完整官方详情，而非只比较营养字段，避免误删不同配方。"""
    return json.loads(left) == json.loads(right)


def save_default_detail(
    connection: sqlite3.Connection, key: str, options: Mapping[str, Sequence[str]], detail: Mapping[str, Any]
) -> bool:
    """默认请求也按实际组合保存；返回 False 表示已有同键但内容不一致。"""
    selection = selection_from_detail(detail, options)
    existing = connection.execute(
        "SELECT detail_json FROM product_details WHERE product_key = ? AND selection_key = ?",
        (key, stable_json(selection)),
    ).fetchone()
    if existing is not None:
        return details_match(existing[0], stable_json(detail))
    save_detail(connection, key, "combination", selection, detail)
    return True


def migrate_default_details(connection: sqlite3.Connection) -> tuple[int, int, int]:
    """将旧的 ``default/{}`` 记录转换成组合记录。

    若已存在同一组合，必须完整详情一致才删除默认记录；不一致时保留它，
    以免不同接口返回被静默覆盖。
    """
    defaults = connection.execute(
        "SELECT product_key, selection_key, detail_json FROM product_details WHERE detail_kind = 'default'"
    ).fetchall()
    converted = removed = conflicts = 0
    for key, old_selection_key, detail_json in defaults:
        detail = json.loads(detail_json)
        selection = selection_from_detail(detail, options_for_product(connection, key))
        selection_key = stable_json(selection)
        existing = connection.execute(
            """SELECT detail_json FROM product_details
               WHERE product_key = ? AND selection_key = ? AND selection_key <> ?""",
            (key, selection_key, old_selection_key),
        ).fetchone()
        with connection:
            if existing is not None:
                if details_match(detail_json, existing[0]):
                    connection.execute(
                        "DELETE FROM product_details WHERE product_key = ? AND selection_key = ?",
                        (key, old_selection_key),
                    )
                    removed += 1
                else:
                    conflicts += 1
                continue
            connection.execute(
                """UPDATE product_details
                   SET selection_key = ?, selection_json = ?, detail_kind = 'combination'
                   WHERE product_key = ? AND selection_key = ?""",
                (selection_key, selection_key, key, old_selection_key),
            )
            converted += 1
    return converted, removed, conflicts


def import_json(connection: sqlite3.Connection, path: Path) -> tuple[int, int]:
    """导入原 JSON 输出；不联网，也不会删除 SQLite 中已有记录。"""
    with path.open(encoding="utf-8") as handle:
        dataset = json.load(handle)
    if not isinstance(dataset, Mapping) or not isinstance(dataset.get("products"), list):
        raise ValueError("JSON 格式不正确：缺少 products 列表")

    source = dataset.get("source", {})
    detail_mode = str(source.get("detail_mode", "default")) if isinstance(source, Mapping) else "default"
    errored_names = {
        str(error.get("product_name"))
        for error in dataset.get("errors", [])
        if isinstance(error, Mapping) and error.get("product_name")
    }
    imported = 0
    details = 0
    for item in dataset["products"]:
        if not isinstance(item, Mapping):
            continue
        listing = item.get("listing")
        original_category = item.get("category")
        if not isinstance(listing, Mapping) or not isinstance(original_category, Mapping):
            continue
        category = {
            "id": original_category.get("id"),
            "typeCode": original_category.get("code"),
            "typeName": original_category.get("name", ""),
        }
        key = product_key(listing, category)
        meta = item.get("record_meta", {})
        if not isinstance(meta, Mapping):
            meta = {}
        seen_at = str(meta.get("last_seen_at") or source.get("fetched_at") or now())
        save_product(connection, key, category, listing, seen_at)
        with connection:
            connection.execute(
                """UPDATE products SET first_seen_at = ?, last_seen_at = ?, last_fetched_at = ?, is_current = ?
                   WHERE product_key = ?""",
                (
                    str(meta.get("first_fetched_at") or seen_at),
                    seen_at,
                    meta.get("last_fetched_at"),
                    int(bool(meta.get("is_current", True))),
                    key,
                ),
            )
        options = item.get("options", {})
        if isinstance(options, Mapping):
            save_options(connection, key, options)
        default_detail = item.get("default_detail")
        detail_fetched_at = str(meta.get("last_fetched_at") or seen_at)
        if isinstance(default_detail, Mapping):
            save_detail(connection, key, "default", {}, default_detail, fetched_at=detail_fetched_at)
            details += 1
        combinations = item.get("combination_details", [])
        if isinstance(combinations, list):
            for detail in combinations:
                if isinstance(detail, Mapping):
                    save_detail(
                        connection, key, "combination", selection_from_detail(detail, options), detail,
                        fetched_at=detail_fetched_at,
                    )
                    details += 1
        if str(listing.get("productName", "")) not in errored_names:
            set_state(connection, key, detail_mode, "complete")
        else:
            set_state(connection, key, detail_mode, "partial")
        imported += 1
    return imported, details


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path,
        default=PROJECT_ROOT / "data/chagee/chagee_drinks.sqlite3",
    )
    parser.add_argument("--import-json", type=Path, help="导入既有 JSON 输出后退出（不联网）")
    parser.add_argument("--migrate-defaults", action="store_true", help="将旧 default 详情迁移为实际规格组合后退出")
    parser.add_argument("--detail-mode", choices=("none", "default", "all"), default="default")
    parser.add_argument("--force-refresh", action="store_true", help="重新请求已完成产品的规格和详情")
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--product", action="append", default=[])
    parser.add_argument("--max-products", type=int)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args(argv)
    if args.delay < 0 or args.timeout <= 0 or args.retries < 0 or (args.max_products is not None and args.max_products <= 0):
        parser.error("--delay >= 0；--timeout > 0；--retries >= 0；--max-products > 0")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    connection = open_database(args.database)
    timestamp = now()
    with connection:
        cursor = connection.execute("INSERT INTO runs (started_at, detail_mode, status) VALUES (?, ?, 'running')", (timestamp, args.detail_mode))
    run_id = int(cursor.lastrowid)
    if args.import_json is not None:
        try:
            imported, details = import_json(connection, args.import_json)
            with connection:
                connection.execute("UPDATE runs SET finished_at = ?, status = 'complete' WHERE run_id = ?", (now(), run_id))
            print(f"已导入：{imported} 款饮品，{details} 条详情；数据库：{args.database}")
            return 0
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            with connection:
                connection.execute("UPDATE runs SET finished_at = ?, status = 'failed' WHERE run_id = ?", (now(), run_id))
            print(f"导入失败：{exc}", file=sys.stderr)
            return 1
        finally:
            connection.close()
    if args.migrate_defaults:
        try:
            converted, removed, conflicts = migrate_default_details(connection)
            with connection:
                connection.execute("UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?", (now(), "complete" if not conflicts else "partial", run_id))
            print(f"默认详情迁移完成：转换 {converted} 条，去重删除 {removed} 条，内容不一致保留 {conflicts} 条。")
            return 0 if not conflicts else 2
        finally:
            connection.close()
    client = ChageeClient(timeout=args.timeout, retries=args.retries, delay=args.delay)
    errors = 0
    seen_keys: set[str] = set()
    count = 0
    full_menu = not args.category and not args.product and args.max_products is None
    try:
        for category in client.list_categories():
            category_name = str(category.get("typeName", ""))
            if not matches_filter(category_name, args.category):
                continue
            print(f"[品类] {category_name}", file=sys.stderr)
            for listing in client.list_products(category_name):
                name = str(listing.get("productName", ""))
                if not matches_filter(name, args.product):
                    continue
                if args.max_products is not None and count >= args.max_products:
                    break
                key = product_key(listing, category)
                seen_keys.add(key)
                save_product(connection, key, category, listing, timestamp)
                count += 1
                if not args.force_refresh and is_complete(connection, key, args.detail_mode):
                    print(f"  [缓存] {name}", file=sys.stderr)
                    continue
                print(f"  [抓取] {name}", file=sys.stderr)
                set_state(connection, key, args.detail_mode, "in_progress")
                try:
                    options = client.get_options(name)
                    save_options(connection, key, options)
                    if args.detail_mode == "default":
                        if not save_default_detail(connection, key, options, client.get_detail(name)):
                            raise ChageeAPIError("默认详情与同规格组合内容不一致，已保留原有组合")
                    elif args.detail_mode == "all":
                        failed = False
                        for selection in option_combinations(options):
                            if not args.force_refresh and has_detail(connection, key, selection):
                                continue
                            try:
                                save_detail(connection, key, "combination", selection, client.get_detail(name, selection))
                            except ChageeAPIError as exc:
                                failed = True
                                errors += 1
                                record_error(connection, run_id, key, name, "combination_detail", exc, selection)
                                print(f"    [跳过组合] {selection}：{exc}", file=sys.stderr)
                        if failed:
                            set_state(connection, key, args.detail_mode, "partial")
                            continue
                    set_state(connection, key, args.detail_mode, "complete")
                    with connection:
                        connection.execute("UPDATE products SET last_fetched_at = ? WHERE product_key = ?", (now(), key))
                except ChageeAPIError as exc:
                    errors += 1
                    record_error(connection, run_id, key, name, "product", exc)
                    set_state(connection, key, args.detail_mode, "partial")
                    print(f"    [警告] {exc}", file=sys.stderr)
            if args.max_products is not None and count >= args.max_products:
                break
        if full_menu:
            with connection:
                connection.execute("UPDATE products SET is_current = 0 WHERE last_seen_at <> ?", (timestamp,))
        status = "complete" if errors == 0 else "partial"
        with connection:
            connection.execute("UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?", (now(), status, run_id))
        print(f"完成：{count} 款饮品；{errors} 个错误；数据库：{args.database}")
        return 0 if errors == 0 else 2
    except (KeyboardInterrupt, Exception) as exc:
        with connection:
            connection.execute("UPDATE runs SET finished_at = ?, status = 'interrupted' WHERE run_id = ?", (now(), run_id))
        if isinstance(exc, KeyboardInterrupt):
            print("已停止；已提交的数据可在下次运行时继续使用。", file=sys.stderr)
            return 130
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
