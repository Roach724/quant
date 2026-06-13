import { useState, useEffect, useMemo } from 'react';
import { Card, Select, Table, Empty, Row, Col, Statistic, Button, Spin } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { api, toLocal } from '../api';

/* ── Metric helpers ── */

function computeDrawdown(equity: number[]): number[] {
  let peak = -Infinity;
  return equity.map((e) => {
    if (e > peak) peak = e;
    return peak > 0 ? (e - peak) / peak : 0;
  });
}

function computeCumReturn(equity: number[]): number[] {
  const init = equity[0];
  if (!init || init === 0) return equity.map(() => 0);
  return equity.map((e) => (e / init - 1) * 100);
}

function computeDailyReturns(equity: number[], ts: string[]): number[] {
  const byDate = new Map<string, number>();
  for (let i = 0; i < ts.length; i++) {
    const date = (ts[i] || '').slice(0, 10);
    byDate.set(date, equity[i]);
  }
  const daily = [...byDate.values()];
  if (daily.length < 2) {
    const rets: number[] = [];
    for (let i = 1; i < equity.length; i++) {
      if (equity[i - 1] !== 0) rets.push((equity[i] - equity[i - 1]) / equity[i - 1]);
    }
    return rets;
  }
  const rets: number[] = [];
  for (let i = 1; i < daily.length; i++) {
    if (daily[i - 1] !== 0) rets.push((daily[i] - daily[i - 1]) / daily[i - 1]);
  }
  return rets;
}

function computeSharpe(dailyReturns: number[]): number {
  if (dailyReturns.length < 2) return 0;
  const mean = dailyReturns.reduce((s, r) => s + r, 0) / dailyReturns.length;
  const variance = dailyReturns.reduce((s, r) => s + (r - mean) ** 2, 0) / (dailyReturns.length - 1);
  const std = Math.sqrt(variance);
  return std > 0 ? (mean / std) * Math.sqrt(252) : 0;
}

function computeCalmar(equity: number[], drawdowns: number[], ts: string[]): number {
  if (equity.length < 2 || drawdowns.length === 0 || ts.length < 2) return 0;
  const totalRet = (equity[equity.length - 1] - equity[0]) / equity[0];
  const firstMs = new Date(ts[0]).getTime();
  const lastMs = new Date(ts[ts.length - 1]).getTime();
  const daysDiff = (lastMs - firstMs) / (1000 * 60 * 60 * 24);
  const nYears = Math.max(daysDiff / 365.25, 1 / 252);
  const annRet = (1 + totalRet) ** (1 / nYears) - 1;
  const maxDD = Math.min(...drawdowns);
  return Math.abs(maxDD) > 1e-10 ? annRet / Math.abs(maxDD) : 0;
}

interface Strategy { id: number; name: string; market: string; status: string; strategy_class: string; capital_allocated: number; cash: number; equity: number; positions: number; }

export default function TradingDashboardPanel({ env, preSelectedId }: { env: string; preSelectedId?: number }) {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedId, setSelectedId] = useState<number | undefined>(preSelectedId);
  const [equity, setEquity] = useState<any[]>([]);
  const [trades, setTrades] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const selected = strategies.find(s => s.id === selectedId);

  useEffect(() => {
    api.get('/api/admin/trading/strategies?env=' + env).then(setStrategies).catch(() => { });
  }, [env]);

  useEffect(() => {
    if (!selectedId) { setEquity([]); setTrades([]); setPositions([]); return; }
    setLoading(true);
    Promise.all([
      api.get(`/api/admin/trading/strategies/${selectedId}/equity?env=${env}`),
      api.get(`/api/admin/trading/strategies/${selectedId}/trades?env=${env}&limit=500`),
      api.get(`/api/admin/trading/strategies/${selectedId}/positions?env=${env}`),
    ]).then(([eq, tr, pos]) => {
      setEquity(Array.isArray(eq) ? eq : []);
      setTrades(Array.isArray(tr) ? tr : []);
      setPositions(Array.isArray(pos) ? pos : []);
    }).catch(() => { }).finally(() => setLoading(false));
  }, [selectedId, env]);

  // ── Computed metrics ──
  const eqValues = useMemo(() => equity.map((d: any) => Number(d.equity ?? 0)), [equity]);
  const tsValues = useMemo(() => equity.map((d: any) => d.ts ?? ''), [equity]);
  const drawdowns = useMemo(() => computeDrawdown(eqValues), [eqValues]);
  const cumReturns = useMemo(() => computeCumReturn(eqValues), [eqValues]);
  const dailyReturns = useMemo(() => computeDailyReturns(eqValues, tsValues), [eqValues, tsValues]);
  const maxDrawdown = drawdowns.length > 0 ? Math.min(...drawdowns) : 0;
  const cumReturnPct = cumReturns.length > 0 ? cumReturns[cumReturns.length - 1] : 0;
  const sharpeRatio = useMemo(() => computeSharpe(dailyReturns), [dailyReturns]);
  const calmarRatio = useMemo(() => computeCalmar(eqValues, drawdowns, tsValues), [eqValues, drawdowns, tsValues]);

  const last = equity.length > 0 ? equity[equity.length - 1] : null;
  const first = equity.length > 0 ? equity[0] : null;
  const initialCapital = first ? Number(first.equity ?? 0) : (selected?.capital_allocated ?? 0);
  const totalPnl = last ? Number(last.equity ?? 0) - initialCapital : 0;
  const unrealizedPnl = positions.reduce((s: number, p: any) => s + Number(p.pnl ?? 0), 0);
  const realizedPnl = totalPnl - unrealizedPnl;

  const winRate = (() => {
    const lots: Record<string, { qty: number; price: number }[]> = {};
    let wins = 0, losses = 0;
    // Trades come in desc order — reverse for FIFO
    const sortedTrades = [...trades].reverse();
    for (const t of sortedTrades) {
      const sym = t.symbol;
      const qty = Number(t.qty);
      const price = Number(t.price);
      if (!lots[sym]) lots[sym] = [];
      if (t.side?.toLowerCase() === 'buy') {
        lots[sym].push({ qty, price });
      } else {
        let remaining = qty;
        while (remaining > 0 && lots[sym].length > 0) {
          const lot = lots[sym][0];
          const matched = Math.min(lot.qty, remaining);
          if (price > lot.price) wins++;
          else if (price < lot.price) losses++;
          lot.qty -= matched;
          remaining -= matched;
          if (lot.qty <= 0) lots[sym].shift();
        }
      }
    }
    const total = wins + losses;
    return total > 0 ? (wins / total) * 100 : 0;
  })();

  return (
    <div>
      {/* ── Controls ── */}
      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col>
          <span style={{ fontWeight: 600, marginRight: 8 }}>策略</span>
          <Select
            value={selectedId}
            onChange={setSelectedId}
            style={{ width: 280 }}
            showSearch
            optionFilterProp="label"
            placeholder="选择策略..."
            options={strategies.map(s => ({
              value: s.id,
              label: `${s.name} (${s.market?.toUpperCase()}) — ${s.status}`,
            }))}
          />
        </Col>
        <Col flex="auto" />
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => { if (selectedId) { setSelectedId(0); setTimeout(() => setSelectedId(selectedId), 0); } }} size="small">刷新</Button>
        </Col>
      </Row>

      <Spin spinning={loading}>
        {!selected ? (
          <Empty description="选择一个策略查看详情" />
        ) : equity.length === 0 ? (
          <Empty description="暂无交易数据，启动策略后将产出权益曲线" />
        ) : (
          <>
            {/* ── Metric Cards: Performance ── */}
            <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
              <Col xs={12} sm={6} md={4}>
                <Card size="small"><Statistic title="Bar" value={last?.bar ?? '—'} /></Card>
              </Col>
              <Col xs={12} sm={6} md={4}>
                <Card size="small"><Statistic title="Initial Capital" value={Math.round(initialCapital).toLocaleString()} prefix="$" /></Card>
              </Col>
              <Col xs={12} sm={6} md={4}>
                <Card size="small"><Statistic title="Equity" value={last?.equity != null ? Math.round(Number(last.equity)).toLocaleString() : '—'} prefix="$" /></Card>
              </Col>
              <Col xs={12} sm={6} md={4}>
                <Card size="small"><Statistic title="Realized PnL" value={Math.round(realizedPnl).toLocaleString()} prefix="$"
                  valueStyle={{ color: realizedPnl >= 0 ? '#3f8600' : '#cf1322' }} /></Card>
              </Col>
              <Col xs={12} sm={6} md={4}>
                <Card size="small"><Statistic title="Unrealized PnL" value={Math.round(unrealizedPnl).toLocaleString()} prefix="$"
                  valueStyle={{ color: unrealizedPnl >= 0 ? '#3f8600' : '#cf1322' }} /></Card>
              </Col>
              <Col xs={12} sm={6} md={4}>
                <Card size="small"><Statistic title="Total PnL" value={Math.round(totalPnl).toLocaleString()} prefix="$"
                  valueStyle={{ color: totalPnl >= 0 ? '#3f8600' : '#cf1322' }} /></Card>
              </Col>
            </Row>

            {/* ── Metric Cards: Risk & Returns ── */}
            <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
              <Col xs={12} sm={6} md={3}>
                <Card size="small"><Statistic title="Cash" value={last?.cash != null ? Math.round(Number(last.cash)).toLocaleString() : '—'} prefix="$" /></Card>
              </Col>
              <Col xs={12} sm={6} md={3}>
                <Card size="small"><Statistic title="Day PnL" value={last?.daily_pnl != null ? Math.round(Number(last.daily_pnl)).toLocaleString() : '—'} prefix="$"
                  valueStyle={{ color: Number(last?.daily_pnl ?? 0) >= 0 ? '#3f8600' : '#cf1322' }} /></Card>
              </Col>
              <Col xs={12} sm={6} md={3}>
                <Card size="small"><Statistic title="Cum Return" value={cumReturnPct.toFixed(2) + '%'}
                  valueStyle={{ color: cumReturnPct >= 0 ? '#3f8600' : '#cf1322' }} /></Card>
              </Col>
              <Col xs={12} sm={6} md={3}>
                <Card size="small"><Statistic title="Max Drawdown" value={(Math.abs(maxDrawdown) * 100).toFixed(2) + '%'}
                  valueStyle={{ color: '#cf1322' }} /></Card>
              </Col>
              <Col xs={12} sm={6} md={3}>
                <Card size="small"><Statistic title="Sharpe" value={sharpeRatio.toFixed(2)}
                  valueStyle={{ color: sharpeRatio >= 0 ? '#3f8600' : '#cf1322' }} /></Card>
              </Col>
              <Col xs={12} sm={6} md={3}>
                <Card size="small"><Statistic title="Calmar" value={calmarRatio.toFixed(2)}
                  valueStyle={{ color: calmarRatio >= 0 ? '#3f8600' : '#cf1322' }} /></Card>
              </Col>
              <Col xs={12} sm={6} md={3}>
                <Card size="small"><Statistic title="Win Rate" value={(winRate).toFixed(1) + '%'}
                  valueStyle={{ color: winRate >= 50 ? '#3f8600' : '#cf1322' }} /></Card>
              </Col>
              <Col xs={12} sm={6} md={3}>
                <Card size="small"><Statistic title="Positions" value={positions.length} /></Card>
              </Col>
            </Row>

            {/* ── Equity Curve (stacked) ── */}
            <Card size="small" title="Equity Curve" style={{ marginBottom: 16 }}>
              <ReactECharts option={makeEquityOption(equity)} style={{ height: 350 }} />
            </Card>

            {/* ── Cumulative Return ── */}
            <Card size="small" title="Cumulative Return" style={{ marginBottom: 16 }}>
              <ReactECharts option={makeCumReturnOption(tsValues, cumReturns)} style={{ height: 250 }} />
            </Card>

            {/* ── Drawdown ── */}
            <Card size="small" title="Drawdown" style={{ marginBottom: 16 }}>
              <ReactECharts option={makeDrawdownOption(tsValues, drawdowns)} style={{ height: 200 }} />
            </Card>

            {/* ── Positions ── */}
            <Card size="small" title={`Positions (${positions.length})`} style={{ marginBottom: 16 }}>
              {positions.length === 0 ? <Empty description="No open positions" /> : (
                <Table
                  dataSource={positions}
                  rowKey="symbol"
                  size="small"
                  pagination={false}
                  scroll={{ x: 700 }}
                  columns={[
                    { title: 'Symbol', dataIndex: 'symbol', width: 80 },
                    { title: 'Qty', dataIndex: 'qty', width: 80, render: (v: any) => Number(v).toFixed(2) },
                    { title: 'Avg Cost', dataIndex: 'avg_cost', width: 100, render: (v: any) => `$${Number(v).toFixed(2)}` },
                    { title: 'Price', dataIndex: 'current_price', width: 100, render: (v: any) => `$${Number(v).toFixed(2)}` },
                    { title: 'Mkt Val', dataIndex: 'market_value', width: 100, render: (v: any) => `$${Number(v).toFixed(2)}` },
                    { title: 'PnL', dataIndex: 'pnl', width: 100, render: (v: any) => ({ children: `$${Number(v).toFixed(2)}`, props: { style: { color: Number(v) >= 0 ? '#3f8600' : '#cf1322' } } }) },
                    { title: 'PnL%', dataIndex: 'pnl_pct', width: 80, render: (v: any) => ({ children: `${Number(v).toFixed(2)}%`, props: { style: { color: Number(v) >= 0 ? '#3f8600' : '#cf1322' } } }) },
                  ]}
                  summary={(pageData) => {
                    const totalMktVal = pageData.reduce((s: number, r: any) => s + Number(r.market_value), 0);
                    const totalPnl = pageData.reduce((s: number, r: any) => s + Number(r.pnl), 0);
                    return (
                      <Table.Summary.Row>
                        <Table.Summary.Cell index={0}><strong>Total</strong></Table.Summary.Cell>
                        <Table.Summary.Cell index={1} />
                        <Table.Summary.Cell index={2} />
                        <Table.Summary.Cell index={3} />
                        <Table.Summary.Cell index={4}>${totalMktVal.toFixed(2)}</Table.Summary.Cell>
                        <Table.Summary.Cell index={5}>
                          <span style={{ color: totalPnl >= 0 ? '#3f8600' : '#cf1322' }}>${totalPnl.toFixed(2)}</span>
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={6} />
                      </Table.Summary.Row>
                    );
                  }}
                />
              )}
            </Card>

            {/* ── Trades ── */}
            <Card size="small" title={`Trades (${trades.length})`}>
              {trades.length === 0 ? <Empty description="No trades" /> : (
                <Table
                  dataSource={trades.map((t: any, i: number) => ({ ...t, _key: i }))}
                  rowKey="_key"
                  size="small"
                  pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: ['20', '50', '100', '200'] }}
                  scroll={{ x: 600 }}
                  columns={[
                    { title: 'Time', dataIndex: 'created_at', width: 170, render: (v: string) => toLocal(v) },
                    { title: 'Symbol', dataIndex: 'symbol', width: 80 },
                    { title: 'Side', dataIndex: 'side', width: 60, render: (v: string) => ({ children: v?.toUpperCase(), props: { style: { color: v?.toLowerCase() === 'buy' ? '#3f8600' : '#cf1322', fontWeight: 600 } } }) },
                    { title: 'Qty', dataIndex: 'qty', width: 80, render: (v: any) => Number(v).toFixed(2) },
                    { title: 'Price', dataIndex: 'price', width: 100, render: (v: any) => `$${Number(v).toFixed(2)}` },
                    { title: 'Commission', dataIndex: 'commission', width: 80, render: (v: any) => `$${Number(v).toFixed(4)}` },
                  ]}
                />
              )}
            </Card>
          </>
        )}
      </Spin>
    </div>
  );
}

/* ── ECharts options ── */

function makeEquityOption(data: any[]) {
  const ts = data.map((d: any) => toLocal(d.ts) ?? '');
  const equity = data.map((d: any) => Number(d.equity ?? 0));
  const cash = data.map((d: any) => Number(d.cash ?? 0));
  const posValue = data.map((_: any, i: number) => equity[i] - cash[i]);

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (ps: any) => {
        const i = ps[0]?.dataIndex ?? 0;
        return `Bar ${i}<br/>Equity: $${equity[i].toLocaleString()}<br/>Cash: $${cash[i].toLocaleString()}<br/>Positions: $${posValue[i].toLocaleString()}`;
      },
    },
    grid: { left: 70, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: ts, axisLabel: { show: false } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: (v: number) => `$${(v / 1000).toFixed(0)}k` } },
    series: [
      {
        name: 'Cash', type: 'line', data: cash, stack: 'total',
        smooth: true, showSymbol: false,
        lineStyle: { color: '#52c41a', width: 1 },
        areaStyle: { color: 'rgba(82, 196, 26, 0.25)' },
      },
      {
        name: 'Positions', type: 'line', data: posValue, stack: 'total',
        smooth: true, showSymbol: false,
        lineStyle: { color: '#faad14', width: 1 },
        areaStyle: { color: 'rgba(250, 173, 20, 0.25)' },
      },
      {
        name: 'Equity', type: 'line', data: equity,
        smooth: true, showSymbol: false,
        lineStyle: { color: '#1677ff', width: 2 },
        symbol: 'none',
      },
    ],
    dataZoom: [{ type: 'inside', start: 0, end: 100 }],
  };
}

function makeCumReturnOption(ts: string[], cumReturns: number[]) {
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => `${v?.toFixed(2)}%` },
    grid: { left: 60, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: ts.map((t) => toLocal(t) ?? ''), axisLabel: { show: false } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: (v: number) => `${v.toFixed(1)}%` } },
    series: [{
      type: 'line', data: cumReturns, showSymbol: false,
      lineStyle: { color: cumReturns[cumReturns.length - 1] >= 0 ? '#3f8600' : '#cf1322', width: 2 },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: cumReturns[cumReturns.length - 1] >= 0 ? 'rgba(63,134,0,0.2)' : 'rgba(207,19,34,0.2)' },
            { offset: 1, color: 'rgba(255,255,255,0)' },
          ],
        },
      },
      markLine: { silent: true, data: [{ yAxis: 0, lineStyle: { color: '#999', type: 'dashed' } }] },
    }],
    dataZoom: [{ type: 'inside', start: 0, end: 100 }],
  };
}

function makeDrawdownOption(ts: string[], drawdowns: number[]) {
  const values = drawdowns.map((d) => d * 100);
  const yMin = values.length > 0 ? Math.min(...values, 0) : 0;
  const yPad = Math.abs(yMin) * 0.1;
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => `${v?.toFixed(2)}%` },
    grid: { left: 60, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: ts.map((t) => toLocal(t) ?? ''), axisLabel: { show: false } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: (v: number) => `${v.toFixed(1)}%` }, max: 0, min: yMin - yPad },
    series: [{
      type: 'line', data: values, showSymbol: false,
      lineStyle: { color: '#cf1322', width: 1 },
      areaStyle: { color: 'rgba(207, 19, 34, 0.15)' },
    }],
    dataZoom: [{ type: 'inside', start: 0, end: 100 }],
  };
}
