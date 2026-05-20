"""Unit tests for RouterOrderManager."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from oms.broker import RouterOrderManager, BrokerOrder, BrokerPosition, BrokerAccount


def _make_mock_broker():
    """Create a mock broker with all AsyncMock protocol methods."""
    broker = MagicMock()
    broker.submit_order = AsyncMock()
    broker.cancel_order = AsyncMock()
    broker.get_order = AsyncMock()
    broker.get_positions = AsyncMock()
    broker.get_account = AsyncMock()
    broker.get_open_orders = AsyncMock()
    return broker


@pytest.fixture
def stock_broker():
    return _make_mock_broker()


@pytest.fixture
def crypto_broker():
    return _make_mock_broker()


@pytest.fixture
def fallback_broker():
    return _make_mock_broker()


@pytest.fixture
def router(stock_broker, crypto_broker, fallback_broker):
    return RouterOrderManager(stock_broker, crypto_broker, fallback_broker)


@pytest.fixture
def router_no_fallback(stock_broker, crypto_broker):
    return RouterOrderManager(stock_broker, crypto_broker)


class TestBrokerFor:
    """Tests for _broker_for symbol routing logic."""

    def test_broker_for_hk(self, router, stock_broker):
        assert router._broker_for("HK.00700") is stock_broker

    def test_broker_for_us(self, router, stock_broker):
        assert router._broker_for("US.AAPL") is stock_broker

    def test_broker_for_crypto_slash(self, router, crypto_broker):
        assert router._broker_for("BTC/USDT") is crypto_broker

    def test_broker_for_crypto_prefix_with_fallback(self, router, fallback_broker):
        assert router._broker_for("CRYPTO_BTC") is fallback_broker

    def test_broker_for_crypto_prefix_no_fallback(self, router_no_fallback, crypto_broker):
        assert router_no_fallback._broker_for("CRYPTO_BTC") is crypto_broker

    def test_broker_for_cc_prefix(self, router, crypto_broker):
        assert router._broker_for("CC.BTCUSDT") is crypto_broker

    def test_broker_for_unknown(self, router):
        with pytest.raises(ValueError, match="Unknown symbol prefix"):
            router._broker_for("UNKNOWN.xyz")


class TestSubmitOrder:
    """Tests for submit_order routing."""

    @pytest.mark.asyncio
    async def test_submit_order_routes_to_stock(self, router, stock_broker):
        expected = BrokerOrder(broker_id="s1", symbol="HK.00700", side="buy", qty=100)
        stock_broker.submit_order.return_value = expected

        result = await router.submit_order("HK.00700", "buy", 100)

        stock_broker.submit_order.assert_awaited_once_with(
            "HK.00700", "buy", 100,
            order_type="market", limit_price=None,
        )
        assert result is expected

    @pytest.mark.asyncio
    async def test_submit_order_routes_to_crypto(self, router, crypto_broker):
        expected = BrokerOrder(broker_id="c1", symbol="BTC/USDT", side="sell", qty=0.5)
        crypto_broker.submit_order.return_value = expected

        result = await router.submit_order("BTC/USDT", "sell", 0.5)

        crypto_broker.submit_order.assert_awaited_once_with(
            "BTC/USDT", "sell", 0.5,
            order_type="market", limit_price=None,
        )
        assert result is expected


class TestGetPositions:
    """Tests for position aggregation."""

    @pytest.mark.asyncio
    async def test_get_positions_aggregates(self, router, stock_broker, crypto_broker):
        pos_s = BrokerPosition(symbol="HK.00700", qty=100, avg_entry_price=300,
                               market_value=31000, unrealized_pnl=1000)
        pos_c = BrokerPosition(symbol="BTC/USDT", qty=0.5, avg_entry_price=60000,
                               market_value=31000, unrealized_pnl=1000)
        stock_broker.get_positions.return_value = [pos_s]
        crypto_broker.get_positions.return_value = [pos_c]

        positions = await router.get_positions()

        assert len(positions) == 2
        assert pos_s in positions
        assert pos_c in positions

    @pytest.mark.asyncio
    async def test_get_positions_skips_none_brokers(self, router_no_fallback):
        """With no fallback, shouldn't break (fallback is None)."""
        router_no_fallback._stock_broker.get_positions.return_value = []
        router_no_fallback._crypto_broker.get_positions.return_value = []

        positions = await router_no_fallback.get_positions()

        assert positions == []


class TestGetOpenOrders:
    """Tests for open orders aggregation."""

    @pytest.mark.asyncio
    async def test_get_open_orders_aggregates(self, router, stock_broker, crypto_broker):
        o1 = BrokerOrder(broker_id="o1", symbol="HK.00700", side="buy", qty=100, status="pending")
        o2 = BrokerOrder(broker_id="o2", symbol="BTC/USDT", side="sell", qty=0.5, status="pending")
        stock_broker.get_open_orders.return_value = [o1]
        crypto_broker.get_open_orders.return_value = [o2]

        orders = await router.get_open_orders()

        assert len(orders) == 2
        assert o1 in orders
        assert o2 in orders


class TestGetOrder:
    """Tests for get_order trying all brokers."""

    @pytest.mark.asyncio
    async def test_get_order_tries_all(self, router, stock_broker, crypto_broker):
        expected = BrokerOrder(broker_id="xyz", symbol="BTC/USDT", side="buy", qty=1.0)
        stock_broker.get_order.return_value = None
        crypto_broker.get_order.return_value = expected

        result = await router.get_order("xyz")

        stock_broker.get_order.assert_awaited_once_with("xyz")
        crypto_broker.get_order.assert_awaited_once_with("xyz")
        assert result is expected

    @pytest.mark.asyncio
    async def test_get_order_not_found(self, router, stock_broker, crypto_broker, fallback_broker):
        stock_broker.get_order.return_value = None
        crypto_broker.get_order.return_value = None
        fallback_broker.get_order.return_value = None

        result = await router.get_order("missing")

        assert result is None


class TestCancelOrder:
    """Tests for cancel_order trying all brokers."""

    @pytest.mark.asyncio
    async def test_cancel_order_tries_all(self, router, stock_broker, crypto_broker):
        stock_broker.cancel_order.return_value = False
        crypto_broker.cancel_order.return_value = True

        result = await router.cancel_order("xyz")

        stock_broker.cancel_order.assert_awaited_once_with("xyz")
        crypto_broker.cancel_order.assert_awaited_once_with("xyz")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_order_not_found(self, router, stock_broker, crypto_broker, fallback_broker):
        stock_broker.cancel_order.return_value = False
        crypto_broker.cancel_order.return_value = False
        fallback_broker.cancel_order.return_value = False

        result = await router.cancel_order("missing")

        assert result is False


class TestGetAccount:
    """Tests for get_account delegation."""

    @pytest.mark.asyncio
    async def test_get_account_returns_stock(self, router, stock_broker):
        expected = BrokerAccount(cash=50000, equity=70000, buying_power=140000)
        stock_broker.get_account.return_value = expected

        result = await router.get_account()

        stock_broker.get_account.assert_awaited_once()
        assert result is expected
