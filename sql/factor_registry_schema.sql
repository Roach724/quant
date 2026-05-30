-- Factor Registry Schema
CREATE TABLE IF NOT EXISTS quant.factor_registry (
    factor_id      STRING    NOT NULL,
    name           STRING    NOT NULL,
    market         STRING    NOT NULL,
    category       STRING,
    source         STRING,
    formula        STRING,
    description    STRING,
    is_active      BOOL      DEFAULT TRUE,
    admitted_at    TIMESTAMP,
    last_evaluated TIMESTAMP,
    created_by     STRING,
    latest_ic_mean     FLOAT64,
    latest_ic_tstat    FLOAT64,
    latest_coverage    FLOAT64,
    latest_eval_id     STRING,
    tags           ARRAY<STRING>,
    metadata       JSON
)
PARTITION BY DATE(admitted_at)
CLUSTER BY market, is_active;

CREATE TABLE IF NOT EXISTS quant.factor_evaluations (
    eval_id        STRING    NOT NULL,
    factor_id      STRING    NOT NULL,
    evaluated_at   TIMESTAMP NOT NULL,
    ic_mean        FLOAT64,
    ic_std         FLOAT64,
    ic_tstat       FLOAT64,
    ic_ir          FLOAT64,
    ic_decay_1d    FLOAT64,
    ic_decay_5d    FLOAT64,
    ic_decay_20d   FLOAT64,
    coverage       FLOAT64,
    skewness       FLOAT64,
    kurtosis       FLOAT64,
    top_correlated   ARRAY<STRING>,
    max_correlation  FLOAT64,
    passes_admission  BOOL,
    admission_details STRING,
    eval_period_start  DATE,
    eval_period_end    DATE,
    eval_market        STRING,
    data_version       STRING,
    metadata  JSON
)
PARTITION BY DATE(evaluated_at)
CLUSTER BY factor_id;
