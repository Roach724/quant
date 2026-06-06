# Symbol 格式统一修复计划

## 目标
所有模块处理 HK symbol 统一输出：**5 位补零无前缀** (如 `"00001"`, `"00700"`)
所有模块处理 US symbol 统一输出：**裸 ticker 无前缀** (如 `"AAPL"`, `"MSFT"`)

---

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

---

## 第一步：切断污染源头

| # | 文件 | 市场 | 改动 |
|---|------|:--:|------|
| 1 | `scripts/compute_daily_bars.py` | 🇺🇸🇭🇰 | 聚合前对 symbol 归一化 + 统一输出 `{market}.{normalized}` |

## 第二步：修复不对称 strip

| # | 文件 | 市场 | 当前代码 | 改为 |
|---|------|:--:|---------|------|
| 2 | `scripts/compute_factors_batch.py` | 🇭🇰 | `.str.replace("US.", "")` (只去US) | `normalize_symbol` |
| 3 | `engine/data.py:BigQuery5mSource` | 🇭🇰 | `.str.replace("US.", "")` (只去US) | `normalize_symbol` |
| 4 | `ml/trainer.py:load_data_from_bq` | 🇭🇰 | `.str.replace("US.", "").str.replace("US_", "")` (只去US) | `normalize_symbol` |
| 5 | `ml/trainer.py:load_from_bq` | 🇭🇰 | strip prefix 但不补零 | `normalize_symbol` (防御性，数据清洗后也应加) |

## 第三步：修复过度 strip

| # | 文件 | 市场 | 当前代码 | 改为 |
|---|------|:--:|---------|------|
| 6 | `live/runner.py:_resolve_symbols` | 🇭🇰 | `.replace(pre, "").lstrip("0")` | `normalize_symbol(s, market)` |
| 7 | `live/runner.py:_load_bq_data` | 🇭🇰 | regex replace + `.str.lstrip("0")` | `normalize_symbol` |
| 8 | `live/bq_datasource.py:_poll` | 🇭🇰 | `.lstrip("0").zfill(5)` + `.lstrip("0")` | `queryize_symbol` + `normalize_symbol` |

## 第四步：SQL 归一化

| # | 文件 | 市场 | 改动 |
|---|------|:--:|------|
| 9 | `admin/server.py:_generate_dataset_inner` MERGE | 🇺🇸🇭🇰 | ON 条件 + bars 子查询统一归一化 |

## 第五步：修复透传

| # | 文件 | 市场 | 改动 |
|---|------|:--:|------|
| 10 | `paper/market.py:from_bq/from_static` | 🇭🇰 | 返回前对每个 symbol 调用 `normalize_symbol` |
| 11 | `admin/server.py:_generate_dataset_inner` CREATE TABLE | 🇺🇸🇭🇰 | 数据集 pivot 时 symbol 归一化 |

## 第六步：BQ 数据清洗 + 去重

4 个表归一化后存在大量重复：

| 表 | 重复对数 | 重复行数 |
|-----|:--:|:--:|
| `us_bars_5m` | 170,944 | 379,915 |
| `hk_bars_1d` | 16,926 | 33,873 |
| `us_bars_1d` | 48 | 96 |
| `hk_bars_5m` | 0 | 0 |

| # | 表 | 市场 | 操作 |
|---|-----|:--:|------|
| 12 | `quant.hk_bars_1d` | 🇭🇰 | `CREATE OR REPLACE` → 归一化 symbol + ROW_NUMBER 去重 |
| 13 | `quant.hk_bars_5m` | 🇭🇰 | 同上 |
| 14 | `quant.us_bars_5m` | 🇺🇸 | 同上（380K 行重灾区） |
| 15 | `quant.us_bars_1d` | 🇺🇸 | 同上（96 行轻微） |

清洗策略：归一化 symbol → `ROW_NUMBER() OVER (PARTITION BY norm_symbol, timestamp ORDER BY _ingest_time DESC)` → 保留最新行。

---

## 不动的地方

| 模块 | 原因 |
|------|------|
| `ml/datasets.py` | 已废弃，只有 legacy ml/tuner.py 引用 |
| `ml/tuner.py` | 已废弃，无活跃代码 import |
| `ml/datasets.py` | 同上 |
| `common/bq_writer.py` | 透传，取决于调用方 |
| `dashboard/observer.py` | 透传，symbol 来自 strategy |
| `live/experiment_manager.py` | 不处理 symbol |
| `admin/pipeline.py` | 已废弃 |
| `paper_run/` | 已废弃 |
| `run_paper.py` | 已废弃 |
| `scripts/train_*.py` ×3 | 已废弃 |
| `bigquery_loader/load_futu_factors.py` | 已废弃 |

---

## 影响评估

- **数据回填**: 修后新写入的数据格式统一
- **Paper 实验**: 修复后 HK paper run 不再有 symbol 不匹配
- **ML 训练**: 修复后基本面因子能正确 join 技术面因子，load_from_bq 防御性补零
- **实时采集**: ws_collector 写入格式不变（已用 HK./US. 前缀），消费端归一化
- **Dashboard**: observer 透传，不受影响

## 统计

15 处代码改动 + 1 个新文件 + 4 个 BQ 表清洗
