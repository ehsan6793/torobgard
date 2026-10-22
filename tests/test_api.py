"""Smoke + behaviour tests. Run:  pytest -q   (needs data/torob.db)."""
import json
import pytest
from fastapi.testclient import TestClient

from app import agent, nlu, textnorm
from app.main import app, engine
from app.utils import hours_status, parse_when

c = TestClient(app)


def test_health():
    assert c.get("/api/health").json()["ok"]


def test_persian_normalisation_and_amounts():
    assert textnorm.normalize("كيف ۱۲۳") == "کیف 123"
    assert textnorm.parse_amount("زیر ۳ میلیون") == 3_000_000
    assert textnorm.parse_amount("دو و نیم میلیون") == 2_500_000
    assert textnorm.parse_amount("۵۰۰ هزار تومان") == 500_000


def test_nlu_extracts_filters():
    p = nlu.parse("دوربین مداربسته زیر ۳ میلیون نزدیک شیخ هادی الان باز باشه ترب پی", engine.areas)
    assert p["max_price"] == 3_000_000 and p["area"] == "شیخ هادی"
    assert p["flags"].get("open_now") and p["flags"].get("trabpay")
    assert p["query"] == "دوربین مداربسته"


def test_hours_ignore_placeholder_shift():
    h = json.dumps({d: {"status": "declared", "shifts": [{"start_time": "10:00:00", "end_time": "19:00:00"},
                                                          {"start_time": "00:00:00", "end_time": "00:00:00"}]}
                    for d in ["sat", "sun", "mon", "tue", "wed", "thu", "fri"]})
    assert hours_status(h, parse_when("2026-10-22T12:00:00"))["state"] == "open"
    assert hours_status(h, parse_when("2026-10-22T20:00:00"))["state"] == "closed"
    assert hours_status(h, parse_when("2026-10-22T18:40:00"))["state"] == "closing_soon"
    assert hours_status(None, parse_when("2026-10-22T12:00:00"))["state"] == "unknown"


def test_search_respects_radius_budget_and_transparency():
    r = c.get("/api/search", params={"q": "دوربین مداربسته", "radius_km": 2, "max_price": 3_000_000}).json()
    assert r["total"] > 0
    for it in r["items"]:
        b = it["best"]
        assert b["distance_km"] <= 2.0
        assert b["price"]["value"] <= 3_000_000
        assert b["stock"]["state"] == "unverified"          # never claim stock
        assert "age_days" in b["price_updated"]


def test_open_now_filter_uses_arrival_time():
    r = c.get("/api/search", params={"q": "ساعت مچی", "open_now": True, "when": "2026-10-22T21:30:00"}).json()
    for it in r["items"]:
        assert it["best"]["hours"]["state"] in ("open", "closing_soon")


def test_empty_result_explains_what_to_relax():
    r = c.get("/api/search", params={"q": "دوربین مداربسته", "radius_km": 0.5, "max_price": 1000}).json()
    assert r["total"] == 0 and r["hints"]


def test_product_detail_sorting_and_flags():
    s = c.get("/api/search", params={"q": "باتری قلمی", "radius_km": 0}).json()
    pid = s["items"][0]["id"]
    d = c.get(f"/api/product/{pid}", params={"radius_km": 0, "sort": "cheap"}).json()
    prices = [o["price"]["value"] for o in d["in_range"] if o["price"]["value"]]
    assert prices == sorted(prices) or len(prices) < 2


def test_trip_plan_has_stops_and_unmet():
    r = c.post("/api/trip", json={"items": [{"query": "باتری قلمی"}, {"query": "کابل شارژ"}, {"query": "xyzxyz"}],
                                 "filters": {"radius_km": 3}}).json()
    assert r["plans"] and r["unmet"] and r["unmet"][0]["label"] == "xyzxyz"
    assert sum(1 for p in r["plans"] if p.get("recommended")) == 1


def test_alternatives_exclude_given_shop():
    s = c.get("/api/search", params={"q": "باتری قلمی", "radius_km": 3}).json()
    it = s["items"][0]
    alt = c.post("/api/alternatives", json={"product_id": it["id"], "exclude_shop_ids": [it["best"]["shop"]["id"]],
                                            "filters": {"radius_km": 3}}).json()
    assert all(o["shop"]["id"] != it["best"]["shop"]["id"] for o in alt["same_product"])
    assert "similar_products" in alt


def test_chat_fallback_without_llm(monkeypatch):
    monkeypatch.setattr(agent, "LLM_KEY", "")
    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "ساعت مچی زیر ۲ میلیون"}]}).json()
    assert r["mode"] == "rules" and r["cards"][0]["type"] == "products"


def test_llm_tool_loop_with_stub(monkeypatch):
    """The agent must call tools (not invent data) and return their cards."""
    monkeypatch.setattr(agent, "LLM_KEY", "stub")
    calls = {"n": 0}

    def fake(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "t1", "type": "function", "function": {
                "name": "search_products", "arguments": json.dumps({"query": "ساعت مچی", "max_price": 2000000, "area": "بهارستان", "open_now": True})}}]}
        tool_msg = [m for m in messages if m["role"] == "tool"][-1]
        assert "total_found" in json.loads(tool_msg["content"])
        return {"role": "assistant", "content": "چند گزینه پیدا کردم."}

    monkeypatch.setattr(agent, "_llm_chat", fake)
    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "ساعت مچی ارزان نزدیک بهارستان"}]}).json()
    assert r["mode"] == "llm" and r["reply"] == "چند گزینه پیدا کردم."
    assert r["cards"][0]["type"] == "products"
    assert r["state"]["filters"]["max_price"] == 2_000_000 and r["state"]["filters"]["area_label"] == "بهارستان"


def test_llm_failure_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(agent, "LLM_KEY", "stub")
    def boom(messages): raise RuntimeError("network down")
    monkeypatch.setattr(agent, "_llm_chat", boom)
    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "ساعت مچی"}]}).json()
    assert r["mode"] == "rules" and "note" in r
