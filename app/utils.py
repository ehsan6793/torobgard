"""Geo, time (Tehran), opening-hours and Jalali helpers."""
import json
import math
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TEHRAN = ZoneInfo("Asia/Tehran")
except Exception:  # pragma: no cover
    TEHRAN = timezone(timedelta(hours=3, minutes=30))

# Tehran bounding box used to sanity-check GPS coordinates
TEHRAN_BBOX = (35.55, 35.85, 51.20, 51.60)
DEFAULT_ORIGIN = {"lat": 35.6955, "lon": 51.4195, "label": "فردوسی"}

PY_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # index == datetime.weekday()
FA_DAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(n) -> str:
    return str(n).translate(FA_DIGITS)


def now_tehran() -> datetime:
    return datetime.now(TEHRAN)


def parse_when(s: str | None) -> datetime:
    """ISO string (local Tehran if naive) -> aware datetime; None -> now."""
    if not s:
        return now_tehran()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return now_tehran()
    if d.tzinfo is None:
        d = d.replace(tzinfo=TEHRAN)
    return d.astimezone(TEHRAN)


# ---------------------------------------------------------------- geo
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bbox(lat, lon, radius_km):
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.2))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def walk_minutes(km: float) -> int:
    """Street-detour factor 1.3, 4.5 km/h walking."""
    return max(1, round(km * 1.3 / 4.5 * 60))


def fmt_distance(km: float) -> str:
    if km < 1:
        return f"{fa(int(round(km * 1000 / 10) * 10))} متر"
    return f"{fa(round(km, 1))} کیلومتر"


# ---------------------------------------------------------------- jalali
def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def jalali_str(d: datetime) -> str:
    d = d.astimezone(TEHRAN)
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{fa(jd)} {JALALI_MONTHS[jm - 1]} {fa(jy)}"


# ---------------------------------------------------------------- opening hours
def _t(s: str) -> int:
    h, m = s.split(":")[:2]
    return int(h) * 60 + int(m)


def _day_intervals(day: dict | None):
    """-> (kind, [(start_min, end_min)]) kind in declared|closed|unknown|24h"""
    if not day:
        return "unknown", []
    st = day.get("status")
    if st == "closed":
        return "closed", []
    if st != "declared":
        return "unknown", []
    out = []
    for sh in day.get("shifts") or []:
        a, b = _t(sh["start_time"]), _t(sh["end_time"])
        if a == 0 and b == 0:
            continue  # placeholder shift in the dataset (00:00-00:00), carries no information
        if b <= a:
            b = 1440 if b == 0 else b + 1440  # ends at midnight / past-midnight shift
        out.append((a, min(b, 1440)))
    out.sort()
    return ("declared", out) if out else ("unknown", [])


def hhmm(minutes: int) -> str:
    minutes %= 1440
    return fa(f"{minutes // 60:02d}:{minutes % 60:02d}")


def hours_status(hours_json: str | None, when: datetime) -> dict:
    """Opening state of a shop at `when`.

    state: open | closing_soon | closed | unknown  (+ human Persian label)
    """
    if not hours_json:
        return {"state": "unknown", "label": "ساعت کاری ثبت نشده"}
    try:
        week = json.loads(hours_json)
    except Exception:
        return {"state": "unknown", "label": "ساعت کاری ثبت نشده"}
    when = when.astimezone(TEHRAN)
    wd = when.weekday()
    cur = when.hour * 60 + when.minute
    kind, iv = _day_intervals(week.get(PY_DAYS[wd]))
    if kind == "unknown":
        return {"state": "unknown", "label": "ساعت کاری امروز نامشخص"}
    if kind == "24h":
        return {"state": "open", "label": "شبانه‌روزی (طبق اعلام فروشنده)", "minutes_left": 1440, "closes": None}
    for a, b in iv:
        if a <= cur < b:
            left = b - cur
            st = "closing_soon" if left <= 45 else "open"
            lab = f"باز است تا {hhmm(b)}"
            if st == "closing_soon":
                lab = f"به‌زودی بسته می‌شود ({fa(left)} دقیقه دیگر، {hhmm(b)})"
            return {"state": st, "label": lab, "minutes_left": left, "closes": hhmm(b)}
    # closed now -> find next opening
    for a, b in iv:
        if cur < a:
            return {"state": "closed", "label": f"بسته است؛ امروز {hhmm(a)} باز می‌شود", "opens": hhmm(a), "opens_day": "امروز"}
    for off in range(1, 8):
        d2 = (wd + off) % 7
        k2, iv2 = _day_intervals(week.get(PY_DAYS[d2]))
        if k2 in ("declared", "24h") and iv2:
            a = iv2[0][0]
            day = "فردا" if off == 1 else FA_DAYS[d2]
            return {"state": "closed", "label": f"بسته است؛ {day} {hhmm(a)} باز می‌شود", "opens": hhmm(a), "opens_day": day}
    return {"state": "closed", "label": "بسته است"}


def week_table(hours_json: str | None):
    if not hours_json:
        return []
    try:
        week = json.loads(hours_json)
    except Exception:
        return []
    order = [5, 6, 0, 1, 2, 3, 4]  # Sat..Fri
    rows = []
    for i in order:
        kind, iv = _day_intervals(week.get(PY_DAYS[i]))
        if kind == "closed":
            txt = "تعطیل"
        elif kind == "unknown":
            txt = "نامشخص"
        elif kind == "24h":
            txt = "۲۴ ساعته"
        else:
            txt = " و ".join(f"{hhmm(a)}–{hhmm(b)}" for a, b in iv)
        rows.append({"day": FA_DAYS[i], "text": txt, "weekday": i})
    return rows
