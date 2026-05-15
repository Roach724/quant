from typing import Protocol


class ExecutionAlgo(Protocol):
    async def run(self, signal, broker, market_data) -> list: ...
