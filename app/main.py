"""FastAPI app: REST API + static mobile web UI."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .engine import Engine
from .main_models import FilterModel, mk_filters  # noqa: F401
from .utils import DEFAULT_ORIGIN, jalali_str, now_tehran

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = os.environ.get("TOROB_DB", str(ROOT / "data" / "torob.db"))

app = FastAPI(title="دستیار خرید حضوری تهران", version="1.0")
app.add_middleware(GZipMiddleware, minimum_size=800)
engine = Engine(DB_PATH)


def qfilters(lat, lon, radius_km, min_price, max_price, open_now, when, trabpay, installment, fast, warranty, try_before_buy):
    return mk_filters(lat, lon, radius_km, min_price, max_price, open_now, when, trabpay, installment, fast, warranty, try_before_buy)


@app.get("/api/config")
def config():
    from .agent import llm_enabled
    return {
        "areas": engine.areas,
        "default_origin": DEFAULT_ORIGIN,
        "snapshot_jalali": jalali_str(engine.snapshot),
        "snapshot_iso": engine.snapshot.isoformat(),
        "now_iso": now_tehran().isoformat(),
        "llm": llm_enabled(),
        "stats": {"shops": len(engine.shops), "shops_with_offers": int(engine.has_offers.sum())},
    }


@app.get("/api/search")
def search(q: str = "", lat: float | None = None, lon: float | None = None, radius_km: float = 3.0,
           min_price: int | None = None, max_price: int | None = None, open_now: bool = False, when: str | None = None,
           trabpay: bool = False, installment: bool = False, fast: bool = False, warranty: bool = False,
           try_before_buy: bool = False, sort: str = "relevance", limit: int = Query(20, le=50), offset: int = 0):
    f = qfilters(lat, lon, radius_km, min_price, max_price, open_now, when, trabpay, installment, fast, warranty, try_before_buy)
    if not q.strip():
        return {"query": q, "total": 0, "items": [], "hints": ["چیزی که دنبالش هستید را بنویسید."], "filters": f.to_dict()}
    return engine.search(q, f, sort, limit, offset)


@app.get("/api/product/{product_id}")
def product(product_id: str, lat: float | None = None, lon: float | None = None, radius_km: float = 3.0,
            min_price: int | None = None, max_price: int | None = None, open_now: bool = False, when: str | None = None,
            trabpay: bool = False, installment: bool = False, fast: bool = False, warranty: bool = False,
            try_before_buy: bool = False, sort: str = "best"):
    f = qfilters(lat, lon, radius_km, min_price, max_price, open_now, when, trabpay, installment, fast, warranty, try_before_buy)
    d = engine.product_detail(product_id, f, sort)
    if not d:
        raise HTTPException(404, "محصول پیدا نشد")
    return d


@app.get("/api/shop/{shop_id}")
def shop(shop_id: str, q: str | None = None, lat: float | None = None, lon: float | None = None, when: str | None = None):
    f = mk_filters(lat, lon, 0, when=when)
    d = engine.shop_detail(shop_id, f, q)
    if not d:
        raise HTTPException(404, "فروشگاه پیدا نشد")
    return d


from .routes_extra import router as extra_router  # noqa: E402

app.include_router(extra_router)

STATIC = ROOT / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health():
    return {"ok": True, "shops": len(engine.shops)}
