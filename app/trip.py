"""Multi-item in-person shopping trip planner + alternatives finder."""
from __future__ import annotations

import math
from datetime import timedelta

from .engine import Engine, Filters
from .textnorm import normalize_fts as normalize
from .utils import fa, fmt_distance, haversine_km, hours_status, walk_minutes

STOP_MIN = 10  # minutes spent in each shop
MAX_CANDIDATE_PRODUCTS = 6


def _strip(o):
    return {k: v for k, v in o.items() if not k.startswith("_")}


def item_candidates(engine: Engine, item: dict, f: Filters):
    """-> (label, {sid: best_offer}, info) for one wanted item."""
    if item.get("product_id"):
        row = engine.conn().execute("SELECT pid,title FROM products WHERE id=?", (item["product_id"],)).fetchone()
        if not row:
            return item.get("label") or item["product_id"], {}, {"error": "محصول پیدا نشد"}
        pids, label = [row["pid"]], item.get("label") or row["title"]
    else:
        q = item.get("query", "").strip()
        label = q
        pids = [p for p, _ in engine.match_products(q, 600)[:MAX_CANDIDATE_PRODUCTS * 10]]
        # keep products with title closest to the query: those that start with the phrase first
        if pids:
            rows = engine.product_rows(pids)
            toks = " ".join(engine.fts_terms(q))
            pids.sort(key=lambda p: 0 if normalize(rows[p]["title"]).startswith(toks) else 1)
            pids = pids[: MAX_CANDIDATE_PRODUCTS * 4]
    if not pids:
        return label, {}, {"error": "محصولی با این عنوان پیدا نشد"}
    sids = engine.sids_within(f)
    rows = engine.fetch_offer_rows(pids, sids)
    offs = []
    for r in rows:
        o = engine.build_offer(r, f)
        if engine.offer_passes(o, f):
            offs.append(o)
    if not item.get("product_id"):
        # a generic item ("باتری قلمی") matches many products: keep the comparable price band so the
        # plan does not "cover" an item with an unrelated, far more expensive product
        prices = sorted(o["price"]["value"] for o in offs if o["price"]["value"] and not o["price"]["outlier"])
        if prices:
            med = prices[len(prices) // 2]
            offs = [o for o in offs if o["price"]["value"] and not o["price"]["outlier"] and 0.4 * med <= o["price"]["value"] <= 2.5 * med]
    best: dict[int, dict] = {}
    for o in offs:
        k = (o["price"]["value"] is None, o["price"]["outlier"], o["price"]["value"] or 0)
        cur = best.get(o["_sid"])
        if cur is None or k < (cur["price"]["value"] is None, cur["price"]["outlier"], cur["price"]["value"] or 0):
            best[o["_sid"]] = o
    info = {}
    if not best:
        # explain: how far is the nearest seller?
        f2 = Filters(**{**f.to_dict(), "radius_km": 0, "open_now": False, "min_price": None, "max_price": None,
                        "trabpay": False, "installment": False, "fast": False, "warranty": False, "try_before_buy": False,
                        "when": f.when})
        allrows = engine.fetch_offer_rows(pids, engine.sids)
        if allrows:
            near = min((engine.build_offer(r, f2) for r in allrows), key=lambda o: o["distance_km"])
            info["nearest_km"] = near["distance_km"]
            info["error"] = f"در محدوده انتخابی فروشنده‌ای نیست؛ نزدیک‌ترین {near['distance_label']} فاصله دارد."
        else:
            info["error"] = "این کالا در دیتاست فروشنده‌ای ندارد."
    return label, best, info


def _route(engine: Engine, f: Filters, stops_sids: list[int]):
    """Nearest-neighbour ordering from the origin."""
    order, cur = [], (f.lat, f.lon)
    left = list(stops_sids)
    while left:
        nxt = min(left, key=lambda s: haversine_km(cur[0], cur[1], engine.shops[s]["lat"], engine.shops[s]["lon"]))
        order.append(nxt)
        left.remove(nxt)
        cur = (engine.shops[nxt]["lat"], engine.shops[nxt]["lon"])
    return order


def _assemble(engine: Engine, f: Filters, kind: str, title: str, assign: dict[int, list[int]], cands, labels):
    """assign: sid -> [item indexes]. Build a presentable plan with timing."""
    order = _route(engine, f, list(assign))
    cur = (f.lat, f.lon)
    elapsed = 0.0
    walk_km = 0.0
    stops = []
    total, unpriced = 0, 0
    for sid in order:
        s = engine.shops[sid]
        leg = haversine_km(cur[0], cur[1], s["lat"], s["lon"])
        wm = walk_minutes(leg) if leg > 0.03 else 0
        elapsed += wm
        arrival = f.when + timedelta(minutes=elapsed)
        hs = hours_status(s["hours"], arrival)
        items = []
        for i in assign[sid]:
            o = cands[i][sid]
            v = o["price"]["value"]
            if v:
                total += v
            else:
                unpriced += 1
            items.append({"item_index": i, "label": labels[i], "offer": _strip(o)})
        stops.append({"shop": engine.shop_brief(sid, f), "leg_km": round(leg, 3), "leg_label": fmt_distance(leg) if leg > 0.03 else "همان‌جا",
                      "leg_walk_min": wm, "arrive_in_min": int(elapsed), "hours_at_arrival": hs, "items": items})
        walk_km += leg
        elapsed += STOP_MIN
        cur = (s["lat"], s["lon"])
    back = haversine_km(cur[0], cur[1], f.lat, f.lon)
    return {"kind": kind, "title": title, "stops": stops, "stops_count": len(stops), "total_price": total,
            "total_display": fa(f"{total:,}") + " تومان", "unpriced_items": unpriced,
            "walk_km": round(walk_km, 2), "walk_label": fmt_distance(walk_km),
            "minutes": int(elapsed), "return_walk_min": walk_minutes(back) if back > 0.03 else 0,
            "warnings": [f"{st['shop']['name']}: {st['hours_at_arrival']['label']}" for st in stops
                         if st["hours_at_arrival"]["state"] in ("closed", "closing_soon")]}


def plan_trip(engine: Engine, items: list[dict], f: Filters, max_stops: int = 4):
    labels, cands, infos = [], [], []
    for it in items:
        label, best, info = item_candidates(engine, it, f)
        labels.append(label)
        cands.append(best)
        infos.append(info)
    wanted = [i for i in range(len(items)) if cands[i]]
    unmet = [{"item_index": i, "label": labels[i], "reason": infos[i].get("error", "پیدا نشد"),
              "nearest_km": infos[i].get("nearest_km")} for i in range(len(items)) if not cands[i]]
    out = {"items": [{"index": i, "label": labels[i], "found": bool(cands[i]), "sellers": len(cands[i])} for i in range(len(items))],
           "unmet": unmet, "plans": [], "filters": f.to_dict()}
    if not wanted:
        return out

    # Plan B: cheapest per item
    assign_b: dict[int, list[int]] = {}
    for i in wanted:
        def k(sid):
            o = cands[i][sid]
            return (o["price"]["value"] is None, o["price"]["value"] or 0, o["distance_km"])
        sid = min(cands[i], key=k)
        assign_b.setdefault(sid, []).append(i)

    # Plan A: greedy set cover, fewest stops (then nearest, then cheaper)
    remaining = set(wanted)
    assign_a: dict[int, list[int]] = {}
    pos = (f.lat, f.lon)
    while remaining and len(assign_a) < max_stops:
        shops = {}
        for i in remaining:
            for sid in cands[i]:
                shops.setdefault(sid, []).append(i)
        def score(sid):
            cover = len(shops[sid])
            d = haversine_km(pos[0], pos[1], engine.shops[sid]["lat"], engine.shops[sid]["lon"])
            cost = sum(cands[i][sid]["price"]["value"] or 0 for i in shops[sid])
            return (-cover, round(d, 1), cost)
        sid = min(shops, key=score)
        assign_a[sid] = shops[sid]
        remaining -= set(shops[sid])
        pos = (engine.shops[sid]["lat"], engine.shops[sid]["lon"])
    # items not covered inside max_stops -> fall back to their cheapest shop (adds stops)
    for i in remaining:
        sid = min(cands[i], key=lambda s: cands[i][s]["distance_km"])
        assign_a.setdefault(sid, []).append(i)

    plan_a = _assemble(engine, f, "fewest_stops", "کمترین توقف", assign_a, cands, labels)
    plan_b = _assemble(engine, f, "cheapest", "ارزان‌ترین", assign_b, cands, labels)
    # plan A only makes sense when it really saves stops or time
    a_useful = plan_a["stops_count"] < plan_b["stops_count"] or plan_a["minutes"] <= plan_b["minutes"] - 10
    plans = [plan_a, plan_b] if (assign_a != assign_b and a_useful) else [plan_b]
    rec = 0
    if len(plans) == 2:
        pa, pb = plan_a, plan_b
        premium = ((pa["total_price"] - pb["total_price"]) / pb["total_price"] * 100) if (pa["total_price"] and pb["total_price"]) else 0
        price_txt = ("با قیمت تقریباً برابر" if abs(premium) < 1 else
                     f"حدود {fa(abs(round(premium)))}٪ {'گران‌تر' if premium > 0 else 'ارزان‌تر'}")
        saved_stops = pb["stops_count"] - pa["stops_count"]
        saved_min = max(0, round(pb["minutes"] - pa["minutes"]))
        parts = []
        if saved_stops > 0:
            parts.append(f"{fa(saved_stops)} توقف کمتر")
        if saved_min > 0:
            parts.append(f"{fa(saved_min)} دقیقه زودتر")
        pa["tradeoff"] = " و ".join(parts) + "؛ " + price_txt
        diff = abs(pa["total_price"] - pb["total_price"])
        pb["tradeoff"] = (fa(f"{diff:,}") + " تومان صرفه‌جویی نسبت به طرح کمترین توقف") if diff else "هزینهٔ برابر با طرح کمترین توقف"
        rec = 0 if (saved_stops > 0 and premium <= 15) else 1
    plans[rec]["recommended"] = True
    out["plans"] = plans
    return out


# --------------------------------------------------------------------------- alternatives
def _tokens(s: str):
    return {t for t in normalize(s).split() if len(t) > 1}


def find_alternatives(engine: Engine, product_id: str, f: Filters, exclude_shop_ids=None, limit=5, ref_price: int | None = None):
    """When a seller says 'not in stock': other sellers of the same item + comparable products nearby."""
    db = engine.conn()
    p = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not p:
        return None
    ex = {engine.sid_by_id[s] for s in (exclude_shop_ids or []) if s in engine.sid_by_id}
    # 1) same product, other sellers (inside radius first, then widened)
    rows = db.execute("SELECT * FROM offers WHERE pid=?", (p["pid"],)).fetchall()
    same_in, same_out = [], []
    for r in rows:
        if r["sid"] in ex:
            continue
        o = engine.build_offer(r, f)
        if not engine.offer_passes(o, f):
            continue
        (same_in if f.radius_km == 0 or o["distance_km"] <= f.radius_km else same_out).append(o)
    engine.score_offers(same_in, f)
    engine.score_offers(same_out, f, "near")
    price_ref = ref_price or p["median_price"]

    # 2) comparable products: same category, similar words/brand, similar price, sold nearby
    sids = engine.sids_within(f)
    cand_rows = db.execute(
        "SELECT o.* FROM offers o WHERE o.sid IN (SELECT value FROM json_each(?)) AND o.pid IN "
        "(SELECT pid FROM products WHERE category=? AND pid<>?) LIMIT 6000",
        (__import__("json").dumps([int(x) for x in sids]), p["category"], p["pid"])).fetchall()
    by_pid: dict[int, list] = {}
    for r in cand_rows:
        if r["sid"] in ex and False:
            continue
        by_pid.setdefault(r["pid"], []).append(r)
    prods = engine.product_rows(list(by_pid))
    base_tokens = _tokens(p["title"])
    scored = []
    for pid, rs in by_pid.items():
        cp = prods[pid]
        tok = _tokens(cp["title"])
        jac = len(base_tokens & tok) / max(len(base_tokens | tok), 1)
        brand = 1.0 if (p["brand"] and cp["brand"] == p["brand"]) else 0.0
        offs = [engine.build_offer(r, f) for r in rs]
        offs = [o for o in offs if engine.offer_passes(o, f) and o["price"]["value"] and not o["price"]["outlier"]]
        if not offs:
            continue
        o = min(offs, key=lambda x: x["price"]["value"])
        pr = 1.0
        if price_ref and o["price"]["value"]:
            ratio = o["price"]["value"] / price_ref
            if ratio < 0.5 or ratio > 2.0:
                continue
            pr = 1 - min(abs(math.log(ratio)) / math.log(2), 1)
        sc = 0.5 * jac + 0.2 * brand + 0.3 * pr
        why = []
        if brand:
            why.append("همان برند")
        if jac >= 0.35:
            why.append("مشخصات/عنوان مشابه")
        if price_ref and o["price"]["value"]:
            why.append(f"قیمت {'نزدیک' if pr > 0.7 else 'در بازهٔ'} محصول اصلی")
        scored.append((sc, cp, o, why))
    scored.sort(key=lambda x: -x[0])
    similar = [{"id": cp["id"], "title": cp["title"], "brand": cp["brand"], "image": cp["image"],
                "similarity": round(sc, 2), "why": why, "offer": _strip(o)} for sc, cp, o, why in scored[:limit]]
    return {
        "product": {"id": p["id"], "title": p["title"]},
        "same_product": [_strip(o) for o in same_in[:limit]],
        "same_product_wider": [_strip(o) for o in same_out[:3]],
        "similar_products": similar,
    }
