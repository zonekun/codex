"""ブローカー基底クラス."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass
class Order:
    ticker: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: float | None = None  # LIMITの場合のみ


@dataclass
class OrderResult:
    order_id: str
    status: str
    filled_quantity: int
    filled_price: float
    message: str = ""


class BaseBroker(ABC):
    """証券会社APIの基底クラス."""

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult:
        """注文を発注する."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[dict]:
        """保有ポジションを取得する."""
        ...

    @abstractmethod
    async def get_balance(self) -> float:
        """口座残高を取得する."""
        ...
