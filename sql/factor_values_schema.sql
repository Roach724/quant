-- sql/factor_values_schema.sql
-- Unified factor values table — both TechFactorBuilder and FundamentalFactorBuilder
-- write values here. FactorRegistry queries this for evaluation.
-- ML Trainer loads from this table for model training.

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.factor_values` (
    factor_id STRING NOT NULL,
    symbol STRING NOT NULL,
    date DATE NOT NULL,
    value FLOAT64,
    source_builder STRING,  -- "tech" or "fundamental"
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY date
CLUSTER BY factor_id, symbol;
