import asyncio
import random
from datetime import datetime, timezone


class TWAPExecutor:
    def __init__(self, window_seconds: int = 1800, slices: int = 10, randomize: bool = True):
        self.window = window_seconds
        self.slices = slices
        self.randomize = randomize

    async def run(self, signal, broker, market_data=None):
        qty = signal.get("qty", 100)
        symbol = signal.get("symbol", "AAPL")
        side = signal.get("side", "buy")
        qty_per_slice = max(1, qty // self.slices)
        interval = self.window / self.slices
        orders = []
        for i in range(self.slices):
            order = await broker.submit_order(symbol, side, qty_per_slice)
            orders.append(order)
            if self.randomize:
                jitter = random.uniform(-0.15, 0.15) * interval
            else:
                jitter = 0
            await asyncio.sleep(interval + jitter)
        return orders
