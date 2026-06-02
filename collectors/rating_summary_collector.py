"""Collect analyst rating summaries from Futu API — US only, paginated."""

import logging

import pandas as pd
from futu import RET_OK

from collectors.futu_collector_base import FutuCollector

logger = logging.getLogger(__name__)


class RatingSummaryCollector(FutuCollector):
    """Paginates through institution and analyst rating summaries."""

    INSTITUTION = 1
    ANALYST = 2

    MAX_PAGES = 50  # safety limit

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _label(dimension: int) -> str:
        return "institution" if dimension == 1 else "analyst"

    @staticmethod
    def _list_key(dimension: int) -> str:
        return "inst_rating_summary_list" if dimension == 1 else "analyst_rating_summary_list"

    @staticmethod
    def _info_key(dimension: int) -> str:
        return "institution_info" if dimension == 1 else "analyst_info"

    @staticmethod
    def _is_done(next_key: str | None) -> bool:
        """Check if pagination cursor signals end-of-data."""
        if next_key is None:
            return False
        return str(next_key) in ("", "-1")

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    def _paginate(self, symbol: str, dimension: int) -> pd.DataFrame:
        """Fetch all pages of rating summaries for *dimension*.

        Parameters
        ----------
        dimension : int
            1 = institution, 2 = analyst.
        """
        list_key = self._list_key(dimension)
        info_key = self._info_key(dimension)
        label = self._label(dimension)

        all_rows: list[dict] = []
        next_key: str | None = None
        page = 0

        while page < self.MAX_PAGES:
            self._rate_limit()
            ctx = self._get_ctx()
            ret, data = ctx.get_research_rating_summary(
                symbol,
                rating_dimension_type=dimension,
                next_key=next_key,
                num=20,
            )
            if ret != RET_OK:
                logger.warning(
                    "get_research_rating_summary failed for %s dim=%s: %s",
                    symbol,
                    label,
                    data,
                )
                break

            if not isinstance(data, dict):
                break

            entries = data.get(list_key, [])
            if not isinstance(entries, list) or not entries:
                break

            for entry in entries:
                info = entry.get(info_key, {})
                rating_items = entry.get("rating_item_list", [])
                if not isinstance(rating_items, list):
                    continue
                for item in rating_items:
                    row: dict = {}
                    row.update(info)
                    row.update(item)
                    row["symbol"] = symbol
                    row["dimension"] = label
                    all_rows.append(row)

            next_key = data.get("next_key")
            if self._is_done(next_key):
                break
            page += 1

        if not all_rows:
            return pd.DataFrame()
        return pd.DataFrame(all_rows)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect_one(self, symbol: str) -> pd.DataFrame | None:
        """Collect institution + analyst rating summaries for *symbol*."""
        inst = self._paginate(symbol, self.INSTITUTION)
        analyst = self._paginate(symbol, self.ANALYST)
        if inst.empty and analyst.empty:
            return None
        frames = [df for df in (inst, analyst) if not df.empty]
        return pd.concat(frames, ignore_index=True)
