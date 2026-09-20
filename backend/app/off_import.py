"""Open Food Facts mirror (spec 7): stream the CSV export, keep European
products that pass the macro-consistency check, upsert into ``foods`` with
``source='off'``. Barcode lookups then resolve locally and offline.

    python -m app.off_import                    # full run from the OFF export URL
    python -m app.off_import --file x.csv.gz    # from a local file
    python -m app.off_import --limit 5000       # stop after N accepted rows (testing)

Runs monthly from the scheduler; the first run takes a while on a Pi
(the export is ~1 GB compressed) — it commits every BATCH rows, so the API
keeps serving and a restart mid-way loses nothing already committed.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import logging
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from . import db, store
from .engine.validation import macros_consistent

log = logging.getLogger("health.off")

EXPORT_URL = "https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz"
DEFAULT_COUNTRIES = ("en:spain", "en:netherlands", "en:germany", "en:czech-republic", "en:czechia")
BATCH = 2000
SETTING_LAST_IMPORT = "off.last_import"
SETTING_LAST_COUNT = "off.last_count"

FIELDS = {
    "code": ("code",),
    "name": ("product_name", "product_name_en", "abbreviated_product_name", "generic_name"),
    "brand": ("brands",),
    "countries": ("countries_tags", "countries_en"),
    "kcal": ("energy-kcal_100g",),
    "kj": ("energy_100g", "energy-kj_100g"),
    "protein": ("proteins_100g",),
    "carbs": ("carbohydrates_100g",),
    "fat": ("fat_100g",),
    "fibre": ("fiber_100g",),
}


def _num(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def open_source(file: str | None, url: str = EXPORT_URL, timeout: int = 60) -> io.TextIOBase:
    if file:
        raw: io.BufferedIOBase = open(file, "rb")  # noqa: SIM115
        stream = gzip.GzipFile(fileobj=raw) if file.endswith(".gz") else raw
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "health-platform-pi/0.1 (single user, monthly mirror)"})
        raw = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        stream = gzip.GzipFile(fileobj=raw)
    return io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")


def parse_rows(text: io.TextIOBase, countries: Iterable[str] = DEFAULT_COUNTRIES) -> Iterator[dict]:
    """Yield accepted products as dicts ready for ``store.insert_food``."""
    wanted = tuple(c.lower() for c in countries)
    reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
    header = next(reader)
    idx: dict[str, int] = {}
    for key, names in FIELDS.items():
        for n in names:
            if n in header:
                idx[key] = header.index(n)
                break
    for key in ("code", "name", "countries", "protein", "carbs", "fat"):
        if key in FIELDS and key not in idx:
            raise ValueError(f"OFF export is missing a column for {key}")

    def get(row: list[str], key: str) -> str:
        i = idx.get(key)
        return row[i] if i is not None and i < len(row) else ""

    for row in reader:
        countries_field = get(row, "countries").lower()
        if not any(c in countries_field for c in wanted):
            continue
        code = get(row, "code").strip()
        name = get(row, "name").strip()
        if not code or not name or not code.isdigit() or len(code) < 6:
            continue
        kcal = _num(get(row, "kcal"))
        if kcal is None:
            kj = _num(get(row, "kj"))
            kcal = kj / 4.184 if kj is not None else None
        p, c, f = _num(get(row, "protein")), _num(get(row, "carbs")), _num(get(row, "fat"))
        if kcal is None or p is None or c is None or f is None:
            continue
        if not (0 <= kcal <= 900 and 0 <= p <= 100 and 0 <= c <= 100 and 0 <= f <= 100):
            continue
        if not macros_consistent(kcal, p, c, f):
            continue
        fibre = _num(get(row, "fibre")) or 0.0
        brand = get(row, "brand").strip() or None
        yield {
            "barcode": code, "name": name[:120], "brand": brand[:80] if brand else None,
            "kcal_100g": round(kcal, 1), "protein_100g": round(p, 1), "carbs_100g": round(c, 1), "fat_100g": round(f, 1),
            "fibre_100g": round(max(0.0, min(fibre, 100.0)), 1),
        }


def upsert_batch(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO foods (barcode, name, brand, kcal_100g, protein_100g, carbs_100g, fat_100g, fibre_100g, source, verified)
        VALUES (:barcode, :name, :brand, :kcal_100g, :protein_100g, :carbs_100g, :fat_100g, :fibre_100g, 'off', 0)
        ON CONFLICT(barcode) DO UPDATE SET name = excluded.name, brand = excluded.brand, kcal_100g = excluded.kcal_100g,
          protein_100g = excluded.protein_100g, carbs_100g = excluded.carbs_100g, fat_100g = excluded.fat_100g,
          fibre_100g = excluded.fibre_100g
        WHERE foods.source = 'off'
        """,
        rows,
    )


def run_import(db_path: Path | str, *, file: str | None = None, url: str = EXPORT_URL, limit: int | None = None,
               countries: Iterable[str] = DEFAULT_COUNTRIES, log_every: int = 50_000) -> int:
    started = time.time()
    conn = db.connect(db_path)
    accepted = 0
    batch: list[dict] = []
    try:
        with open_source(file, url) as text:
            for product in parse_rows(text, countries):
                batch.append(product)
                if len(batch) >= BATCH:
                    with db.transaction(conn):
                        upsert_batch(conn, batch)
                    accepted += len(batch)
                    batch = []
                    if accepted % log_every < BATCH:
                        log.info("off import: %d accepted so far (%.0fs)", accepted, time.time() - started)
                    if limit and accepted >= limit:
                        break
        if batch:
            with db.transaction(conn):
                upsert_batch(conn, batch)
            accepted += len(batch)
        with db.transaction(conn):
            store.set_setting(conn, SETTING_LAST_IMPORT, datetime.now(timezone.utc).isoformat(timespec="seconds"))
            store.set_setting(conn, SETTING_LAST_COUNT, str(accepted))
        log.info("off import done: %d rows in %.0fs", accepted, time.time() - started)
        return accepted
    finally:
        conn.close()


def status(conn: sqlite3.Connection) -> dict:
    n = conn.execute("SELECT COUNT(*) FROM foods WHERE source = 'off'").fetchone()[0]
    return {"off_products": n, "last_import": store.get_setting(conn, SETTING_LAST_IMPORT),
            "last_import_accepted": store.get_setting(conn, SETTING_LAST_COUNT)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file")
    ap.add_argument("--url", default=EXPORT_URL)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--countries", default=",".join(DEFAULT_COUNTRIES))
    ap.add_argument("--db")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.db:
        db_path = Path(args.db)
    else:
        from .config import get_settings

        db_path = get_settings().db_path
    n = run_import(db_path, file=args.file, url=args.url, limit=args.limit, countries=args.countries.split(","))
    print(f"accepted {n} products")
    return 0


if __name__ == "__main__":
    sys.exit(main())
