from typing import Callable


class MarketDataStream:
    def __init__(self):
        self._bar_callbacks: list[Callable] = []
        self._connected = False

    async def connect(self, symbols):
        self._connected = True

    async def disconnect(self):
        self._connected = False

    def on_bar(self, callback):
        self._bar_callbacks.append(callback)

    async def latest_bar(self, symbol):
        return {"symbol": symbol, "close": 100.0, "timestamp": None}
