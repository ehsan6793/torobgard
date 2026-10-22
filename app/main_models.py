"""Request models + filter construction (kept separate to avoid circular imports)."""
from __future__ import annotations

from pydantic import BaseModel

from .engine import Filters
from .utils import TEHRAN_BBOX


def mk_filters(lat=None, lon=None, radius_km=3.0, min_price=None, max_price=None, open_now=False, when=None,
               trabpay=False, installment=False, fast=False, warranty=False, try_before_buy=False, area=None) -> Filters:
    if lat is not None and lon is not None:
        b = TEHRAN_BBOX
        if not (b[0] <= lat <= b[1] and b[2] <= lon <= b[3]):
            lat = lon = None  # GPS outside Tehran -> fall back to default centre
    return Filters(lat, lon, radius_km, min_price, max_price, open_now, when, trabpay, installment, fast, warranty,
                   try_before_buy, area)


class FilterModel(BaseModel):
    lat: float | None = None
    lon: float | None = None
    radius_km: float = 3.0
    min_price: int | None = None
    max_price: int | None = None
    open_now: bool = False
    when: str | None = None
    trabpay: bool = False
    installment: bool = False
    fast: bool = False
    warranty: bool = False
    try_before_buy: bool = False
    area_label: str | None = None

    def to_filters(self) -> Filters:
        return mk_filters(self.lat, self.lon, self.radius_km, self.min_price, self.max_price, self.open_now, self.when,
                          self.trabpay, self.installment, self.fast, self.warranty, self.try_before_buy, self.area_label)
