"""DatasetManager — versioned GCS Parquet datasets from BigQuery factor_values.

Creates train/val/test Parquet splits with metadata, stored under
gs://{bucket}/datasets/{name}/ for downstream model training.
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import ClassVar

import pandas as pd
from google.cloud import bigquery
from google.cloud import storage

logger = logging.getLogger(__name__)


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class DatasetConfig:
    """Configuration for creating a dataset from BigQuery factor values."""

    market: str
    symbols: list[str] | str  # list of tickers or "all"
    features: list[str] | str  # list of factor names or "from_registry_top_N"
    label: str  # target column name (factor_id in BQ)
    train_range: tuple[str, str]  # (start_date, end_date) YYYY-MM-DD
    val_range: tuple[str, str]
    test_range: tuple[str, str]


@dataclass
class DatasetBundle:
    """A loaded dataset with metadata and train/val/test DataFrames."""

    name: str
    meta: dict
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame


# ── DatasetManager ───────────────────────────────────────────────────────────


class DatasetManager:
    """Create, load, and list versioned Parquet datasets on GCS.

    Datasets are stored as:
        gs://{bucket}/datasets/{name}/
            train.parquet
            val.parquet
            test.parquet
            meta.json
    """

    DEFAULT_BUCKET: ClassVar[str] = "deductive-notch-495015-c2-quant-data"
    DEFAULT_PROJECT: ClassVar[str] = "deductive-notch-495015-c2"
    DATASET: ClassVar[str] = "quant"
    TABLE: ClassVar[str] = "factor_values"
    PREFIX: ClassVar[str] = "datasets"

    # ── BigQuery / GCS clients ──────────────────────────────────────────

    @classmethod
    def _bq_client(cls) -> bigquery.Client:
        return bigquery.Client(project=cls.DEFAULT_PROJECT)

    @classmethod
    def _gcs_client(cls) -> storage.Client:
        return storage.Client(project=cls.DEFAULT_PROJECT)

    @classmethod
    def _bucket(cls, bucket_name: str | None = None) -> storage.Bucket:
        bucket_name = bucket_name or cls.DEFAULT_BUCKET
        return cls._gcs_client().bucket(bucket_name)

    # ── Symbol resolution ───────────────────────────────────────────────

    @classmethod
    def _all_symbols(cls, bq_client: bigquery.Client, market: str) -> list[str]:
        """Return all distinct symbols for *market* from factor_values."""
        query = """
            SELECT DISTINCT symbol
            FROM `{project}.{dataset}.{table}`
            WHERE STARTS_WITH(factor_id, @prefix)
            ORDER BY symbol
        """
        query = query.format(
            project=cls.DEFAULT_PROJECT, dataset=cls.DATASET, table=cls.TABLE
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("prefix", "STRING", f"{market}_"),
            ]
        )
        df = bq_client.query(query, job_config=job_config).to_dataframe()
        return df["symbol"].tolist() if not df.empty else []

    # ── Feature resolution ──────────────────────────────────────────────

    @classmethod
    def _top_features_from_registry(
        cls, bq_client: bigquery.Client, n: int, exclude: set[str] | None = None
    ) -> list[str]:
        """Return top *n* factor_ids ranked by absolute IC from factor_evaluations."""
        exclude = exclude or set()
        query = f"""
            WITH latest_eval AS (
                SELECT factor_id, ic_mean,
                       ROW_NUMBER() OVER (
                           PARTITION BY factor_id ORDER BY evaluated_at DESC
                       ) AS rn
                FROM `{cls.DEFAULT_PROJECT}.{cls.DATASET}.factor_evaluations`
            )
            SELECT factor_id
            FROM latest_eval
            WHERE rn = 1
            ORDER BY ABS(ic_mean) DESC
            LIMIT @n
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("n", "INT64", n)]
        )
        df = bq_client.query(query, job_config=job_config).to_dataframe()
        features = df["factor_id"].tolist() if not df.empty else []
        return [f for f in features if f not in exclude]

    @classmethod
    def _resolve_feature_names(
        cls,
        bq_client: bigquery.Client,
        features: list[str] | str,
        label: str,
        market: str,
    ) -> tuple[list[str], str]:
        """Resolve feature names and label to BQ factor_ids.

        - If *features* is ``"from_registry_top_N"``, query top N.
        - If a feature name does not exist in BQ, prepend ``{market}_`` and retry.
        - Returns (list_of_output_column_names, label_output_column_name).
        """
        if isinstance(features, str) and features.startswith("from_registry_top_"):
            try:
                n = int(features.split("_")[-1])
            except ValueError:
                n = 15
            raw = cls._top_features_from_registry(bq_client, n, exclude={label})
            # Strip market prefix for output column names
            output_features = []
            for fid in raw:
                if fid.startswith(f"{market}_"):
                    output_features.append(fid[len(market) + 1 :])
                else:
                    output_features.append(fid)
            if label.startswith(f"{market}_"):
                output_label = label[len(market) + 1 :]
            else:
                output_label = label
            return output_features, output_label

        # Direct feature list
        # Determine output column names (strip market prefix if present)
        output_features = []
        for f in features:
            if f.startswith(f"{market}_"):
                output_features.append(f[len(market) + 1 :])
            else:
                output_features.append(f)

        if label.startswith(f"{market}_"):
            output_label = label[len(market) + 1 :]
        else:
            output_label = label

        return output_features, output_label

    @classmethod
    def _resolve_factor_id(
        cls, bq_client: bigquery.Client, name: str, market: str
    ) -> str:
        """Find the matching factor_id in BQ for a feature/label name.

        Tries *name* exactly, then ``{market}_{name}``.  Falls back to *name*
        (the column will be all-NULL if the factor does not exist).
        """
        candidates = [name]
        if market and not name.startswith(f"{market}_"):
            candidates.append(f"{market}_{name}")

        # Quick existence check
        try:
            query = (
                f"SELECT DISTINCT factor_id FROM "
                f"`{cls.DEFAULT_PROJECT}.{cls.DATASET}.{cls.TABLE}` "
                f"WHERE factor_id IN UNNEST(@candidates) LIMIT 1"
            )
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("candidates", "STRING", candidates),
                ]
            )
            rows = list(
                bq_client.query(query, job_config=job_config).result()
            )
            if rows:
                return rows[0].factor_id
        except Exception:
            pass

        return name  # fallback — will produce NULL column

    # ── Query builder ───────────────────────────────────────────────────

    @classmethod
    def _query_factor_data(
        cls,
        bq_client: bigquery.Client,
        features: list[str],
        label: str,
        symbols: list[str],
        start: str,
        end: str,
        market: str,
    ) -> pd.DataFrame:
        """Query and pivot factor_values into a wide DataFrame.

        *features* and *label* must be **already-resolved** names (market
        prefix stripped).  Each is looked up via ``_resolve_factor_id`` to
        find the underlying BQ factor_id.
        """
        # features is already resolved — do NOT call _resolve_feature_names here.
        # Build CASE WHEN for each feature + label
        all_names = list(features) + [label]

        case_clauses: list[str] = []
        select_cols: list[str] = []
        for name in all_names:
            fid = cls._resolve_factor_id(bq_client, name, market)
            case_clauses.append(
                f"MAX(CASE WHEN factor_id = '{fid}' THEN value END) AS `{name}`"
            )
            select_cols.append(name)

        query = f"""
            SELECT symbol, date, {', '.join(case_clauses)}
            FROM `{cls.DEFAULT_PROJECT}.{cls.DATASET}.{cls.TABLE}`
            WHERE symbol IN UNNEST(@symbols)
              AND date BETWEEN @start AND @end
            GROUP BY symbol, date
            ORDER BY symbol, date
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("symbols", "STRING", symbols),
                bigquery.ScalarQueryParameter("start", "STRING", start),
                bigquery.ScalarQueryParameter("end", "STRING", end),
            ],
        )
        df = bq_client.query(query, job_config=job_config).to_dataframe()
        df["date"] = pd.to_datetime(df["date"])
        return df

    # ── Splitting ───────────────────────────────────────────────────────

    @classmethod
    def _split_by_date(
        cls,
        df: pd.DataFrame,
        train_range: tuple[str, str],
        val_range: tuple[str, str],
        test_range: tuple[str, str],
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Split a DataFrame into train/val/test by date ranges."""
        train_s, train_e = train_range
        val_s, val_e = val_range
        test_s, test_e = test_range

        train = df[(df["date"] >= train_s) & (df["date"] <= train_e)].copy()
        val = df[(df["date"] >= val_s) & (df["date"] <= val_e)].copy()
        test = df[(df["date"] >= test_s) & (df["date"] <= test_e)].copy()

        return train, val, test

    # ── Git helper ──────────────────────────────────────────────────────

    @staticmethod
    def _git_commit() -> str | None:
        """Return current git commit hash (short) or None."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    # ── Forward return computation ──────────────────────────────────────

    @classmethod
    def _compute_fwd_ret(
        cls,
        bq_client: bigquery.Client,
        symbols: list[str],
        start: str,
        end: str,
        n_days: int,
    ) -> "pd.DataFrame":
        """Compute forward return label from bars data.

        fwd_ret_Nd = close[t+N] / close[t] - 1
        Returns DataFrame with columns: symbol, date, fwd_ret_{N}d
        """
        import pandas as pd

        # Extend end date by n_days to have forward-looking prices
        end_ext = pd.Timestamp(end) + pd.Timedelta(days=n_days + 14)  # buffer for weekends
        end_ext_str = end_ext.strftime("%Y-%m-%d")

        query = f"""
            SELECT symbol, timestamp AS date, close
            FROM `{cls.DEFAULT_PROJECT}.{cls.DATASET}.us_bars_1d`
            WHERE symbol IN UNNEST(@symbols)
              AND timestamp BETWEEN @start AND @end_ext
            ORDER BY symbol, timestamp
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("symbols", "STRING", symbols),
                bigquery.ScalarQueryParameter("start", "STRING", start),
                bigquery.ScalarQueryParameter("end_ext", "STRING", end_ext_str),
            ],
        )
        bars = bq_client.query(query, job_config=job_config).to_dataframe()
        bars["date"] = pd.to_datetime(bars["date"])

        # Compute forward return per symbol
        bars = bars.sort_values(["symbol", "date"])
        bars["fwd_close"] = bars.groupby("symbol")["close"].shift(-n_days)
        label_name = f"fwd_ret_{n_days}d"
        bars[label_name] = bars["fwd_close"] / bars["close"] - 1.0

        result = bars[["symbol", "date", label_name]].copy()
        # Trim to original date range
        result = result[(result["date"] >= start) & (result["date"] <= end)]
        return result

    # ── Create ──────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        name: str,
        config: DatasetConfig,
        bucket: str | None = None,
    ) -> str:
        """Create a dataset from BigQuery, write Parquet + meta to GCS.

        Args:
            name: Unique dataset name (versioned, e.g. ``"us_v1"``).
            config: ``DatasetConfig`` specifying market, symbols, features, label,
                    and date ranges.
            bucket: GCS bucket name (defaults to ``DEFAULT_BUCKET``).

        Returns:
            The dataset *name*.
        """
        bq_client = cls._bq_client()
        bucket_obj = cls._bucket(bucket)

        # Resolve symbols
        if config.symbols == "all" or (
            isinstance(config.symbols, str) and config.symbols.lower() == "all"
        ):
            symbols = cls._all_symbols(bq_client, config.market)
            if not symbols:
                raise ValueError(
                    f"No symbols found for market={config.market} in factor_values"
                )
        else:
            symbols = list(config.symbols)

        # Resolve features once; pass resolved names to _query_factor_data
        output_features, output_label = cls._resolve_feature_names(
            bq_client, config.features, config.label, config.market
        )

        # Determine overall query range (union of all three)
        all_start = min(
            config.train_range[0],
            config.val_range[0],
            config.test_range[0],
        )
        all_end = max(
            config.train_range[1],
            config.val_range[1],
            config.test_range[1],
        )

        # Query all data in one shot
        logger.info(
            "Querying factor_values for %d symbols, %d features, %s → %s",
            len(symbols),
            len(output_features),
            all_start,
            all_end,
        )
        df = cls._query_factor_data(
            bq_client,
            output_features,
            output_label,
            symbols,
            all_start,
            all_end,
            config.market,
        )
        logger.info("Queried %d rows × %d columns", len(df), len(df.columns))

        # Compute forward-return labels from bars if needed
        import re
        fwd_match = re.match(r"fwd_ret_(\d+)d", output_label)
        if fwd_match and output_label in df.columns and df[output_label].isna().all():
            n_days = int(fwd_match.group(1))
            logger.info("Computing fwd_ret_%dd from bars data...", n_days)
            fwd_series = cls._compute_fwd_ret(bq_client, symbols, all_start, all_end, n_days)
            df = df.drop(columns=[output_label]).merge(
                fwd_series, on=["symbol", "date"], how="left"
            )
            logger.info("fwd_ret_%dd computed: %.1f%% non-null", n_days,
                        df[output_label].notna().mean() * 100)

        # Drop rows where label is NaN
        n_before = len(df)
        df = df.dropna(subset=[output_label])
        logger.info("Dropped %d rows with NaN label (before=%d, after=%d)",
                    n_before - len(df), n_before, len(df))

        # Split
        train, val, test = cls._split_by_date(
            df,
            config.train_range,
            config.val_range,
            config.test_range,
        )
        logger.info(
            "Split: train=%d  val=%d  test=%d",
            len(train),
            len(val),
            len(test),
        )

        # Build metadata
        meta = {
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "market": config.market,
            "symbols": symbols,
            "n_symbols": len(symbols),
            "features": output_features,
            "n_features": len(output_features),
            "label": output_label,
            "train_range": list(config.train_range),
            "val_range": list(config.val_range),
            "test_range": list(config.test_range),
            "train_rows": len(train),
            "val_rows": len(val),
            "test_rows": len(test),
            "factor_computed_at": None,
            "git_commit": cls._git_commit(),
        }

        # Write to GCS
        prefix = f"{cls.PREFIX}/{name}"
        _upload_parquet(bucket_obj, f"{prefix}/train.parquet", train)
        _upload_parquet(bucket_obj, f"{prefix}/val.parquet", val)
        _upload_parquet(bucket_obj, f"{prefix}/test.parquet", test)
        _upload_json(bucket_obj, f"{prefix}/meta.json", meta)

        logger.info("Dataset '%s' created at gs://%s/%s/", name, bucket_obj.name, prefix)
        return name

    # ── Load ────────────────────────────────────────────────────────────

    @classmethod
    def load(
        cls, name: str, bucket: str | None = None
    ) -> DatasetBundle:
        """Load a dataset from GCS.

        Returns:
            ``DatasetBundle`` with metadata and train/val/test DataFrames.
        """
        bucket_obj = cls._bucket(bucket)
        prefix = f"{cls.PREFIX}/{name}"

        meta = _download_json(bucket_obj, f"{prefix}/meta.json")
        train = _download_parquet(bucket_obj, f"{prefix}/train.parquet")
        val = _download_parquet(bucket_obj, f"{prefix}/val.parquet")
        test = _download_parquet(bucket_obj, f"{prefix}/test.parquet")

        return DatasetBundle(
            name=name,
            meta=meta,
            train=train,
            val=val,
            test=test,
        )

    # ── List / exists ───────────────────────────────────────────────────

    @classmethod
    def list_all(cls, bucket: str | None = None) -> list[dict]:
        """List all datasets with metadata summaries."""
        bucket_obj = cls._bucket(bucket)
        # Use delimiter="/" to get common prefixes (virtual directories)
        iterator = bucket_obj.list_blobs(
            prefix=f"{cls.PREFIX}/", delimiter="/"
        )
        # Force iteration to populate prefixes
        _ = list(iterator)

        datasets: list[dict] = []
        for prefix_str in iterator.prefixes:
            # prefix_str looks like "datasets/test_mini_v1/"
            ds_name = prefix_str.rstrip("/").split("/")[-1]
            if not ds_name:
                continue

            # Try to load metadata summary
            try:
                meta = _download_json(
                    bucket_obj, f"{cls.PREFIX}/{ds_name}/meta.json"
                )
                datasets.append(
                    {
                        "name": ds_name,
                        "created_at": meta.get("created_at"),
                        "market": meta.get("market"),
                        "n_symbols": meta.get("n_symbols"),
                        "n_features": meta.get("n_features"),
                        "label": meta.get("label"),
                        "train_rows": meta.get("train_rows"),
                        "val_rows": meta.get("val_rows"),
                        "test_rows": meta.get("test_rows"),
                    }
                )
            except Exception:
                datasets.append({"name": ds_name, "created_at": None})

        return datasets

    @classmethod
    def exists(cls, name: str, bucket: str | None = None) -> bool:
        """Check whether a dataset exists in GCS."""
        bucket_obj = cls._bucket(bucket)
        blob = bucket_obj.blob(f"{cls.PREFIX}/{name}/meta.json")
        return blob.exists()


# ── Internal GCS helpers ─────────────────────────────────────────────────────


def _upload_parquet(
    bucket_obj: storage.Bucket, blob_path: str, df: pd.DataFrame
) -> None:
    """Upload a DataFrame as Parquet to GCS."""
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        df.to_parquet(tmp.name, index=False)
        blob = bucket_obj.blob(blob_path)
        blob.upload_from_filename(tmp.name)
    logger.debug("Uploaded %s (%d rows)", blob_path, len(df))


def _upload_json(
    bucket_obj: storage.Bucket, blob_path: str, data: dict
) -> None:
    """Upload a dictionary as JSON to GCS."""
    blob = bucket_obj.blob(blob_path)
    blob.upload_from_string(json.dumps(data, indent=2, default=str), content_type="application/json")
    logger.debug("Uploaded %s", blob_path)


def _download_parquet(
    bucket_obj: storage.Bucket, blob_path: str
) -> pd.DataFrame:
    """Download a Parquet blob from GCS into a DataFrame."""
    blob = bucket_obj.blob(blob_path)
    data = blob.download_as_bytes()
    return pd.read_parquet(io.BytesIO(data))


def _download_json(
    bucket_obj: storage.Bucket, blob_path: str
) -> dict:
    """Download a JSON blob from GCS and parse it."""
    blob = bucket_obj.blob(blob_path)
    data = blob.download_as_bytes()
    return json.loads(data.decode("utf-8"))
