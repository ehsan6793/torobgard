"""Rule-based Persian NLU: used as the offline fallback of the LLM agent (and as a fast pre-parser)."""
from __future__ import annotations

import re

from .textnorm import normalize, parse_amount

_NUM = r"(?:\d[\d,\.]*|یک|یه|دو|سه|چهار|پنج|شش|شیش|هفت|هشت|نه|ده|بیست|سی|چهل|پنجاه|شصت|هفتاد|هشتاد|نود|صد|نیم)"
_MONEY = rf"({_NUM}(?:\s*و\s*نیم)?\s*(?:میلیارد|میلیون|هزار)(?:\s*و\s*{_NUM}\s*(?:هزار))?(?:\s*تومان|\s*تومن)?|\d{{4,}}(?:\s*تومان|\s*تومن)?)"

PATTERNS = [
    ("between", re.compile(rf"بین\s+{_MONEY}\s+(?:تا|و)\s+{_MONEY}")),
    ("max", re.compile(rf"(?:زیر|کمتر\s+از|ارزان\s*تر\s+از|ارزون\s*تر\s+از|تا|حداکثر|نهایتا|بودجه(?:\s+ام|\s+م)?(?:\s+حدود)?)\s*{_MONEY}")),
    ("min", re.compile(rf"(?:بالای|بیشتر\s+از|حداقل)\s*{_MONEY}")),
]
FLAG_WORDS = {
    "open_now": [r"الان\s+باز", r"باز\s+باشه", r"باز\s+باشد", r"بازه\b", r"همین\s+الان"],
    "trabpay": [r"ترب\s*پی", r"ترب‌پی"],
    "installment": [r"قسط", r"اقساط", r"قسطی"],
    "fast": [r"ارسال\s+فوری", r"پیک", r"فوری"],
    "warranty": [r"گارانتی"],
    "try_before_buy": [r"تست\s+و\s+تحویل", r"تست\s+کنم"],
}
SORT_WORDS = {"cheap": [r"ارزان\s*ترین", r"ارزونترین", r"کمترین\s+قیمت", r"ارزون"], "near": [r"نزدیک\s*ترین"]}
FILLER = [r"\bمیخوام\b", r"\bمی\s*خوام\b", r"\bبخرم\b", r"\bدنبال\b", r"\bلطفا\b", r"\bیه\b", r"\bیک\b", r"\bبرام\b",
          r"\bپیدا\s+کن\b", r"\bنشون\s+بده\b", r"\bدارم\b", r"\bلازم\b", r"\bنیاز\s+دارم\b", r"\bکجا\b", r"\bرو\b", r"\bرا\b",
          r"\bاطراف\b", r"\bنزدیک\b", r"\bتو\b", r"\bدر\b", r"\bحوالی\b", r"\bمحدوده\b", r"\bاز\b", r"\bکه\b", r"\bباشه\b", r"\bباشد\b", r"\bبشه\b", r"\bباز\b"]
TRIP_HINT = re.compile(r"برنامه\s+خرید|مسیر|لیست|همه\s*(?:شون|ی\s*اینا)|یه\s+جا|چند\s+فروشگاه|یک\s+جا|سفر\s+خرید|خرید\s+کنم\s+که")


def parse(text: str, areas: list[dict]) -> dict:
    text = text.replace("،", " و ").replace(",", " و ")
    t = normalize(text)
    out: dict = {"flags": {}}
    # area
    for a in sorted(areas, key=lambda a: -len(a["name"])):
        n = normalize(a["name"])
        if n and n in t:
            out["area"] = a["name"]
            out["lat"], out["lon"] = a["lat"], a["lon"]
            t = t.replace(n, " ")
            break
    # radius
    m = re.search(r"(\d+(?:\.\d+)?)\s*(کیلومتر|کیلو|متر)\b", t)
    if m:
        v = float(m.group(1))
        out["radius_km"] = v if m.group(2) != "متر" else v / 1000
        t = t.replace(m.group(0), " ")
    # prices
    for kind, rx in PATTERNS:
        m = rx.search(t)
        if m:
            if kind == "between":
                a, b = parse_amount(m.group(1)), parse_amount(m.group(2))
                if a and b:
                    out["min_price"], out["max_price"] = sorted((a, b))
            else:
                v = parse_amount(m.group(1))
                if v:
                    out["max_price" if kind == "max" else "min_price"] = v
            t = t.replace(m.group(0), " ")
    for key, pats in FLAG_WORDS.items():
        for p in pats:
            if re.search(p, t):
                out["flags"][key] = True
                t = re.sub(p, " ", t)
    for key, pats in SORT_WORDS.items():
        for p in pats:
            if re.search(p, t):
                out["sort"] = key
                t = re.sub(p, " ", t)
    out["trip_hint"] = bool(TRIP_HINT.search(t))
    t = TRIP_HINT.sub(" ", t)
    for f in FILLER:
        t = re.sub(f, " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # trip items: split on ' و ' when we have a trip hint
    parts = [p.strip() for p in re.split(r"\s+و\s+|،|,", t) if p.strip()]
    out["items"] = parts
    out["query"] = t
    return out
