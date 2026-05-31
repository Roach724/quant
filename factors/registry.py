import os
import logging
from datetime import datetime, timezone

import pandas as pd
from google.cloud import bigquery

logger = logging.getLogger(__name__)
PROJECT = os.environ.get("GCP_PROJECT", "deductive-notch-495015-c2")
DATASET = "quant"


class FactorRegistry:
    """Factor registration and query in BigQuery."""

    def __init__(self, project=PROJECT, dataset=DATASET):
        self.project = project
        self.dataset = dataset
        self._client = bigquery.Client(project=project)

    def register(
        self,
        factor_id,
        name,
        market,
        source=None,
        formula=None,
        category=None,
        description=None,
        tags=None,
    ):
        row = {
            "factor_id": factor_id,
            "name": name,
            "market": market,
            "category": category,
            "source": source,
            "formula": formula,
            "description": description,
            "is_active": True,
            "admitted_at": datetime.now(timezone.utc).isoformat(),
            "tags": tags or [],
        }
        table_ref = f"{self.project}.{self.dataset}.factor_registry"
        errors = self._client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error("Register failed for %s: %s", factor_id, errors)
            return False
        logger.info("Registered factor: %s", factor_id)
        return True

    def get_active(self, market="us"):
        query = f"""
            SELECT * FROM `{self.project}.{self.dataset}.factor_registry`
            WHERE market = @market AND is_active = TRUE
            ORDER BY latest_ic_mean DESC
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("market", "STRING", market)]
        )
        return self._client.query(query, job_config=job_config).to_dataframe()

    def deactivate(self, factor_id, reason=None):
        query = f"""
            UPDATE `{self.project}.{self.dataset}.factor_registry`
            SET is_active = FALSE
            WHERE factor_id = @factor_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("factor_id", "STRING", factor_id)]
        )
        job = self._client.query(query, job_config=job_config)
        job.result()
        logger.info("Deactivated factor: %s (reason: %s)", factor_id, reason)
        return True

    def evaluate(self, factor_id, factor_values, fwd_ret_1d, fwd_ret_5d,
                 fwd_ret_20d, eval_period_start=None, eval_period_end=None, force=False,
                 min_periods=30):
        """Evaluate a factor and write results to factor_evaluations table.

        Parameters
        ----------
        min_periods : int
            Minimum number of data points required for evaluation.
            Default 30 for daily factors; use 12 for quarterly fundamental factors.
        """
        from factors.evaluation import evaluate_factor as _eval

        if not force:
            latest = self._latest_eval_date(factor_id)
            if latest and (datetime.now(timezone.utc) - latest).days < 30:
                logger.info("Skipping %s: evaluated recently", factor_id)
                return None

        result = _eval(factor_values, fwd_ret_1d, fwd_ret_5d, fwd_ret_20d, min_periods=min_periods)
        eval_id = f"{factor_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        self._write_evaluation(
            eval_id=eval_id, factor_id=factor_id,
            eval_period_start=eval_period_start,
            eval_period_end=eval_period_end, **result,
        )
        self._update_registry_snapshot(
            factor_id, eval_id, result["ic_mean"],
            result["ic_tstat"], result["coverage"],
        )
        if not result["passes_admission"]:
            self.deactivate(factor_id, result["admission_details"])

        return result

    def _write_evaluation(self, eval_id, factor_id, ic_mean=None, ic_std=None,
                          ic_tstat=None, ic_ir=None, ic_decay_1d=None, ic_decay_5d=None,
                          ic_decay_20d=None, coverage=None, skewness=None, kurtosis=None,
                          max_correlation=None, passes_admission=False,
                          admission_details=None, eval_period_start=None,
                          eval_period_end=None):
        """Write evaluation result to factor_evaluations BQ table."""
        row = {
            "eval_id": eval_id,
            "factor_id": factor_id,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "ic_mean": ic_mean,
            "ic_std": ic_std,
            "ic_tstat": ic_tstat,
            "ic_ir": ic_ir,
            "ic_decay_1d": ic_decay_1d,
            "ic_decay_5d": ic_decay_5d,
            "ic_decay_20d": ic_decay_20d,
            "coverage": coverage,
            "skewness": skewness,
            "kurtosis": kurtosis,
            "max_correlation": max_correlation,
            "passes_admission": passes_admission,
            "admission_details": admission_details,
            "eval_period_start": eval_period_start,
            "eval_period_end": eval_period_end,
        }
        table_ref = f"{self.project}.{self.dataset}.factor_evaluations"
        errors = self._client.insert_rows_json(table_ref, [row])
        return not bool(errors)

    def _update_registry_snapshot(self, factor_id, eval_id, ic_mean, ic_tstat, coverage):
        """Update latest evaluation snapshot in factor_registry table."""
        query = f"""
            UPDATE `{self.project}.{self.dataset}.factor_registry`
            SET latest_ic_mean = @ic_mean,
                latest_ic_tstat = @ic_tstat,
                latest_coverage = @coverage,
                latest_eval_id = @eval_id,
                last_evaluated = @now
            WHERE factor_id = @factor_id
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("ic_mean", "FLOAT64", ic_mean),
            bigquery.ScalarQueryParameter("ic_tstat", "FLOAT64", ic_tstat),
            bigquery.ScalarQueryParameter("coverage", "FLOAT64", coverage),
            bigquery.ScalarQueryParameter("eval_id", "STRING", eval_id),
            bigquery.ScalarQueryParameter("now", "STRING", datetime.now(timezone.utc).isoformat()),
            bigquery.ScalarQueryParameter("factor_id", "STRING", factor_id),
        ])
        self._client.query(query, job_config=job_config).result()

    def _latest_eval_date(self, factor_id):
        """Get the most recent evaluation date for a factor."""
        query = f"""
            SELECT MAX(evaluated_at) as latest
            FROM `{self.project}.{self.dataset}.factor_evaluations`
            WHERE factor_id = @factor_id
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("factor_id", "STRING", factor_id),
        ])
        rows = list(self._client.query(query, job_config=job_config))
        return rows[0].latest if rows else None
