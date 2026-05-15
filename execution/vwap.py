import asyncio


class VWAPExecutor:
    def __init__(self, window_seconds: int = 1800, slices: int = 10, volume_profile=None):
        self.window = window_seconds
        self.slices = slices
        self.volume_profile = volume_profile

    async def run(self, signal, broker, market_data=None):
        qty = signal.get("qty", 100)
        symbol = signal.get("symbol", "AAPL")
        side = signal.get("side", "buy")
        interval = self.window / self.slices
        if self.volume_profile is not None and len(self.volume_profile) > 0:
            weights = self.volume_profile / self.volume_profile.sum()
            slice_qtys = [max(1, int(qty * w)) for w in weights[:self.slices]]
        else:
            slice_qtys = [max(1, qty // self.slices)] * self.slices
        orders = []
        for sq in slice_qtys:
            order = await broker.submit_order(symbol, side, sq)
            orders.append(order)
            await asyncio.sleep(interval)
        return orders
