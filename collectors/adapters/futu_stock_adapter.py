"""Futu OpenD stock market adapter — HK (LV2) + US (LV3) equities."""

import os
import logging
import time as _time
from datetime import date, time, datetime
from typing import Optional

import pandas as pd
from futu import (
    OpenQuoteContext, RET_OK, AuType, KLType,
)

logger = logging.getLogger(__name__)


class FutuStockAdapter:
    """Futu OpenD stock market adapter for HK + US equities.

    Uses request_history_kline with pagination.
    Supports both HK (LV2) and US (LV3: NasBasic+TotalView+Arcabook).
    """

    market = "MIXED"  # resolved per-symbol in fetch_bars via prefix (HK./US.)

    _FREQ_MAP = {
        "1m": KLType.K_1M,
        "5m": KLType.K_5M,
        "15m": KLType.K_15M,
        "30m": KLType.K_30M,
        "1h": KLType.K_60M,
        "1d": KLType.K_DAY,
        "1w": KLType.K_WEEK,
    }

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        self.host = host or os.environ.get("OPEND_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("OPEND_PORT", "11111"))
        self._ctx: Optional[OpenQuoteContext] = None

    def _get_ctx(self) -> OpenQuoteContext:
        if self._ctx is None:
            self._ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self._ctx

    def _map_frequency(self, frequency: str):
        mapped = self._FREQ_MAP.get(frequency)
        if mapped is None:
            raise ValueError(f"Unsupported frequency: {frequency}")
        return mapped

    def _determine_autype(self, code: str) -> int:
        """HK stocks use QFQ (forward-adjusted), US uses NONE."""
        if code.startswith("HK."):
            return AuType.QFQ
        return AuType.NONE

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        frequency: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV bars via request_history_kline with pagination.

        Args:
            symbols: ["HK.00700", "HK.09988", "US.AAPL"] format
            start: start datetime
            end: end datetime
            frequency: "1m", "5m", "1d", etc.

        Returns:
            DataFrame with columns: symbol, timestamp, open, high, low,
            close, volume, market
        """
        ctx = self._get_ctx()
        ktype = self._map_frequency(frequency)
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        records = []
        for code in symbols:
            autype = self._determine_autype(code)
            page_key = None

            while True:
                ret, data, page_key = ctx.request_history_kline(
                    code, start=start_str, end=end_str,
                    ktype=ktype, autype=autype,
                    max_count=1000, page_req_key=page_key,
                )
                if ret != RET_OK:
                    logger.warning("Futu fetch failed for %s: %s", code, data)
                    break

                for _, row in data.iterrows():
                    records.append({
                        "symbol": code,
                        "timestamp": row["time_key"],
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(float(row["volume"])),
                        "market": "HK" if code.startswith("HK.") else "US",
                    })

                if page_key is None:
                    break

            # Rate limit: 60 req/30s → ~0.6s per symbol
            if len(symbols) > 50:
                _time.sleep(0.6)

        return pd.DataFrame(records)

    _DEFAULT_SYMBOLS = [
        # HK — 15 stocks (fallback only, SSOT is config/symbols.yaml)
        "HK.00700", "HK.09988", "HK.00941", "HK.00005", "HK.00388",
        "HK.01299", "HK.02318", "HK.01810", "HK.00883", "HK.02382",
        "HK.01093", "HK.03968", "HK.02269", "HK.03690", "HK.09633",
        # US — Nasdaq 100 + S&P 500 top 150 (deduplicated, ~239, fallback only)
        "US.AAPL","US.MSFT","US.NVDA","US.AMZN","US.META","US.GOOGL","US.AVGO","US.TSLA","US.COST","US.NFLX",
        "US.ADBE","US.AMD","US.PEP","US.CSCO","US.LIN","US.INTU","US.QCOM","US.TXN","US.AMGN","US.ISRG",
        "US.AMAT","US.CMCSA","US.HON","US.BKNG","US.GILD","US.MU","US.LRCX","US.ADI","US.VRTX","US.SBUX",
        "US.MDLZ","US.INTC","US.KLAC","US.REGN","US.SNPS","US.ADP","US.PANW","US.CDNS","US.MELI","US.ABNB",
        "US.ADSK","US.CRWD","US.FTNT","US.MAR","US.CTAS","US.ORLY","US.CSX","US.MRVL","US.NXPI","US.WDAY",
        "US.ROP","US.CEG","US.DASH","US.PCAR","US.MCHP","US.ROST","US.MNST","US.CPRT","US.AEP","US.KDP",
        "US.PAYX","US.KHC","US.ODFL","US.FAST","US.TTD","US.GEHC","US.IDXX","US.EXC","US.BKR","US.CTSH",
        "US.CCEP","US.DDOG","US.MRNA","US.TTWO","US.AZN","US.LULU","US.CDW","US.DXCM","US.TEAM",
        "US.BIIB","US.CHTR","US.DLTR",
        "US.XEL","US.CSGP","US.EA","US.ILMN","US.VRSK","US.GFS","US.BRK.B","US.JPM","US.V","US.UNH","US.WBD","US.PDD","US.ZS","US.FANG","US.MDB","US.ON",
        "US.XOM","US.MA","US.JNJ","US.WMT","US.PG","US.HD","US.BAC","US.CVX","US.ABBV","US.KO",
        "US.MRK","US.WFC","US.ORCL","US.CRM","US.PFE","US.DIS","US.IBM","US.CAT","US.GS","US.NEE",
        "US.T","US.VZ","US.RTX","US.MS","US.AXP","US.C","US.LOW","US.BLK","US.TMO","US.GE",
        "US.UPS","US.SPGI","US.NOW","US.UBER","US.BMY","US.SYK","US.CI","US.SCHW","US.ETN","US.ELV",
        "US.CB","US.BSX","US.MDT","US.PLD","US.DE","US.SO","US.TMUS","US.DUK",
        "US.ICE","US.MO","US.EQIX","US.WM","US.CME","US.PYPL","US.TT","US.SHW","US.WELL","US.ZTS",
        "US.PNC","US.USB","US.APH","US.EOG","US.BDX","US.ITW","US.PH","US.CL","US.FCX","US.LMT",
        "US.CVS","US.NOC","US.APD","US.TGT","US.MMM","US.EMR","US.AON","US.KKR","US.GD","US.HUM",
        "US.NKE","US.SLB","US.TDG","US.ECL","US.CARR","US.DHI","US.LEN","US.MCO","US.OXY","US.AZO",
        "US.F","US.PSA","US.JCI","US.HLT","US.KMB","US.NSC","US.MPC","US.TFC","US.AFL","US.GM",
        "US.MET","US.D","US.AIG","US.ALL","US.TRV","US.CP","US.WMB","US.LHX","US.SRE","US.PCG",
        "US.OKE","US.KMI","US.ED","US.VST","US.NRG","US.EIX","US.AWK","US.VLTO","US.AME","US.URI",
        "US.IR","US.XYL","US.OTIS","US.ROK","US.PWR","US.HWM","US.MLM","US.VMC","US.FTV","US.DOV",
        "US.GRMN","US.PPG","US.LYB","US.DD","US.DOW","US.HAL","US.NEM","US.DVN",
    ]

    @staticmethod
    def _load_symbols_from_yaml() -> list[str]:
        """Load symbols from config/symbols.yaml (SSOT).

        Falls back to _DEFAULT_SYMBOLS if the config file is unavailable.
        """
        from pathlib import Path
        import yaml

        config_paths = [
            Path(__file__).resolve().parent.parent.parent / "config" / "symbols.yaml",
            Path("/opt/quant/config/symbols.yaml"),
            Path("/opt/quant-dev/config/symbols.yaml"),
        ]
        for p in config_paths:
            if p.exists():
                try:
                    cfg = yaml.safe_load(p.read_text())
                    symbols = []
                    for market_cfg in cfg.get("markets", {}).values():
                        for sym in market_cfg.get("symbols", []):
                            prefix = f"{sym[:2].upper()}." if len(sym) > 2 else "HK."
                            # ws_collector format: "HK.00700", adapter format: "HK.00700"
                            symbols.append(sym)
                    if symbols:
                        import logging
                        logging.getLogger(__name__).info(
                            "Loaded %d symbols from %s", len(symbols), p
                        )
                        return symbols
                except Exception:
                    pass
        return []

    def fetch_supported_symbols(self) -> list[str]:
        """Return symbol list from SSOT (config/symbols.yaml), falling back to hardcoded list."""
        from_yaml = self._load_symbols_from_yaml()
        if from_yaml:
            return from_yaml
        return list(self._DEFAULT_SYMBOLS)

    def market_hours(self, d: date) -> tuple[time, time]:
        """Return trading hours.
        
        HK: 09:30-16:00, US: 09:30-16:00 ET
        TODO: Use get_market_state for dynamic hours.
        """
        return (time(9, 30), time(16, 0))

    def close(self):
        """Close the OpenD context."""
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
