#!/usr/bin/env python3.12
"""Unified factor IC evaluation — reads from factor_registry, computes IC, writes to factor_evaluations.

Evaluates ALL active factors (tech + fundamental) from the BQ factor_registry,
computes rank IC vs forward returns, and writes results to factor_evaluations.
Also updates factor_registry with latest IC values.

Usage:
    python3.12 scripts/evaluate_all_factors.py --start 2020-01-01 --end 2026-05-31
    python3.12 scripts/evaluate_all_factors.py --source tech --dry-run
    python3.12 scripts/evaluate_all_factors.py --source fundamental --dry-run
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse, json, logging
import pandas as pd, numpy as np
from datetime import datetime, timezone
from google.cloud import bigquery
from scipy.stats import spearmanr

from factors.registry import FactorRegistry
from factors.tech_builder import TechFactorBuilder
from factors.f10_transformer import F10Transformer
from scripts.evaluate_f10_factors import (
    load_f10_table, preprocess_table, TABLE_TO_KEY, compute_quarterly_fwd_ret,
)
from scripts.compute_factors_batch import load_ohlcv_from_bq

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('eval_all')

PROJECT = 'deductive-notch-495015-c2'
TABLE_EVALS = f'{PROJECT}.quant.factor_evaluations'
TABLE_REGISTRY = f'{PROJECT}.quant.factor_registry'
TABLE_FACTOR_VALUES = f'{PROJECT}.quant.factor_values'

# ---------------------------------------------------------------
#  Tech factor IC — compute factor values from OHLCV on-the-fly
# ---------------------------------------------------------------

def compute_all_tech_factors_ic(start: str, end: str) -> list[dict]:
    """Compute IC for all tech factors directly from OHLCV data.

    Loads OHLCV once, computes all tech factors per stock via TechFactorBuilder,
    computes daily fwd_ret_5d, then evaluates each factor's IC vs fwd_ret_5d.

    Returns list of result dicts.
    """
    df = load_ohlcv_from_bq('us', start, end)
    if df.empty:
        log.warning('No OHLCV data')
        return []

    tfb = TechFactorBuilder()
    all_frames = []

    for sym, group in df.groupby('symbol'):
        stock_df = group.sort_values('date').drop_duplicates(subset=['date']).reset_index(drop=True)
        try:
            factors = tfb.compute_factors(stock_df)
            if factors is not None and not factors.empty:
                factors['symbol'] = sym
                factors['date'] = stock_df['date'].values[:len(factors)]
                all_frames.append(factors)
        except Exception as e:
            log.debug('  %s: skip — %s', sym, e)

    if not all_frames:
        log.warning('No tech factors computed')
        return []

    combined = pd.concat(all_frames, ignore_index=True)
    log.info('Tech factors computed: %d rows x %d symbols, %d cols',
             len(combined), combined['symbol'].nunique(), len(tfb.factor_names))

    # Compute fwd_ret_5d per stock for label
    fwd_list = []
    for sym, group in df.groupby('symbol'):
        group = group.sort_values('date').set_index('date')
        close = group['close']
        if len(close) <= 5:
            continue
        fwd = close.shift(-5)
        ret = (fwd - close) / close
        ret = ret.dropna()
        for d, v in ret.items():
            fwd_list.append({'symbol': sym, 'date': d, 'fwd_ret_5d': float(v)})
    fwd_df = pd.DataFrame(fwd_list)
    if not fwd_df.empty:
        fwd_df['date'] = pd.to_datetime(fwd_df['date'])
    log.info('Fwd ret 5d: %d rows', len(fwd_df))

    combined['date'] = pd.to_datetime(combined['date'])

    # Drop label columns from evaluation (don't evaluate labels as factors)
    factor_cols = [c for c in tfb.factor_names if c in combined.columns]
    log.info('Evaluating %d tech factor columns...', len(factor_cols))

    results = []
    for col in factor_cols:
        try:
            sub = combined[['symbol', 'date', col]].dropna(subset=[col])
            # Remove zeros that came from process_factors fillna — keep only raw values
            # (TechFactorBuilder doesn't fillna; raw NaN means no data)
            if len(sub) < 30:
                results.append({
                    'factor_id': f'us_{col}', 'factor_name': col,
                    'status': 'too_few', 'ic': None, 't_stat': None,
                    'coverage': 0, 'n': len(sub), 'passes': False,
                })
                continue

            merged = sub.merge(fwd_df, on=['symbol', 'date'], how='inner')
            if len(merged) < 30:
                results.append({
                    'factor_id': f'us_{col}', 'factor_name': col,
                    'status': 'too_few_merged', 'ic': None, 't_stat': None,
                    'coverage': 0, 'n': len(merged), 'passes': False,
                })
                continue

            total_rows = len(merged)
            valid = merged.dropna(subset=[col, 'fwd_ret_5d'])
            if len(valid) < 30:
                results.append({
                    'factor_id': f'us_{col}', 'factor_name': col,
                    'status': 'too_few_valid', 'ic': None, 't_stat': None,
                    'coverage': 0, 'n': len(valid), 'passes': False,
                })
                continue

            ic, _pval = spearmanr(valid[col], valid['fwd_ret_5d'])
            n = len(valid)
            denom = np.sqrt(max(1 - ic**2, 1e-12))
            t_stat = abs(ic) * np.sqrt(n - 2) / denom
            coverage = n / total_rows if total_rows > 0 else 0
            # Lower thresholds for admission (not same as rigorous IC eval)
            passes = abs(ic) > 0.02 and abs(t_stat) > 2.0 and coverage > 0.5

            status = 'pass' if passes else 'fail'
            results.append({
                'factor_id': f'us_{col}', 'factor_name': col,
                'status': status, 'ic': float(ic), 't_stat': float(t_stat),
                'coverage': float(coverage), 'n': n, 'passes': passes,
            })

            if status == 'pass':
                log.info('  ✅ %-35s IC=%+7.4f t=%6.1f cov=%.1f%% n=%d',
                         col, ic, t_stat, coverage * 100, n)

        except Exception as e:
            log.warning('  ❌ %s: %s', col, e)
            results.append({
                'factor_id': f'us_{col}', 'factor_name': col,
                'status': f'error: {e}', 'ic': None, 't_stat': None,
                'coverage': 0, 'n': 0, 'passes': False,
            })

    return results


# ---------------------------------------------------------------
#  F10 factor IC (quarterly pipeline)
# ---------------------------------------------------------------

def _build_category_map(ffb):
    """Build factor_name -> category dict from FundamentalFactorBuilder."""
    m = {}
    for name in ffb.QUALITY_COLS: m[name] = "quality"
    for name in ffb.GROWTH_COLS: m[name] = "growth"
    for name in ffb.EARNINGS_QUALITY_COLS: m[name] = "earnings_quality"
    for name in ffb.VALUATION_COLS: m[name] = "valuation"
    for name in ffb.SHORT_COLS: m[name] = "short_sentiment"
    for name in ffb.FLOW_COLS: m[name] = "capital_flow"
    for name in ffb.ANALYST_COLS: m[name] = "analyst"
    for name in ffb.SMART_MONEY_COLS: m[name] = "smart_money"
    for name in ffb.EARNINGS_EVENT_COLS: m[name] = "earnings_event"
    return m


def _load_and_preprocess_f10(start: str, end: str) -> dict:
    """Load all F10 tables, preprocess, transform, and return as data_map.

    Returns dict with MultiIndex (symbol, date) DataFrames keyed by category.
    """
    from factors.fundamental_builder import FundamentalFactorBuilder
    F10_TABLES = ['us_valuation', 'us_financials', 'us_analyst', 'us_capital_flow', 'us_shareholder']
    data_map = {}

    for tbl in F10_TABLES:
        try:
            raw = load_f10_table(tbl, start, end)
            if raw is None or raw.empty:
                continue
            processed = preprocess_table(tbl, raw)
            if processed.empty:
                continue
            key = TABLE_TO_KEY[tbl]
            data_map[key] = processed
        except Exception as e:
            log.warning('  %s: SKIP — %s', tbl, e)

    if not data_map:
        return {}

    data_map = F10Transformer.transform_all(data_map)

    # Set (symbol, date) MultiIndex
    for key in list(data_map.keys()):
        df = data_map[key]
        df = df.drop_duplicates(subset=[c for c in ['symbol', 'date'] if c in df.columns])
        if not df.empty and 'symbol' in df.columns and 'date' in df.columns:
            data_map[key] = df.set_index(['symbol', 'date'])

    return data_map


def compute_all_f10_factors_ic(start: str, end: str) -> list[dict]:
    """Evaluate all F10 factors using the quarterly pipeline.

    Loads F10 data once, computes quarterly fwd_ret once, then evaluates
    each factor against it. Returns list of result dicts.
    """
    from factors.fundamental_builder import FundamentalFactorBuilder

    data_map = _load_and_preprocess_f10(start, end)
    if not data_map:
        log.warning('No F10 data loaded')
        return []

    # Collect all unique (symbol, date) pairs
    f10_dates_list = []
    for key, df in data_map.items():
        if df.empty:
            continue
        if isinstance(df.index, pd.MultiIndex) and df.index.nlevels >= 2:
            idx_frame = df.index.to_frame(index=False)
            if 'symbol' in idx_frame.columns and 'date' in idx_frame.columns:
                f10_dates_list.append(idx_frame[['symbol', 'date']])
        elif 'symbol' in df.columns and 'date' in df.columns:
            f10_dates_list.append(df[['symbol', 'date']])

    if not f10_dates_list:
        return []

    f10_dates = pd.concat(f10_dates_list).drop_duplicates()
    log.info('F10 dates: %d unique (symbol,date) pairs', len(f10_dates))

    fwd = compute_quarterly_fwd_ret(f10_dates, horizon_days=63)
    if fwd.empty:
        log.warning('No quarterly fwd_ret computed')
        return []

    # Build factor list from builder
    ffb = FundamentalFactorBuilder()
    cat_map = _build_category_map(ffb)
    all_factors = ffb.ALL_FACTOR_COLS

    results = []
    for factor_name in all_factors:
        try:
            cat = cat_map.get(factor_name, 'unknown')
            # Find the data_map key that has this factor
            sub_map = {}
            for key, key_df in data_map.items():
                if factor_name in key_df.columns:
                    sub_map[key] = key_df

            if not sub_map:
                results.append({
                    'factor_id': f'us_{factor_name}', 'factor_name': factor_name,
                    'status': 'no_data', 'ic': None, 't_stat': None,
                    'coverage': 0, 'n': 0, 'passes': False,
                })
                continue

            factors_df = ffb.compute([factor_name], sub_map)
            if factors_df is None or factors_df.empty:
                results.append({
                    'factor_id': f'us_{factor_name}', 'factor_name': factor_name,
                    'status': 'no_data', 'ic': None, 't_stat': None,
                    'coverage': 0, 'n': 0, 'passes': False,
                })
                continue

            if not factors_df.index.is_unique:
                factors_df = factors_df[~factors_df.index.duplicated(keep='first')]

            if factor_name not in factors_df.columns:
                results.append({
                    'factor_id': f'us_{factor_name}', 'factor_name': factor_name,
                    'status': 'no_data', 'ic': None, 't_stat': None,
                    'coverage': 0, 'n': 0, 'passes': False,
                })
                continue

            # Reset index: (symbol, date) MultiIndex → columns
            factor_series = factors_df[factor_name]
            if isinstance(factor_series.index, pd.MultiIndex) and factor_series.index.nlevels >= 2:
                merged = factor_series.reset_index()
                merged.columns = ['symbol', 'date', factor_name]
            else:
                results.append({
                    'factor_id': f'us_{factor_name}', 'factor_name': factor_name,
                    'status': 'no_symbol', 'ic': None, 't_stat': None,
                    'coverage': 0, 'n': len(factors_df), 'passes': False,
                })
                continue

            merged['date'] = pd.to_datetime(merged['date'])
            fwd['date'] = pd.to_datetime(fwd['date'])

            merged = merged.merge(fwd, on=['symbol', 'date'], how='inner')
            if len(merged) < 10:
                results.append({
                    'factor_id': f'us_{factor_name}', 'factor_name': factor_name,
                    'status': 'too_few', 'ic': None, 't_stat': None,
                    'coverage': 0, 'n': len(merged), 'passes': False,
                })
                continue

            total_rows = len(merged)
            valid = merged.dropna(subset=[factor_name, 'fwd_ret_quarterly'])
            if len(valid) < 10:
                results.append({
                    'factor_id': f'us_{factor_name}', 'factor_name': factor_name,
                    'status': 'too_few_valid', 'ic': None, 't_stat': None,
                    'coverage': 0, 'n': len(valid), 'passes': False,
                })
                continue

            ic, _pval = spearmanr(valid[factor_name], valid['fwd_ret_quarterly'])
            n = len(valid)
            denom = np.sqrt(max(1 - ic**2, 1e-12))
            t_stat = abs(ic) * np.sqrt(n - 2) / denom
            coverage = n / total_rows if total_rows > 0 else 0
            # Lower |IC| threshold for quarterly fundamental (0.02 works here)
            passes = abs(ic) > 0.02 and abs(t_stat) > 2.0 and coverage > 0.3

            status = 'pass' if passes else 'fail'
            results.append({
                'factor_id': f'us_{factor_name}', 'factor_name': factor_name,
                'status': status, 'ic': float(ic), 't_stat': float(t_stat),
                'coverage': float(coverage), 'n': n, 'passes': passes,
            })

            if status == 'pass':
                log.info('  ✅ %-35s IC=%+7.4f t=%6.1f cov=%.1f%% n=%d',
                         factor_name, ic, t_stat, coverage * 100, n)

        except Exception as e:
            log.warning('  ❌ %s: %s', factor_name, e)
            results.append({
                'factor_id': f'us_{factor_name}', 'factor_name': factor_name,
                'status': f'error: {e}', 'ic': None, 't_stat': None,
                'coverage': 0, 'n': 0, 'passes': False,
            })

    return results


# ---------------------------------------------------------------
#  Main
# ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Unified Factor IC Evaluation')
    parser.add_argument('--start', default='2020-01-01')
    parser.add_argument('--end', default='2026-05-31')
    parser.add_argument('--source', choices=['tech', 'fundamental', 'all'], default='all')
    parser.add_argument('--dry-run', action='store_true', help='Compute but do not write to BQ')
    args = parser.parse_args()

    client = bigquery.Client(project=PROJECT)
    now = datetime.now(timezone.utc)

    # Read factors from registry
    source_filter = ''
    if args.source != 'all':
        source_filter = f"AND source = '{args.source}'"

    reg = client.query(f'''
        SELECT * FROM `{TABLE_REGISTRY}`
        WHERE is_active = TRUE {source_filter}
        ORDER BY source, category, factor_id
    ''').to_dataframe()

    log.info('=' * 60)
    log.info('Evaluating %d active factors (source=%s)', len(reg), args.source)
    log.info('Period: %s → %s', args.start, args.end)
    log.info('=' * 60)

    evaluations = []
    all_results = []

    # ── Tech factors (compute from OHLCV directly) ──
    if args.source in ('tech', 'all'):
        log.info('=== Computing tech factors from OHLCV ===')
        try:
            tech_results = compute_all_tech_factors_ic(args.start, args.end)
        except Exception as e:
            log.error('Tech factor evaluation failed: %s', e)
            import traceback; traceback.print_exc()
            tech_results = []

        for r in tech_results:
            fid = r['factor_id']
            ic_mean = r.get('ic')
            ic_tstat = r.get('t_stat')
            coverage = r.get('coverage')
            n_samples = r.get('n', 0)
            passes = r.get('passes', False)
            status = r.get('status', 'unknown')

            eval_id = f'{fid}_{args.start}_{args.end}'
            evaluation = {
                'eval_id': eval_id,
                'factor_id': fid,
                'evaluated_at': now.isoformat(),
                'ic_mean': ic_mean,
                'ic_std': None,
                'ic_tstat': ic_tstat,
                'ic_ir': None,
                'ic_decay_1d': None,
                'ic_decay_5d': None,
                'ic_decay_20d': None,
                'coverage': coverage,
                'skewness': None,
                'kurtosis': None,
                'top_correlated': None,
                'max_correlation': None,
                'passes_admission': passes,
                'admission_details': json.dumps({'status': status, 'n_samples': n_samples}),
                'eval_period_start': args.start,
                'eval_period_end': args.end,
                'eval_market': 'us',
                'data_version': now.strftime('%Y-%m-%d'),
                'metadata': json.dumps({'source': 'tech', 'method': 'spearman_rank'}),
            }
            evaluations.append(evaluation)
            all_results.append({
                'factor_id': fid, 'source': 'tech',
                'status': status, 'ic': ic_mean, 'n': n_samples, 'passes': passes,
            })

    # ── Fundamental factors ──
    if args.source in ('fundamental', 'all'):
        log.info('=== Computing fundamental factors from F10 tables ===')
        try:
            f10_results = compute_all_f10_factors_ic(args.start, args.end)
        except Exception as e:
            log.error('F10 evaluation failed: %s', e)
            import traceback; traceback.print_exc()
            f10_results = []

        for r in f10_results:
            fid = r['factor_id']
            ic_mean = r.get('ic')
            ic_tstat = r.get('t_stat')
            coverage = r.get('coverage')
            n_samples = r.get('n', 0)
            passes = r.get('passes', False)
            status = r.get('status', 'unknown')

            eval_id = f'{fid}_{args.start}_{args.end}'
            evaluation = {
                'eval_id': eval_id,
                'factor_id': fid,
                'evaluated_at': now.isoformat(),
                'ic_mean': ic_mean,
                'ic_std': None,
                'ic_tstat': ic_tstat,
                'ic_ir': None,
                'ic_decay_1d': None,
                'ic_decay_5d': None,
                'ic_decay_20d': None,
                'coverage': coverage,
                'skewness': None,
                'kurtosis': None,
                'top_correlated': None,
                'max_correlation': None,
                'passes_admission': passes,
                'admission_details': json.dumps({'status': status, 'n_samples': n_samples}),
                'eval_period_start': args.start,
                'eval_period_end': args.end,
                'eval_market': 'us',
                'data_version': now.strftime('%Y-%m-%d'),
                'metadata': json.dumps({'source': 'fundamental', 'method': 'spearman_rank'}),
            }
            evaluations.append(evaluation)
            all_results.append({
                'factor_id': fid, 'source': 'fundamental',
                'status': status, 'ic': ic_mean, 'n': n_samples, 'passes': passes,
            })

    # ── Write to BQ ──
    if not args.dry_run and evaluations:
        log.info('Writing %d evaluations to factor_evaluations...', len(evaluations))
        errors = client.insert_rows_json(TABLE_EVALS, evaluations)
        if errors:
            log.error('Errors writing evaluations (first 3): %s', errors[:3])
        else:
            log.info('  Written successfully!')

    # ── Update factor_registry with latest IC ──
    if not args.dry_run and all_results:
        log.info('Updating factor_registry with latest IC values...')
        registry = FactorRegistry()
        updated = 0
        for r in all_results:
            if r.get('ic') is not None and not np.isnan(r['ic']):
                try:
                    registry._update_registry_snapshot(
                        factor_id=r['factor_id'],
                        eval_id=f"{r['factor_id']}_{args.start}_{args.end}",
                        ic_mean=float(r['ic']),
                        ic_tstat=float(r.get('t_stat', 0)),
                        coverage=float(r.get('coverage', 0)),
                    )
                    updated += 1
                except Exception as e:
                    log.warning('  Update registry failed for %s: %s', r['factor_id'], e)
        log.info('  Updated %d factor registry entries', updated)

    # ── Print summary ──
    df_results = pd.DataFrame(all_results)
    if len(df_results) > 0:
        passing = df_results[df_results['passes'] == True]
        print('\n' + '=' * 60)
        print('  Factor IC Evaluation Summary')
        print('=' * 60)
        print(f'  Total: {len(df_results)} | Passing: {len(passing)}')

        if len(passing) > 0:
            print(f'\n  Passing factors:')
            for _, r in passing.sort_values('ic', key=abs, ascending=False).iterrows():
                print(f'    {r["factor_id"]:45s} IC={r["ic"]:+.4f}  n={r["n"]:,}  src={r["source"]}')

        failed = df_results[df_results['passes'] == False]
        if len(failed) > 0:
            print(f'\n  Did not pass ({len(failed)}):')
            status_counts = failed['status'].value_counts()
            for s, c in status_counts.items():
                print(f'    {s}: {c}')

        print('=' * 60)
    else:
        print('\nNo results — check logs for errors.')


if __name__ == '__main__':
    main()
