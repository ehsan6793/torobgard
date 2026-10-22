"""Shopping agent: LLM tool-calling loop (OpenAI-compatible API) with a deterministic rule-based fallback.

The LLM never invents data: every product/shop/price comes from the tools below, which query the real dataset.
"""
from __future__ import annotations

import json
import os

import httpx

from . import nlu
from .engine import Engine, Filters
from .trip import find_alternatives, plan_trip
from .utils import fa, fmt_distance, parse_when

LLM_BASE = os.environ.get("LLM_BASE_URL", "https://api.metisai.ir/openai/v1")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o")
MAX_STEPS = 5


def llm_enabled() -> bool:
    return bool(LLM_KEY)


SYSTEM_PROMPT = """تو «دستیار خرید حضوری تهران» هستی؛ به کاربر کمک می‌کنی محصول را در فروشگاه‌ها و پاساژهای تهران پیدا کند، قیمت‌ها را مقایسه کند، بهترین فروشنده در محدوده/بودجه/زمان مراجعه‌اش را انتخاب کند و در صورت ناموجود بودن جایگزین بگیرد.
قواعد:
- همیشه فارسی، کوتاه و مفید جواب بده. هیچ داده‌ای از خودت نساز؛ فقط از خروجی ابزارها استفاده کن.
- «قیمت ثبت‌شده» با «موجودی» فرق دارد: موجودی هیچ‌وقت تأیید نشده است. هرگز نگو «موجود است»؛ بگو «قیمت ثبت‌شده … است، پیش از رفتن موجودی را از فروشنده بپرس».
- اگر ساعت کاری/تاریخ قیمت/قیمت نامشخص است، صریح بگو.
- کاربر محدوده، بودجه، شرایط خرید (ترب‌پی حضوری، اقساط، گارانتی، ارسال فوری) و زمان مراجعه را در پیامش می‌گوید؛ آن‌ها را به‌عنوان آرگومان ابزار بفرست.
- برای چند کالا با هم از plan_trip استفاده کن (کمترین توقف در برابر ارزان‌ترین را توضیح بده).
- اگر کاربر گفت فروشنده کالا را ندارد، از find_alternatives استفاده کن.
- نتیجه‌ها به‌صورت کارت زیر پیام نمایش داده می‌شوند؛ لازم نیست همه را در متن تکرار کنی. حداکثر ۲ تا ۴ جملهٔ مفید بنویس و بگو «چرا» این گزینه پیشنهاد شد."""

AREA_HELP = "نام محله/محدوده مثل فردوسی، شیخ هادی، بهارستان (اختیاری؛ اگر نگویی از موقعیت فعلی کاربر استفاده می‌شود)"
_FILTER_PROPS = {
    "max_price": {"type": "integer", "description": "سقف بودجه به تومان"},
    "min_price": {"type": "integer", "description": "حداقل قیمت به تومان"},
    "radius_km": {"type": "number", "description": "شعاع جستجو به کیلومتر"},
    "area": {"type": "string", "description": AREA_HELP},
    "open_now": {"type": "boolean", "description": "فقط فروشگاه‌هایی که در زمان مراجعه باز هستند"},
    "when": {"type": "string", "description": "زمان مراجعه به صورت ISO (اختیاری؛ پیش‌فرض الان)"},
    "trabpay": {"type": "boolean"}, "installment": {"type": "boolean"}, "fast": {"type": "boolean"},
    "warranty": {"type": "boolean"}, "try_before_buy": {"type": "boolean"},
}
TOOLS = [
    {"type": "function", "function": {
        "name": "search_products", "description": "جستجوی محصول در دیتاست فروشگاه‌های تهران با فیلتر محدوده/بودجه/شرایط خرید",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "عنوان محصول (فارسی)"},
            "sort": {"type": "string", "enum": ["relevance", "cheap", "near"]}, **_FILTER_PROPS}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_product_offers", "description": "فروشنده‌های یک محصول مشخص، مرتب‌شده بر اساس بهترین انتخاب",
        "parameters": {"type": "object", "properties": {"product_id": {"type": "string"}, **_FILTER_PROPS}, "required": ["product_id"]}}},
    {"type": "function", "function": {
        "name": "plan_trip", "description": "برنامهٔ خرید چندکالایی: کمترین توقف و ارزان‌ترین مسیر",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "string"}, "description": "فهرست کالاها"}, **_FILTER_PROPS}, "required": ["items"]}}},
    {"type": "function", "function": {
        "name": "find_alternatives", "description": "جایگزین وقتی فروشنده کالا را ندارد: فروشنده‌های دیگر همان کالا و محصولات مشابه",
        "parameters": {"type": "object", "properties": {
            "product_id": {"type": "string"}, "exclude_shop_ids": {"type": "array", "items": {"type": "string"}}, **_FILTER_PROPS},
            "required": ["product_id"]}}},
]


def _filters_from(ctx: dict, args: dict, engine: Engine) -> Filters:
    base = {**(ctx or {})}
    for k in ("max_price", "min_price", "radius_km", "open_now", "trabpay", "installment", "fast", "warranty", "try_before_buy", "when"):
        if args.get(k) is not None:
            base[k] = args[k]
    area = args.get("area")
    if area:
        for a in engine.areas:
            if a["name"] in area or area in a["name"]:
                base["lat"], base["lon"], base["area_label"] = a["lat"], a["lon"], a["name"]
                break
    return Filters(**{k: v for k, v in base.items() if k in Filters.__init__.__code__.co_varnames})


def _compact_card(c: dict) -> dict:
    b = c["best"]
    return {"id": c["id"], "title": c["title"], "price_from": c["price_min_display"], "sellers_in_range": c["sellers_in_range"],
            "nearest": c["nearest_label"], "best_shop": b["shop"]["name"], "best_mall": b["shop"]["mall"],
            "best_price": b["price"]["display"], "best_hours": b["hours"]["label"], "price_age": b["price_updated"]["label"],
            "reasons": b["reasons"]}


def run_tool(engine: Engine, name: str, args: dict, ctx: dict, cards: list, state: dict) -> dict:
    f = _filters_from(ctx, args, engine)
    state["filters"] = f.to_dict()
    if name == "search_products":
        res = engine.search(args.get("query", ""), f, args.get("sort", "relevance"), limit=6)
        state["query"] = args.get("query", "")
        cards.append({"type": "products", "query": args.get("query", ""), "total": res["total"], "items": res["items"],
                      "hints": res["hints"], "filters": res["filters"]})
        return {"total_found": res["total"], "hints": res["hints"], "top": [_compact_card(c) for c in res["items"]]}
    if name == "get_product_offers":
        d = engine.product_detail(args["product_id"], f)
        if not d:
            return {"error": "محصول پیدا نشد"}
        cards.append({"type": "offers", "product": {k: d[k] for k in ("id", "title", "brand", "images")}, "offers": d["in_range"][:6],
                      "out_of_range_count": d["out_of_range_count"], "filters": d["filters"]})
        return {"title": d["title"], "stats": d["stats"],
                "offers": [{"shop": o["shop"]["name"], "mall": o["shop"]["mall"], "price": o["price"]["display"],
                            "distance": o["distance_label"], "hours": o["hours"]["label"], "age": o["price_updated"]["label"],
                            "reasons": o["reasons"]} for o in d["in_range"][:6]],
                "out_of_range_count": d["out_of_range_count"]}
    if name == "plan_trip":
        plan = plan_trip(engine, [{"query": q} for q in args.get("items", [])], f)
        cards.append({"type": "trip", "plan": plan})
        return {"unmet": plan["unmet"],
                "plans": [{"title": p["title"], "stops": p["stops_count"], "total": p["total_display"], "minutes": p["minutes"],
                           "tradeoff": p.get("tradeoff"), "recommended": p.get("recommended", False),
                           "route": [{"shop": s["shop"]["name"], "items": [i["label"] for i in s["items"]],
                                      "hours": s["hours_at_arrival"]["label"]} for s in p["stops"]]} for p in plan["plans"]]}
    if name == "find_alternatives":
        alt = find_alternatives(engine, args["product_id"], f, args.get("exclude_shop_ids") or [])
        if not alt:
            return {"error": "محصول پیدا نشد"}
        cards.append({"type": "alternatives", "data": alt})
        return {"same_product": [{"shop": o["shop"]["name"], "price": o["price"]["display"], "distance": o["distance_label"], "hours": o["hours"]["label"]}
                                 for o in alt["same_product"][:4]],
                "similar": [{"title": s["title"], "why": s["why"], "price": s["offer"]["price"]["display"], "shop": s["offer"]["shop"]["name"]}
                            for s in alt["similar_products"][:4]]}
    return {"error": "ابزار ناشناخته"}


def _llm_chat(messages: list[dict]) -> dict:
    r = httpx.post(f"{LLM_BASE.rstrip('/')}/chat/completions", timeout=60,
                   headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
                   json={"model": LLM_MODEL, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "temperature": 0.2})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def chat_llm(engine: Engine, history: list[dict], ctx: dict) -> dict:
    cards: list = []
    state: dict = {}
    sys_ctx = (f"زمینه: کاربر در حال حاضر نزدیک «{ctx.get('area_label') or 'فردوسی'}» است، شعاع {ctx.get('radius_km', 3)} کیلومتر. "
               f"زمان مراجعه: {ctx.get('when') or 'همین حالا'}. محله‌های شناخته‌شده: " + "، ".join(a["name"] for a in engine.areas[:25]))
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + sys_ctx}] + history
    for _ in range(MAX_STEPS):
        msg = _llm_chat(messages)
        calls = msg.get("tool_calls")
        if not calls:
            return {"reply": msg.get("content") or "", "cards": cards, "state": state, "mode": "llm"}
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        for c in calls:
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
                result = run_tool(engine, c["function"]["name"], args, ctx, cards, state)
            except Exception as e:  # tool errors go back to the model, never crash the chat
                result = {"error": f"{type(e).__name__}: {e}"}
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, ensure_ascii=False)[:6000]})
    return {"reply": "نتیجه‌ها را در کارت‌های پایین گذاشتم.", "cards": cards, "state": state, "mode": "llm"}


# ----------------------------------------------------------------------------- fallback
def _understood(parsed: dict, f: Filters) -> list[str]:
    bits = []
    if parsed.get("area"):
        bits.append(f"محدودهٔ {parsed['area']}")
    if f.radius_km:
        bits.append(f"شعاع {fa(int(f.radius_km) if float(f.radius_km).is_integer() else f.radius_km) if f.radius_km >= 1 else fa(int(f.radius_km * 1000))} {'کیلومتر' if f.radius_km >= 1 else 'متر'}")
    if f.max_price:
        bits.append(f"سقف {fa(f'{f.max_price:,}')} تومان")
    if f.min_price:
        bits.append(f"حداقل {fa(f'{f.min_price:,}')} تومان")
    for k, lab in (("open_now", "باز در زمان مراجعه"), ("trabpay", "ترب‌پی"), ("installment", "اقساط"), ("fast", "ارسال فوری"), ("warranty", "گارانتی")):
        if getattr(f, k):
            bits.append(lab)
    return bits


def chat_rules(engine: Engine, text: str, ctx: dict) -> dict:
    cards: list = []
    state: dict = {}
    p = nlu.parse(text, engine.areas)
    args = {k: p[k] for k in ("max_price", "min_price", "radius_km") if k in p}
    args.update(p["flags"])
    if p.get("area"):
        args["area"] = p["area"]
    if p.get("sort"):
        args["sort"] = p["sort"]
    f = _filters_from(ctx, args, engine)
    if p["trip_hint"] and len(p["items"]) >= 2:
        res = run_tool(engine, "plan_trip", {**args, "items": p["items"]}, ctx, cards, state)
        plans = res["plans"]
        if not plans:
            reply = "برای هیچ‌کدام از این کالاها فروشنده‌ای در محدودهٔ فعلی پیدا نکردم؛ شعاع جستجو را بیشتر کنید."
        else:
            rec = next((x for x in plans if x["recommended"]), plans[0])
            reply = (f"برنامهٔ خرید {fa(len(p['items']))} کالا را چیدم. پیشنهاد من «{rec['title']}» است: {fa(rec['stops'])} توقف، "
                     f"حدود {fa(rec['minutes'])} دقیقه و جمعاً {rec['total']} (بر اساس قیمت‌های ثبت‌شده)." + (f" {rec['tradeoff']}." if rec.get("tradeoff") else ""))
            if res["unmet"]:
                reply += " برای این موارد فروشنده‌ای پیدا نشد: " + "، ".join(u["label"] for u in res["unmet"]) + "."
        return {"reply": reply, "cards": cards, "state": state, "mode": "rules"}
    q = p["query"] or text
    res = run_tool(engine, "search_products", {**args, "query": q}, ctx, cards, state)
    bits = _understood(p, f)
    if res["total_found"] == 0:
        reply = "چیزی با این شرایط پیدا نکردم. " + " ".join(res["hints"])
    else:
        t = res["top"][0]
        reply = (f"{fa(res['total_found'])} محصول مرتبط با «{q}» پیدا کردم. پیشنهاد اول: {t['title']} از {t['price_from'] or 'قیمت ثبت‌نشده'}؛ "
                 f"{t['best_shop']}{' (' + t['best_mall'] + ')' if t['best_mall'] else ''}، {t['nearest']} تا نزدیک‌ترین فروشنده — {t['best_hours']}. "
                 "قیمت‌ها ثبت‌شده‌اند و موجودی تأیید نشده؛ پیش از رفتن از فروشنده بپرسید.")
    if bits:
        reply = "فهمیدم: " + "، ".join(bits) + ". " + reply
    return {"reply": reply, "cards": cards, "state": state, "mode": "rules"}


def chat(engine: Engine, messages: list[dict], ctx: dict) -> dict:
    last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    if llm_enabled():
        try:
            return chat_llm(engine, [{"role": m["role"], "content": m["content"]} for m in messages[-12:]], ctx)
        except Exception as e:  # network / quota -> graceful degradation
            out = chat_rules(engine, last, ctx)
            out["note"] = f"LLM در دسترس نبود ({type(e).__name__}) و از تحلیل قاعده‌محور استفاده شد."
            return out
    return chat_rules(engine, last, ctx)
