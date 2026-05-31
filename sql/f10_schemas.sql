-- sql/f10_schemas.sql
-- F10 data tables in BigQuery, one per data type per market.

-- Financial statements
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_financials` (
    symbol STRING NOT NULL,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_financials` (
    symbol STRING NOT NULL,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

-- Valuation
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_valuation` (
    symbol STRING NOT NULL,
    valuation_type STRING,
    `interval` STRING,
    `date` DATE,
    value FLOAT64,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_valuation` (
    symbol STRING NOT NULL,
    valuation_type STRING,
    `interval` STRING,
    `date` DATE,
    value FLOAT64,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

-- Short interest
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_short_interest` (
    symbol STRING NOT NULL,
    data_type STRING,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_short_interest` (
    symbol STRING NOT NULL,
    data_type STRING,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

-- Capital flow
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_capital_flow` (
    symbol STRING NOT NULL,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_capital_flow` (
    symbol STRING NOT NULL,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

-- Analyst consensus
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_analyst` (
    symbol STRING NOT NULL,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_analyst` (
    symbol STRING NOT NULL,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

-- Shareholder data
CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.us_shareholder` (
    symbol STRING NOT NULL,
    data_type STRING,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;

CREATE TABLE IF NOT EXISTS `deductive-notch-495015-c2.quant.hk_shareholder` (
    symbol STRING NOT NULL,
    data_type STRING,
    data JSON,
    fetched_at TIMESTAMP,
    ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
) PARTITION BY DATE(ingest_time) CLUSTER BY symbol;
