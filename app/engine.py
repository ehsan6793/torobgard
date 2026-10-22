"""Core data engine: shops cache, product search, offer scoring/explanations."""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import zlib
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .textnorm import normalize_fts as normalize
from .utils import (DEFAULT_ORIGIN, bbox, fa, fmt_distance, haversine_km, hours_status, jalali_str,
                    now_tehran, parse_when, walk_minutes, week_table, TEHRAN)

STOP_TOKENS = {"و", "برای", "با", "از", "در", "به", "یک", "یه", "مدل", "قیمت", "خرید", "میخوام", "می", "خوام", "دنبال", "نزدیک", "ارزان"}
NO_WARRANTY = "بدون گارانتی"
TRY_BEFORE_BUY = "تست و تحویل در حضور مشتری"


class Filters:
    """Normalised user-facing filters shared by search / detail / trip / alternatives."""

    def __init__(self, lat=None, lon=None, radius_km=3.0, min_price=None, max_price=None, open_now=False,
                 when=None, trabpay=False, installment=False, fast=False, warranty=False, try_before_buy=False,
                 area_label=None):
        self.lat = lat if lat is not None else DEFAULT_ORIGIN["lat"]
        self.lon = lon if lon is not None else DEFAULT_ORIGIN["lon"]
        self.radius_km = float(radius_km or 0)
        self.min_price = int(min_price) if min_price else None
        self.max_price = int(max_price) if max_price else None
        self.open_now = bool(open_now)
        self.when = when if isinstance(when, datetime) else parse_when(when)
        self.trabpay = bool(trabpay)
        self.installment = bool(installment)
        self.fast = bool(fast)
        self.warranty = bool(warranty)
        self.try_before_buy = bool(try_before_buy)
        self.area_label = area_label

    def to_dict(self):
        return {"lat": self.lat, "lon": self.lon, "radius_km": self.radius_km, "min_price": self.min_price,
                "max_price": self.max_price, "open_now": self.open_now, "when": self.when.isoformat(),
                "trabpay": self.trabpay, "installment": self.installment, "fast": self.fast,
                "warranty": self.warranty, "try_before_buy": self.try_before_buy, "area_label": self.area_label}


def payment_info(payment_json: str | None) -> dict:
    out = {"trabpay": None, "installments": []}
    if not payment_json:
        return out
    try:
        entries = json.loads(payment_json)
    except Exception:
        return out
    for e in entries:
        name = (e.get("provider_name") or "").strip()
        confirmed = e.get("channel") == "in_person"
        item = {"name": name, "confirmed_in_person": confirmed,
                "min_price": e.get("min_price_toman"), "max_price": e.get("max_price_toman")}
        if "ترب" in name:
            if out["trabpay"] is None or confirmed:
                out["trabpay"] = item
        else:
            out["installments"].append(item)
    return out


class Engine:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if not Path(db_path).exists():
            raise FileNotFoundError(
                f"دیتابیس پیدا نشد: {db_path} — اول اسکریپت ساخت دیتابیس (build_db.py / 2_build_db.bat) را اجرا کن."
            )
        self._local = threading.local()
        db = self.conn()
        self.snapshot = datetime.fromisoformat(db.execute("SELECT MAX(changed_at) FROM offers WHERE changed_at LIKE '20%'").fetchone()[0]).astimezone(TEHRAN)
        self.shops: dict[int, dict] = {}
        self.sid_by_id: dict[str, int] = {}
        for r in db.execute("SELECT sid,id,name,address,area,lat,lon,mall,floor,unit,categories,hours,payment,fast,phone,website,instagram,n_offers FROM shops"):
            d = dict(r)
            d["pay"] = payment_info(d["payment"])
            self.shops[d["sid"]] = d
            self.sid_by_id[d["id"]] = d["sid"]
        self.sids = np.array(sorted(self.shops), dtype=np.int64)
        self.lats = np.array([self.shops[s]["lat"] for s in self.sids])
        self.lons = np.array([self.shops[s]["lon"] for s in self.sids])
        self.has_offers = np.array([self.shops[s]["n_offers"] > 0 for s in self.sids])
        self.areas = [dict(r) for r in db.execute("SELECT name,n,lat,lon FROM areas ORDER BY n DESC LIMIT 40")]

    # ------------------------------------------------------------ plumbing
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            # Path.as_uri() always uses forward slashes, so this works on Windows
            # (where str(path) gives "C:\...", which is not a valid file: URI) and POSIX alike.
            uri = Path(self.db_path).resolve().as_uri() + "?mode=ro"
            c = sqlite3.connect(uri, uri=True, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=1")
            c.execute("PRAGMA cache_size=-65536")
            c.execute("PRAGMA mmap_size=268435456")
            self._local.c = c
        return c

    def sids_within(self, f: Filters) -> np.ndarray:
        """Shop ids having offers within the radius (all if radius==0)."""
        mask = self.has_offers
        if f.radius_km > 0:
            d = self.dist_array(f.lat, f.lon)
            mask = mask & (d <= f.radius_km)
        return self.sids[mask]

    def dist_array(self, lat, lon):
        p1 = np.radians(lat)
        p2 = np.radians(self.lats)
        dphi = p2 - p1
        dl = np.radians(self.lons - lon)
        a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
        return 2 * 6371.0088 * np.arcsin(np.sqrt(a))

    # ------------------------------------------------------------ FTS
    @staticmethod
    def fts_terms(q: str):
        toks = [t for t in normalize(q).split() if t not in STOP_TOKENS]
        return toks or normalize(q).split()

    def match_products(self, q: str, limit=2500):
        """-> list[(pid, rank_index)] best first. AND semantics, falls back to OR."""
        toks = self.fts_terms(q)
        if not toks:
            return []
        db = self.conn()
        esc = lambda t: '"' + t.replace('"', "") + '"*'
        for joiner in (" AND ", " OR "):
            expr = joiner.join(esc(t) for t in toks)
            rows = db.execute(
                "SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY bm25(fts, 8, 3, 2, 1) LIMIT ?", (expr, limit)).fetchall()
            if len(rows) >= 3 or joiner == " OR " or len(toks) == 1:
                return [(r[0], i) for i, r in enumerate(rows)]
        return []

    # ------------------------------------------------------------ offer helpers
    def freshness(self, changed_at: str | None):
        if not changed_at:
            return {"iso": None, "age_days": None, "level": "unknown", "jalali": None, "label": "تاریخ نامشخص"}
        d = datetime.fromisoformat(changed_at).astimezone(TEHRAN)
        age = max(0, (self.snapshot - d).days)
        level = "fresh" if age <= 7 else "aging" if age <= 30 else "stale"
        label = "امروز/تازه" if age == 0 else f"{fa(age)} روز پیش"
        return {"iso": d.isoformat(), "age_days": age, "level": level, "jalali": jalali_str(d), "label": label}

    def conditions(self, shop: dict, offer: dict | None, price: int | None) -> dict:
        pay = shop["pay"]
        tp = pay["trabpay"]
        tp_out = None
        if tp:
            ok_limit = True
            if price:
                if tp.get("max_price") and price > tp["max_price"]:
                    ok_limit = False
                if tp.get("min_price") and price < tp["min_price"]:
                    ok_limit = False
            tp_out = {"confirmed": tp["confirmed_in_person"], "within_limits": ok_limit}
        warranty = None
        if offer is not None:
            prov, months = offer["warranty_provider"], offer["warranty_months"]
            if prov and prov != NO_WARRANTY and prov != TRY_BEFORE_BUY:
                warranty = {"provider": prov, "months": months}
            elif prov == NO_WARRANTY:
                warranty = {"provider": "بدون گارانتی", "months": None, "none": True}
        return {
            "trabpay": tp_out,
            "installments": [{"name": i["name"], "confirmed": i["confirmed_in_person"]} for i in pay["installments"]],
            "fast_delivery": {1: True, 0: False}.get(shop["fast"]),
            "warranty": warranty,
            "try_before_buy": bool(offer and offer["warranty_provider"] == TRY_BEFORE_BUY),
        }

    def shop_brief(self, sid: int, f: Filters | None = None) -> dict:
        s = self.shops[sid]
        out = {"id": s["id"], "name": s["name"], "address": s["address"], "area": s["area"], "mall": s["mall"],
               "floor": s["floor"], "unit": s["unit"], "lat": s["lat"], "lon": s["lon"], "phone": s["phone"],
               "website": s["website"], "instagram": s["instagram"]}
        if f is not None:
            km = haversine_km(f.lat, f.lon, s["lat"], s["lon"])
            out["distance_km"] = round(km, 3)
            out["distance_label"] = fmt_distance(km)
            out["walk_min"] = walk_minutes(km)
        return out

    def build_offer(self, row, f: Filters) -> dict:
        s = self.shops[row["sid"]]
        km = haversine_km(f.lat, f.lon, s["lat"], s["lon"])
        wmin = walk_minutes(km)
        arrival = f.when + timedelta(minutes=wmin)
        price = row["price"] if row["price_state"] == "ok" else None
        hs = hours_status(s["hours"], arrival)
        return {
            "offer_id": row["id"],
            "product_pid": row["pid"],
            "shop": {"id": s["id"], "name": s["name"], "address": s["address"], "area": s["area"], "mall": s["mall"],
                     "floor": s["floor"], "unit": s["unit"], "lat": s["lat"], "lon": s["lon"], "phone": s["phone"]},
            "distance_km": round(km, 3), "distance_label": fmt_distance(km), "walk_min": wmin,
            "seller_title": row["seller_title"],
            "description": row["description"],
            "price": {
                "value": price, "state": row["price_state"],
                "raw": row["price"] if row["price_state"] == "invalid" else None,
                "display": (fa(f"{price:,}").replace(",", "٬") + " تومان") if price else ("قیمت ثبت نشده" if row["price_state"] == "missing" else "قیمت نامعتبر در دیتا"),
                "outlier": bool(row["outlier"]), "unit_hint": bool(row["unit_hint"]),
            },
            "price_updated": self.freshness(row["changed_at"]),
            "stock": {"state": "unverified", "label": "موجودی تأییدنشده"},
            "hours": hs,
            "conditions": self.conditions(s, row, price),
            "_sid": row["sid"],
        }

    # filters on built offers ------------------------------------------------
    def offer_passes(self, o: dict, f: Filters) -> bool:
        p = o["price"]
        if (f.min_price or f.max_price) and p["state"] != "ok":
            return False
        if f.max_price and p["value"] and p["value"] > f.max_price:
            return False
        if f.min_price and p["value"] and p["value"] < f.min_price:
            return False
        c = o["conditions"]
        if f.open_now and o["hours"]["state"] not in ("open", "closing_soon"):
            return False
        if f.open_now and o["hours"]["state"] == "closing_soon" and o["hours"].get("minutes_left", 0) < 10:
            return False
        if f.trabpay and not c["trabpay"]:
            return False
        if f.installment and not c["installments"]:
            return False
        if f.fast and not c["fast_delivery"]:
            return False
        if f.warranty and not (c["warranty"] and not c["warranty"].get("none")):
            return False
        if f.try_before_buy and not c["try_before_buy"]:
            return False
        return True

    @staticmethod
    def score_offers(offers: list[dict], f: Filters, mode: str = "best"):
        """Lower score is better. Adds `score` and sorts in place."""
        prices = [o["price"]["value"] for o in offers if o["price"]["value"] and not o["price"]["outlier"]]
        pmin, pmax = (min(prices), max(prices)) if prices else (0, 0)
        span = max(pmax - pmin, 1)
        rad = f.radius_km if f.radius_km > 0 else max([o["distance_km"] for o in offers] + [1])
        wp, wd = {"best": (0.55, 0.30), "cheap": (0.9, 0.05), "near": (0.1, 0.85)}.get(mode, (0.55, 0.30))
        for o in offers:
            p = o["price"]
            if p["value"] and not p["outlier"]:
                pn = (p["value"] - pmin) / span
            elif p["value"]:
                pn = 1.0
            else:
                pn = 1.2
            sc = wp * pn + wd * min(o["distance_km"] / max(rad, 0.5), 1.5)
            st = o["hours"]["state"]
            sc += {"open": 0, "closing_soon": 0.12, "unknown": 0.06, "closed": 0.5}.get(st, 0.05)
            if o["price_updated"]["level"] == "stale":
                sc += 0.08
            if p["outlier"]:
                sc += 0.15
            o["score"] = round(sc, 4)
        offers.sort(key=lambda o: (o["score"], o["distance_km"]))
        return offers

    @staticmethod
    def explain(o: dict, ctx: dict) -> list[str]:
        """Short Persian reasons: why this offer is suggested (UX requirement: transparency)."""
        r = []
        p = o["price"]
        if p["value"] and ctx.get("cheapest_id") == o["offer_id"]:
            r.append("ارزان‌ترین قیمت ثبت‌شده در محدوده")
        if ctx.get("nearest_id") == o["offer_id"]:
            r.append(f"نزدیک‌ترین فروشنده ({o['distance_label']})")
        st = o["hours"]["state"]
        if st == "open":
            r.append(o["hours"]["label"])
        elif st == "closing_soon":
            r.append("⚠ " + o["hours"]["label"])
        elif st == "closed":
            r.append("⚠ " + o["hours"]["label"])
        c = o["conditions"]
        if c["trabpay"]:
            r.append("ترب‌پی حضوری" if c["trabpay"]["confirmed"] else "ترب‌پی (حضوری تأیید نشده)")
        if c["try_before_buy"]:
            r.append("تست و تحویل در حضور مشتری")
        if o["price_updated"]["level"] == "fresh":
            r.append("قیمت به‌روز")
        if o["shop"]["mall"]:
            r.append(o["shop"]["mall"])
        return r[:4]

    # ------------------------------------------------------------ queries
    def fetch_offer_rows(self, pids, sids):
        db = self.conn()
        return db.execute(
            "SELECT * FROM offers WHERE pid IN (SELECT value FROM json_each(?)) AND sid IN (SELECT value FROM json_each(?))",
            (json.dumps(list(map(int, pids))), json.dumps(list(map(int, sids))))).fetchall()

    def product_rows(self, pids):
        db = self.conn()
        rows = db.execute("SELECT * FROM products WHERE pid IN (SELECT value FROM json_each(?))",
                          (json.dumps(list(map(int, pids))),)).fetchall()
        return {r["pid"]: r for r in rows}

    def product_card(self, p, offers: list[dict], f: Filters, total_in_city: int | None = None) -> dict:
        valid = [o for o in offers if o["price"]["value"] and not o["price"]["outlier"]] or [o for o in offers if o["price"]["value"]]
        cheapest = min(valid, key=lambda o: o["price"]["value"]) if valid else None
        nearest = min(offers, key=lambda o: o["distance_km"])
        best = offers[0]
        ctx = {"cheapest_id": cheapest["offer_id"] if cheapest else None, "nearest_id": nearest["offer_id"]}
        pr = [o["price"]["value"] for o in valid]
        return {
            "id": p["id"], "title": p["title"], "brand": p["brand"], "category": p["category"], "image": p["image"],
            "sellers_in_range": len(offers), "sellers_total": p["n_offers"],
            "price_min": min(pr) if pr else None, "price_max": max(pr) if pr else None,
            "price_min_display": (fa(f"{min(pr):,}").replace(",", "٬") + " تومان") if pr else None,
            "no_price": not pr,
            "best": {k: v for k, v in best.items() if not k.startswith("_")} | {"reasons": self.explain(best, ctx)},
            "nearest_km": nearest["distance_km"], "nearest_label": nearest["distance_label"],
            "any_open": any(o["hours"]["state"] in ("open", "closing_soon") for o in offers),
        }

    # ------------------------------------------------------------ search
    def search(self, q: str, f: Filters, sort="relevance", limit=20, offset=0):
        matches = self.match_products(q)
        if not matches:
            return {"query": q, "total": 0, "items": [], "hints": ["نتیجه‌ای پیدا نشد؛ عبارت ساده‌تری امتحان کنید."], "filters": f.to_dict()}
        rank = {pid: i for pid, i in matches}
        sids = self.sids_within(f)
        rows = self.fetch_offer_rows(rank.keys(), sids)
        by_pid: dict[int, list] = {}
        for r in rows:
            o = self.build_offer(r, f)
            if self.offer_passes(o, f):
                by_pid.setdefault(r["pid"], []).append(o)
        mode = {"cheap": "cheap", "near": "near"}.get(sort, "best")
        groups = []
        for pid, offs in by_pid.items():
            self.score_offers(offs, f, mode)
            groups.append((pid, offs))
        # product ordering
        def key(g):
            pid, offs = g
            if sort == "cheap":
                prices = [o["price"]["value"] for o in offs if o["price"]["value"]]
                return (min(prices) if prices else 1e18, rank[pid])
            if sort == "near":
                return (min(o["distance_km"] for o in offs), rank[pid])
            return (rank[pid] - 2 * math.log1p(len(offs)) - boost.get(pid, 0), rank[pid])
        boost = {}
        if sort not in ("cheap", "near"):
            toks = self.fts_terms(q)
            phrase = " ".join(toks)
            for pid, p in self.product_rows([g[0] for g in groups]).items():
                t = normalize(p["title"])
                if t.startswith(phrase):
                    boost[pid] = 400
                elif t.startswith(toks[0]):
                    boost[pid] = 150
                elif phrase in t:
                    boost[pid] = 60
        groups.sort(key=key)
        total = len(groups)
        page = groups[offset: offset + limit]
        prods = self.product_rows([pid for pid, _ in page])
        items = [self.product_card(prods[pid], offs, f) for pid, offs in page]
        # hints when empty / sparse
        hints = []
        if total == 0:
            hints = self.empty_hints(q, matches, f)
        return {"query": q, "total": total, "items": items, "hints": hints, "filters": f.to_dict(),
                "matched_products": len(matches)}

    def empty_hints(self, q, matches, f: Filters):
        """Explain WHY nothing was found and what to relax (smart, actionable)."""
        hints = []
        pids = [p for p, _ in matches]
        everywhere = Filters(**{**f.to_dict(), "radius_km": 0, "min_price": None, "max_price": None, "open_now": False,
                                "trabpay": False, "installment": False, "fast": False, "warranty": False, "try_before_buy": False,
                                "when": f.when})
        rows = self.fetch_offer_rows(pids, self.sids)
        if not rows:
            return ["این محصول در فروشگاه‌های دیتاست ثبت نشده است."]
        offs = [self.build_offer(r, everywhere) for r in rows]
        nearest = min(offs, key=lambda o: o["distance_km"])
        if f.radius_km and nearest["distance_km"] > f.radius_km:
            hints.append(f"نزدیک‌ترین فروشنده {nearest['distance_label']} با شما فاصله دارد؛ محدوده را بیشتر کنید.")
        within = [o for o in offs if not f.radius_km or o["distance_km"] <= f.radius_km]
        if within:
            if f.max_price or f.min_price:
                prices = [o["price"]["value"] for o in within if o["price"]["value"]]
                if prices and f.max_price and min(prices) > f.max_price:
                    hints.append(f"ارزان‌ترین قیمت در محدوده {fa(f'{min(prices):,}')} تومان است؛ بودجه را بالاتر ببرید.")
            if f.open_now and not any(o["hours"]["state"] in ("open", "closing_soon") for o in within):
                hints.append("فروشنده‌های این محدوده در زمان انتخابی بسته‌اند؛ زمان مراجعه را عوض کنید.")
            if f.trabpay or f.installment or f.warranty or f.fast or f.try_before_buy:
                hints.append("فیلتر شرایط خرید (ترب‌پی/اقساط/گارانتی/ارسال) را کم کنید.")
        return hints or ["فیلترها را کمی آزادتر کنید."]

    # ------------------------------------------------------------ product detail
    def product_detail(self, pid_text: str, f: Filters, sort="best", limit=60):
        db = self.conn()
        p = db.execute("SELECT * FROM products WHERE id=?", (pid_text,)).fetchone()
        if not p:
            return None
        blob = db.execute("SELECT blob FROM detail WHERE pid=?", (p["pid"],)).fetchone()
        det = json.loads(zlib.decompress(blob[0])) if blob else {"images": [], "specs": []}
        rows = db.execute("SELECT * FROM offers WHERE pid=?", (p["pid"],)).fetchall()
        offers = [self.build_offer(r, f) for r in rows]
        in_range, out_range = [], []
        for o in offers:
            ok = self.offer_passes(o, f)
            near = f.radius_km == 0 or o["distance_km"] <= f.radius_km
            (in_range if ok and near else out_range).append(o)
        mode = {"cheap": "cheap", "near": "near"}.get(sort, "best")
        self.score_offers(in_range, f, mode)
        self.score_offers(out_range, f, mode)
        valid = [o for o in offers if o["price"]["value"] and not o["price"]["outlier"]]
        cheapest = min(valid, key=lambda o: o["price"]["value"]) if valid else None
        nearest = min(in_range, key=lambda o: o["distance_km"]) if in_range else None
        ctx = {"cheapest_id": cheapest["offer_id"] if cheapest else None, "nearest_id": nearest["offer_id"] if nearest else None}
        strip = lambda o, extra=None: {k: v for k, v in o.items() if not k.startswith("_")} | (extra or {})
        prices = sorted(o["price"]["value"] for o in valid)
        return {
            "id": p["id"], "title": p["title"], "brand": p["brand"], "category": p["category"], "model": p["model"],
            "images": det["images"] or ([p["image"]] if p["image"] else []), "specs": det["specs"],
            "stats": {"sellers_total": len(offers), "priced": len(prices),
                      "min": prices[0] if prices else None, "max": prices[-1] if prices else None,
                      "median": prices[len(prices) // 2] if prices else None,
                      "snapshot": jalali_str(self.snapshot)},
            "in_range": [strip(o, {"reasons": self.explain(o, ctx), "in_range": True}) for o in in_range[:limit]],
            "out_of_range": [strip(o, {"reasons": [], "in_range": False}) for o in out_range[:20]],
            "out_of_range_count": len(out_range),
            "filters": f.to_dict(),
        }

    # ------------------------------------------------------------ shop detail
    def shop_detail(self, shop_id: str, f: Filters, q: str | None = None, limit=30):
        sid = self.sid_by_id.get(shop_id)
        if not sid:
            return None
        s = self.shops[sid]
        brief = self.shop_brief(sid, f)
        db = self.conn()
        if q:
            m = self.match_products(q, 3000)
            pid_set = [pid for pid, _ in m]
            rows = db.execute("SELECT o.*, p.title, p.image FROM offers o JOIN products p ON p.pid=o.pid WHERE o.sid=? AND o.pid IN (SELECT value FROM json_each(?)) LIMIT ?",
                              (sid, json.dumps(pid_set), limit)).fetchall()
        else:
            rows = db.execute("SELECT o.*, p.title, p.image FROM offers o JOIN products p ON p.pid=o.pid WHERE o.sid=? ORDER BY o.changed_at DESC LIMIT ?", (sid, limit)).fetchall()
        offers = []
        for r in rows:
            o = self.build_offer(r, f)
            offers.append({k: v for k, v in o.items() if not k.startswith("_") and k not in ("shop",)} |
                          {"product": {"title": r["title"], "image": r["image"]}})
        pay = s["pay"]
        return {
            **brief,
            "categories": json.loads(s["categories"] or "[]")[:15],
            "hours": hours_status(s["hours"], f.when + timedelta(minutes=brief["walk_min"])),
            "week": week_table(s["hours"]),
            "conditions": {"trabpay": ({"confirmed": pay["trabpay"]["confirmed_in_person"]} if pay["trabpay"] else None),
                           "installments": [{"name": i["name"], "confirmed": i["confirmed_in_person"]} for i in pay["installments"]],
                           "fast_delivery": {1: True, 0: False}.get(s["fast"])},
            "offers_total": s["n_offers"], "offers": offers,
        }
