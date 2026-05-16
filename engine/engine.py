from engine.portfolio import Portfolio
from engine.orders import Order, simulate_fills
from engine.strategy import StrategyContext
from engine.risk import RiskEngine


class Result:
    def __init__(self, portfolio, config, strategy_name=""):
        self.portfolio = portfolio
        self.config = config
        self.strategy_name = strategy_name


class Engine:
    def __init__(self, config):
        self.config = config

    def _signals_to_orders(self, signals, portfolio, bar_data=None):
        orders = []
        for sig in signals:
            if sig.side == "close" or sig.side == "sell":
                pos = portfolio.positions.get(sig.symbol)
                size = pos.size if pos and hasattr(pos, 'size') else 0
                orders.append(Order(symbol=sig.symbol, side="sell", size=size))
            elif sig.side == "buy" or sig.side == "target":
                weight = sig.weight or 1.0
                cash_per_symbol = portfolio.total_equity * weight
                close_prices = bar_data.get("close", {}) if bar_data else {}
                price_est = close_prices.get(sig.symbol, 100.0)
                size = max(1, int(cash_per_symbol / price_est))
                orders.append(Order(symbol=sig.symbol, side="buy", size=size))
        return orders

    def _simulate_fills(self, orders, bar_data):
        return simulate_fills(orders, bar_data, self.config)

    def run(self, strategy, data):
        portfolio = Portfolio(initial_capital=self.config.initial_capital)
        risk_engine = RiskEngine(strategy.risk_rules)
        ctx = StrategyContext(data=data, portfolio=portfolio, config=self.config)
        strategy.on_init(ctx)

        n_bars = len(data)
        for bar in range(n_bars):
            signals = strategy.on_bar(ctx, bar)
            if signals:
                bar_data = data.iloc(bar)
                orders = self._signals_to_orders(signals, portfolio, bar_data)
                orders = risk_engine.check(orders, portfolio, bar_data)
                fills = self._simulate_fills(orders, data.iloc(bar))
                portfolio.update(fills, data.iloc(bar))
            portfolio.mark_and_record(data.timestamp[bar], data.iloc(bar))

        return Result(portfolio=portfolio, config=self.config,
                      strategy_name=strategy.__class__.__name__)
