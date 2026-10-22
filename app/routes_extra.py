from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import agent
from .trip import find_alternatives, plan_trip

router = APIRouter(prefix="/api")


def _engine():
    from .main import engine
    return engine


class TripItem(BaseModel):
    product_id: str | None = None
    query: str | None = None
    label: str | None = None


from .main_models import FilterModel  # noqa: E402


class TripReq(BaseModel):
    items: list[TripItem]
    filters: FilterModel = Field(default_factory=FilterModel)
    max_stops: int = 4


@router.post("/trip")
def trip(req: TripReq):
    f = req.filters.to_filters()
    items = [i.model_dump(exclude_none=True) for i in req.items if i.product_id or (i.query or "").strip()]
    return plan_trip(_engine(), items, f, req.max_stops)


class AltReq(BaseModel):
    product_id: str
    exclude_shop_ids: list[str] = []
    ref_price: int | None = None
    filters: FilterModel = Field(default_factory=FilterModel)


@router.post("/alternatives")
def alternatives(req: AltReq):
    from fastapi import HTTPException
    d = find_alternatives(_engine(), req.product_id, req.filters.to_filters(), req.exclude_shop_ids, ref_price=req.ref_price)
    if not d:
        raise HTTPException(404, "محصول پیدا نشد")
    return d


class Msg(BaseModel):
    role: str
    content: str


class ChatReq(BaseModel):
    messages: list[Msg]
    filters: FilterModel = Field(default_factory=FilterModel)


@router.post("/chat")
def chat(req: ChatReq):
    ctx = req.filters.model_dump(exclude_none=True)
    return agent.chat(_engine(), [m.model_dump() for m in req.messages], ctx)


@router.get("/understand")
def understand(text: str):
    """Rule-based parse of a free-text request into query + filters (used by the search bar)."""
    from . import nlu
    e = _engine()
    p = nlu.parse(text, e.areas)
    return {"query": p["query"], "items": p["items"], "trip_hint": p["trip_hint"], "area": p.get("area"),
            "lat": p.get("lat"), "lon": p.get("lon"), "radius_km": p.get("radius_km"),
            "min_price": p.get("min_price"), "max_price": p.get("max_price"), "flags": p["flags"], "sort": p.get("sort")}
