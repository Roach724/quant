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
