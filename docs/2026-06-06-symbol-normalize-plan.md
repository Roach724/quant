# Symbol 格式统一修复计划

## 目标
所有模块处理 HK symbol 统一输出：**5 位补零无前缀** (如 `"00001"`, `"00700"`)
所有模块处理 US symbol 统一输出：**裸 ticker 无前缀** (如 `"AAPL"`, `"MSFT"`)

## 第零步：创建统一入口

### 新建 `common/normalize.py`
```python
def normalize_symbol(s: str, market: str) -> str:
    """Canonical symbol normalization.
    
    HK: strip prefix + zero-pad to 5 digits. HK.00005 / 0005 / 5 → "00005"
    US: strip prefix. US.AAPL / AAPL → "AAPL"
    """
    s = str(s)
    prefix = f"{market.upper()}."
    if s.startswith(prefix):
        s = s[len(prefix):]
    if market == "hk":
        s = s.lstrip("0") or "0"
        s = s.zfill(5)
    return s


def queryize_symbol(s: str, market: str) -> str:
    """Convert bare symbol to BQ query format (add market prefix)."""
    s = normalize_symbol(s, market)
    return f"{market.upper()}.{s}"
```

## 第一步：切断污染源头

| # | 文件 | 行号 | 改动 |
|---|------|------|------|
| 1 | `scripts/compute_daily_bars.py` | GROUP BY | 聚合前对 symbol 列做 `normalize_symbol` + 统一输出 `{market}.{normalized}` |

## 第二步：修复不对称 strip

| # | 文件 | 位置 | 当前代码 | 改为 |
|---|------|------|---------|------|
| 2 | `scripts/compute_factors_batch.py` | symbol处理 | `.str.replace("US.", "")` (只去US) | `normalize_symbol` |
| 3 | `engine/data.py` | `BigQuery5mSource` | `.str.replace("US.", "")` (只去US) | `normalize_symbol` |
| 4 | `ml/trainer.py:load_data_from_bq` | 行273-275 | `.str.replace("US.", "").str.replace("US_", "")` (只去US) | `normalize_symbol` |

## 第三步：修复过度 strip / 不一致 strip

| # | 文件 | 位置 | 当前代码 | 改为 |
|---|------|------|---------|------|
| 5 | `live/runner.py:_resolve_symbols` | 行265 | `s.replace(pre, "").lstrip("0")` | 对每个symbol调用 `normalize_symbol(s, market)` |
| 6 | `live/runner.py:_load_bq_data` | 行686/661 | `str.replace(regex)` + `str.lstrip("0")` | `normalize_symbol` |
| 7 | `live/bq_datasource.py:_poll` | 查询构造+结果处理 | `.lstrip("0").zfill(5)` + `.lstrip("0")` | `queryize_symbol` + `normalize_symbol` |

## 第四步：修复 admin/server.py MERGE

| # | 文件 | 位置 | 改动 |
|---|------|------|------|
| 8 | `admin/server.py` | `_generate_dataset_inner` MERGE SQL | bars 子查询 + ON 条件改用 `normalize_symbol` 逻辑（SQL 版用 `REGEXP_REPLACE(REPLACE(symbol, 'HK.', ''), r'^0+', '')` 再加 `LPAD(..., 5, '0')`） |

## 第五步：修复透传

| # | 文件 | 位置 | 改动 |
|---|------|------|------|
| 9 | `paper/market.py` | `from_bq()`, `from_static()` | 返回前对每个symbol调用 `normalize_symbol` |
| 10 | `admin/server.py` | `_generate_dataset_inner` CREATE TABLE | 数据集 pivot 时对 symbol 归一化 |

## 第六步：BQ 数据清洗

| # | 表 | 操作 |
|---|-----|------|
| 11 | `quant.hk_bars_1d` | UPDATE 归一化现有数据: `REPLACE(symbol, 'HK.', '')` → `LPAD(lstrip('0'), 5, '0')` → `CONCAT('HK.', ...)` |
| 12 | `quant.hk_bars_5m` | 同上 |

## 不动的地方

| 模块 | 原因 |
|------|------|
| `ml/datasets.py` | 已废弃，只有 legacy ml/tuner.py 引用 |
| `ml/tuner.py` | 已废弃，无活跃代码 import |
| `common/bq_writer.py` | 透传，取决于调用方 |
| `dashboard/observer.py` | 透传，symbol 来自 strategy |
| `live/experiment_manager.py` | 不处理 symbol |
| `admin/pipeline.py` | 已废弃 |
| `paper_run/` | 已废弃 |
| `run_paper.py` | 已废弃 |
| `scripts/train_*.py` ×3 | 已废弃 |
| `bigquery_loader/load_futu_factors.py` | 已废弃 |

## 影响评估

- **数据回填**: 修后新写入的数据格式统一，旧数据需第 11-12 步清洗
- **Paper 实验**: 修复后 HK paper run 不再有 symbol 不匹配
- **ML 训练**: 修复后基本面因子能正确 join 技术面因子
- **实时采集**: ws_collector 写入格式不变（已用 HK./US. 前缀），消费端归一化即可
- **Dashboard**: observer 透传，不受影响

## 执行顺序

```
Step 0 → Step 1-4 (代码) → Step 5-8 (修复) → Step 11-12 (数据清洗)
```

预计改动 12 处，约 4 个文件内部改动 + 1 个新文件。

## 补充：BQ 清洗时的去重

4 个表归一化后存在大量重复（同一 symbol 以不同格式写入过相同 timestamp）：

| 表 | 重复对数 | 重复行数 |
|-----|:--:|:--:|
| `us_bars_5m` | 170,944 | 379,915 |
| `hk_bars_1d` | 16,926 | 33,873 |
| `us_bars_1d` | 48 | 96 |
| `hk_bars_5m` | 0 | 0 |

### 清洗 + 去重策略

```sql
CREATE OR REPLACE TABLE `${table}_clean` AS
SELECT 
  CONCAT('${market}.', REGEXP_REPLACE(REPLACE(symbol, '${market}.', ''), r'^0+', LPAD('', 5, '0'))) AS symbol,
  timestamp, open, high, low, close, volume,
  _ingest_time
FROM (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY 
      REGEXP_REPLACE(REPLACE(symbol, '${market}.', ''), r'^0+', ''),
      timestamp
    ORDER BY _ingest_time DESC
  ) as rn
  FROM `${table}`
)
WHERE rn = 1
```

- 归一化 symbol → 按 (symbol, timestamp) 分组 → `ROW_NUMBER()` 去重，保留最新 `_ingest_time`
- US/hk_bars_1d 没有 `_ingest_time` 列 → 用 `MAX(close)` 或任意聚合

### 已纳入 Step 6 的 4 个表清洗
