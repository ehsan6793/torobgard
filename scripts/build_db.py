#!/usr/bin/env python3
"""Build the optimized SQLite database (with FTS5) from the Torob challenge CSVs.

Usage:  python scripts/build_db.py /path/to/csv_dir data/torob.db
"""
import json
import re
import sqlite3
import statistics
import sys
import time
import zlib
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.textnorm import normalize_fts as normalize  # noqa: E402

PRICE_MIN_OK = 1_000          # below this a price is almost surely a data error
PRICE_MAX_OK = 20_000_000_000  # above this too (2e10 toman)
UNIT_HINT_RE = re.compile(r"(قیمت\s*متری|هر\s*متر|متری|قیمت\s*کیلو(?:یی)?|هر\s*کیلو|کیلویی|قیمت\s*هر\s*(?:عدد|متر|کیلو|جفت))")
MALL_RE = re.compile(r"(پاساژ|مجتمع|مرکز خرید|بازارچه|پلاسکو|تجاری)\s*([^،,\-]+)")
FLOOR_RE = re.compile(r"طبقه\s*([^،,]+)")
UNIT_RE = re.compile(r"واحد\s*([^،,]+)")


def jl(x):
    if x is None or (isinstance(x, float) and pd.isna(x)) or x == "null":
        return None
    try:
        return json.loads(x)
    except Exception:
        return None


def build(src: Path, dst: Path):
    t0 = time.time()
    if dst.exists():
        dst.unlink()
    db = sqlite3.connect(dst)
    db.executescript(
        """
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE shops(
          sid INTEGER PRIMARY KEY, id TEXT UNIQUE, name TEXT, address TEXT, area TEXT,
          lat REAL, lon REAL, mall TEXT, floor TEXT, unit TEXT,
          categories TEXT, hours TEXT, payment TEXT, fast INTEGER,
          phone TEXT, website TEXT, instagram TEXT, n_offers INTEGER DEFAULT 0
        );
        CREATE TABLE products(
          pid INTEGER PRIMARY KEY, id TEXT UNIQUE, title TEXT, brand TEXT, category TEXT, model TEXT,
          image TEXT, n_offers INTEGER DEFAULT 0, median_price INTEGER
        );
        CREATE TABLE detail(pid INTEGER PRIMARY KEY, blob BLOB);  -- zlib(json{images,specs})
        CREATE TABLE offers(
          oid INTEGER PRIMARY KEY, id TEXT, pid INTEGER, sid INTEGER, price INTEGER, price_state TEXT,
          changed_at TEXT, seller_title TEXT, description TEXT, warranty TEXT, warranty_provider TEXT,
          warranty_months INTEGER, unit_hint INTEGER DEFAULT 0, outlier INTEGER DEFAULT 0
        );
        """
    )

    # ---------------- shops ----------------
    shops = pd.read_csv(src / "shops.csv", dtype=str, keep_default_na=False)
    sid_of = {}
    rows = []
    for i, r in enumerate(shops.itertuples(index=False), start=1):
        sid_of[r.shop_id] = i
        addr = r.address if r.address != "null" else ""
        mall = floor = unit = None
        m = MALL_RE.search(addr)
        if m:
            mall = (m.group(1) + " " + m.group(2)).strip()
        m = FLOOR_RE.search(addr)
        if m:
            floor = m.group(1).strip()
        m = UNIT_RE.search(addr)
        if m:
            unit = m.group(1).strip()
        contacts = jl(r.contacts) or []
        phone = next((c.get("display_number") for c in contacts if c.get("channel") == "phone"), None)
        fast = {"true": 1, "false": 0}.get(r.supports_fast_delivery.lower(), None)
        insta = jl(r.instagram)
        rows.append((
            i, r.shop_id, r.name, addr, None if r.location_label == "null" else r.location_label,
            float(r.lat), float(r.lon), mall, floor, unit,
            r.category_names if r.category_names != "null" else "[]",
            r.weekly_hours if r.weekly_hours != "null" else None,
            r.payment_options if r.payment_options != "null" else "[]",
            fast, phone,
            None if r.website_url == "null" else r.website_url,
            (insta or {}).get("profile_url") if isinstance(insta, dict) else None,
        ))
    db.executemany("INSERT INTO shops(sid,id,name,address,area,lat,lon,mall,floor,unit,categories,hours,payment,fast,phone,website,instagram) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    print(f"shops: {len(rows)}  ({time.time()-t0:.0f}s)")

    # ---------------- offers (needed first for product stats) ----------------
    off = pd.read_csv(src / "offers.csv", dtype=str, keep_default_na=False)
    prod_ids = {}
    # product pids assigned from products file order; read ids first
    pid_col = pd.read_csv(src / "products.csv", usecols=["product_id"], dtype=str)["product_id"]
    for i, p in enumerate(pid_col, start=1):
        prod_ids[p] = i
    del pid_col

    def pstate(v):
        if v in ("", "null"):
            return None, "missing"
        try:
            f = int(float(v))
        except ValueError:
            return None, "missing"
        if f < PRICE_MIN_OK or f > PRICE_MAX_OK:
            return f, "invalid"
        return f, "ok"

    by_prod = defaultdict(list)
    orows = []
    for j, r in enumerate(off.itertuples(index=False), start=1):
        price, st = pstate(r.price_toman)
        pid = prod_ids.get(r.product_id)
        sid = sid_of.get(r.shop_id)
        if pid is None or sid is None:
            continue
        w = jl(r.warranty) or {}
        wp = w.get("provider")
        wm = w.get("period_months")
        unit_hint = 1 if UNIT_HINT_RE.search(r.seller_title or "") else 0
        orows.append([j, r.offer_id, pid, sid, price, st, (r.last_price_changed_at if r.last_price_changed_at[:2] == '20' else None), r.seller_title,
                      None if r.description in ("", "null") else r.description,
                      None if r.warranty in ("", "null") else r.warranty, wp, wm, unit_hint, 0])
        if st == "ok":
            by_prod[pid].append((len(orows) - 1, price))
    # outlier detection (needs >=3 valid offers of the same product, ignore unit-hint offers)
    medians = {}
    for pid, lst in by_prod.items():
        prices = [p for _, p in lst]
        med = statistics.median(prices)
        medians[pid] = int(med)
        if len(prices) >= 3:
            for idx, p in lst:
                if p < med * 0.25 or p > med * 4:
                    orows[idx][13] = 1
    db.executemany("INSERT INTO offers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", orows)
    print(f"offers: {len(orows)}  ({time.time()-t0:.0f}s)")
    n_off_prod = defaultdict(int)
    n_off_shop = defaultdict(int)
    for o in orows:
        n_off_prod[o[2]] += 1
        n_off_shop[o[3]] += 1
    db.executemany("UPDATE shops SET n_offers=? WHERE sid=?", [(n, s) for s, n in n_off_shop.items()])
    del off, orows, by_prod

    # ---------------- products ----------------
    db.execute("CREATE VIRTUAL TABLE fts USING fts5(title, brand, category, model, tokenize='unicode61 remove_diacritics 2')")
    SPEC_KEEP = 24
    frows, prows, drows = [], [], []
    for chunk in pd.read_csv(src / "products.csv", dtype=str, keep_default_na=False, chunksize=20000):
        for r in chunk.itertuples(index=False):
            pid = prod_ids[r.product_id]
            imgs = jl(r.image_urls) or []
            specs = jl(r.specs) or []
            comp = [[s.get("key"), str(s.get("value"))[:140]] for s in specs[:SPEC_KEEP] if s.get("key") and s.get("value")]
            brand = None if r.brand_name in ("", "null") else r.brand_name
            model = None if r.model in ("", "null") else r.model
            prows.append((pid, r.product_id, r.persian_title, brand, r.category_name, model,
                          imgs[0] if imgs else None, n_off_prod.get(pid, 0), medians.get(pid)))
            drows.append((pid, zlib.compress(json.dumps({'images': imgs[:3], 'specs': comp}, ensure_ascii=False).encode(), 6)))
            frows.append((pid, normalize(r.persian_title), normalize(brand or ""), normalize(r.category_name), normalize(model or "")))
        db.executemany("INSERT INTO products(pid,id,title,brand,category,model,image,n_offers,median_price) VALUES(?,?,?,?,?,?,?,?,?)", prows)
        db.executemany("INSERT INTO detail VALUES(?,?)", drows)
        db.executemany("INSERT INTO fts(rowid,title,brand,category,model) VALUES(?,?,?,?,?)", frows)
        prows.clear(); frows.clear(); drows.clear()
        print("  products chunk done", f"({time.time()-t0:.0f}s)")

    db.executescript(
        """
        CREATE INDEX idx_off_pid ON offers(pid);
        CREATE INDEX idx_off_sid ON offers(sid);
        CREATE INDEX idx_prod_cat ON products(category);
        CREATE INDEX idx_shop_geo ON shops(lat, lon);
        """
    )
    # area table (landmarks)
    db.executescript(
        """
        CREATE TABLE areas AS
          SELECT area AS name, COUNT(*) AS n, AVG(lat) AS lat, AVG(lon) AS lon
          FROM shops WHERE area IS NOT NULL GROUP BY area HAVING COUNT(*) >= 8 ORDER BY n DESC;
        """
    )
    db.commit()
    db.execute("INSERT INTO fts(fts) VALUES('optimize')")
    db.commit()
    db.execute("VACUUM")
    db.close()
    print(f"done in {time.time()-t0:.0f}s -> {dst}  ({dst.stat().st_size/1e6:.0f} MB)")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/csv")
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/torob.db")
    build(src, dst)
