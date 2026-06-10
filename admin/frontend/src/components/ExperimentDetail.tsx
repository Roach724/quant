import { Card, Select, Table, Spin, Empty, Row, Col, Statistic, Button, Space } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import ReactECharts from 'echarts-for-react';
import { api, toLocal } from '../api';
import CacheRefresh from './CacheRefresh';

interface Props {
  type: 'live' | 'prod' | 'debug' | 'paper';
  readonly?: boolean;
}

export default function ExperimentDetail({ type, readonly: _readonly }: Props) {
  const [searchParams] = useSearchParams();
  const urlExpId = searchParams.get('exp_id') || '';
  const urlRunId = searchParams.get('run_id') || '';
  const [experiments, setExperiments] = useState<any[]>([]);
  const [selectedExp, setSelectedExp] = useState(urlExpId);
  const [selectedRun, setSelectedRun] = useState(urlRunId);
  const [runs, setRuns] = useState<any[]>([]);
  const [equity, setEquity] = useState<any[]>([]);
  const [trades, setTrades] = useState<any[]>([]);
  const [positions, setPositions] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadExperiments();
  }, []);

  useEffect(() => {
    if (!selectedExp) return;
    loadData(selectedExp, selectedRun);
  }, [selectedExp, selectedRun]);

  const loadExperiments = async () => {
    try {
      const [bqData, meta] = await Promise.all([
        api.get('/api/admin/dashboard/experiments'),
        api.get('/api/admin/dashboard/experiments/meta'),
      ]);
      const bqMap: Record<string, any> = {};
      if (Array.isArray(bqData)) bqData.forEach((e: any) => { bqMap[e.exp_id] = e; });
      if (Array.isArray(meta)) {
        const all = meta.map((m: any) => ({ ...m, ...(bqMap[m.exp_id] || { sleeping: true }) }));
        // Filter by type: live → only live_* experiments, etc.
        const filtered = all.filter((e: any) => e.exp_id.startsWith(type + '_'));
        setExperiments(filtered);
        // If URL exp_id specified, prefer it; otherwise auto-select first
        if (urlExpId && filtered.some((e: any) => e.exp_id === urlExpId)) {
          setSelectedExp(urlExpId);
        } else if (!selectedExp && filtered.length > 0) {
          setSelectedExp(filtered[0].exp_id);
        }
      }
    } catch (e) {
      console.error('load experiments failed', e);
    }
  };

  const loadData = async (expId: string, runId: string) => {
    setLoading(true);
    try {
      const params = runId ? `?run_id=${runId}` : '';
      const [equityData, tradesData, posData, runData] = await Promise.all([
        api.get(`/api/admin/dashboard/equity/${expId}${params}`),
        api.get(`/api/admin/dashboard/trades/${expId}?limit=500${runId ? `&run_id=${runId}` : ''}`),
        api.get(`/api/admin/dashboard/experiments/${expId}/positions`),
        api.get(`/api/admin/dashboard/experiments/${expId}/runs`),
      ]);
      setEquity(Array.isArray(equityData) ? equityData : []);
      setTrades(Array.isArray(tradesData) ? tradesData : []);
      setPositions(Array.isArray(posData) ? posData : []);
      if (Array.isArray(runData)) {
        setRuns(runData);
        // If URL run_id specified and exists in loaded runs, prefer it
        if (urlRunId && runData.some((r: any) => r.run_id === urlRunId)) {
          if (!selectedRun || selectedRun !== urlRunId) setSelectedRun(urlRunId);
        } else if (!runId && runData.length > 0) {
          setSelectedRun(runData[0].run_id);
        }
      }
    } catch (e) {
      console.error('load data failed', e);
    } finally {
      setLoading(false);
    }
  };

  // Latest metrics
  const last = equity.length > 0 ? equity[equity.length - 1] : null;
  const first = equity.length > 0 ? equity[0] : null;
  const initialCapital = first ? Number(first.cash ?? first.equity ?? 0) : 0;
  const totalPnl = last ? Number(last.equity ?? 0) - initialCapital : 0;
  const maxDrawdown = equity.length > 0 ? Math.min(...equity.map((d: any) => Number(d.drawdown ?? 0))) : 0;
  const unrealizedPnl = positions.reduce((s: number, p: any) => s + Number(p.pnl ?? 0), 0);
  const realizedPnl = totalPnl - unrealizedPnl;
  const winRate = (() => {
    const lots: Record<string, { qty: number; price: number }[]> = {};
    let wins = 0, losses = 0;
    for (const t of trades) {
      const sym = t.symbol;
      const qty = Number(t.qty);
      const price = Number(t.price);
      if (!lots[sym]) lots[sym] = [];
      if (t.side === 'buy') {
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
          <span style={{ fontWeight: 600, marginRight: 8 }}>Experiment</span>
          <Select
            value={selectedExp}
            onChange={(v) => { setSelectedExp(v); setSelectedRun(''); }}
            style={{ width: 260 }}
            showSearch
            optionFilterProp="label"
            options={experiments.map((e: any) => ({
              value: e.exp_id,
              label: `${e.exp_id} ${e.sleeping ? '(idle)' : ''}`,
            }))}
          />
        </Col>
        {runs.length > 0 && (
          <Col>
            <span style={{ fontWeight: 600, marginRight: 8 }}>Run</span>
            <Select
              value={selectedRun}
              onChange={setSelectedRun}
              style={{ width: 300 }}
              showSearch
              optionFilterProp="label"
              options={runs.map((r: any) => ({
                value: r.run_id,
                label: `${r.run_id}${r.created_at ? ' — ' + toLocal(r.created_at) : ''}`,
              }))}
            />
          </Col>
        )}
          <Col flex="auto" />
        <Col>
          <Space>
            <CacheRefresh module="dashboard:experiments" warmup={false} onRefresh={loadExperiments} />
            <CacheRefresh module="dashboard:equity" warmup={false} label="刷新权益" onRefresh={() => selectedExp && loadData(selectedExp, selectedRun)} />
            <CacheRefresh module="dashboard:trades" warmup={false} label="刷新交易" onRefresh={() => selectedExp && loadData(selectedExp, selectedRun)} />
            <CacheRefresh module="dashboard:positions" warmup={false} label="刷新持仓" onRefresh={() => selectedExp && loadData(selectedExp, selectedRun)} />
            <Button icon={<ReloadOutlined />} onClick={() => { loadExperiments(); if (selectedExp) loadData(selectedExp, selectedRun); }} size="small">刷新全部</Button>
          </Space>
        </Col>
      </Row>

      {/* ── Metric Cards: PnL Decomposition ── */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6} md={4}>
          <Card size="small"><Statistic title="Bar" value={last?.bar ?? '—'} /></Card>
        </Col>
        <Col xs={12} sm={6} md={4}>
          <Card size="small">
            <Statistic title="Initial Capital" value={Math.round(initialCapital).toLocaleString()} prefix="$" />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={4}>
          <Card size="small">
            <Statistic title="Realized PnL" value={Math.round(realizedPnl).toLocaleString()} prefix="$"
              valueStyle={{ color: realizedPnl >= 0 ? '#3f8600' : '#cf1322' }} />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={4}>
          <Card size="small">
            <Statistic title="Unrealized PnL" value={Math.round(unrealizedPnl).toLocaleString()} prefix="$"
              valueStyle={{ color: unrealizedPnl >= 0 ? '#3f8600' : '#cf1322' }} />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={4}>
          <Card size="small">
            <Statistic title="Total PnL" value={Math.round(totalPnl).toLocaleString()} prefix="$"
              valueStyle={{ color: totalPnl >= 0 ? '#3f8600' : '#cf1322' }} />
          </Card>
        </Col>
        <Col xs={12} sm={6} md={4}>
          <Card size="small">
            <Statistic title="Equity" value={last?.equity != null ? Math.round(Number(last.equity)).toLocaleString() : '—'} prefix="$" />
          </Card>
        </Col>
      </Row>

      {/* ── Metric Cards: Risk ── */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="Cash" value={last?.cash != null ? Math.round(Number(last.cash)).toLocaleString() : '—'} prefix="$" />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="Day PnL" value={last?.daily_pnl != null ? Math.round(Number(last.daily_pnl)).toLocaleString() : '—'} prefix="$"
              valueStyle={{ color: Number(last?.daily_pnl ?? 0) >= 0 ? '#3f8600' : '#cf1322' }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="Max Drawdown" value={(maxDrawdown * 100).toFixed(2) + '%'}
              valueStyle={{ color: '#cf1322' }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="Win Rate" value={(winRate).toFixed(1) + '%'}
              valueStyle={{ color: winRate >= 50 ? '#3f8600' : '#cf1322' }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="Positions" value={positions.length} />
          </Card>
        </Col>
      </Row>

      <Spin spinning={loading}>
        {equity.length === 0 ? (
          <Empty description="Select an experiment to view data" />
        ) : (
          <>
            {/* ── Equity Curve (stacked) ── */}
            <Card size="small" title="Equity Curve" style={{ marginBottom: 16 }}
              extra={<CacheRefresh module="dashboard:equity" warmup={false} onRefresh={() => loadData(selectedExp, selectedRun)} />}>
              <ReactECharts option={makeEquityOption(equity)} style={{ height: 350 }} />
            </Card>

            {/* ── Drawdown ── */}
            <Card size="small" title="Drawdown" style={{ marginBottom: 16 }}
              extra={<CacheRefresh module="dashboard:equity" warmup={false} onRefresh={() => loadData(selectedExp, selectedRun)} />}>
              <ReactECharts option={makeDrawdownOption(equity)} style={{ height: 200 }} />
            </Card>

            {/* ── Positions ── */}
            <Card size="small" title={`Positions (${positions.length})`} style={{ marginBottom: 16 }}
              extra={<CacheRefresh module="dashboard:positions" warmup={false} onRefresh={() => loadData(selectedExp, selectedRun)} />}>
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
                    const totalQty = pageData.reduce((s: number, r: any) => s + Number(r.qty), 0);
                    const totalCost = pageData.reduce((s: number, r: any) => s + Number(r.qty) * Number(r.avg_cost), 0);
                    const totalMktVal = pageData.reduce((s: number, r: any) => s + Number(r.market_value), 0);
                    const totalPriceQty = pageData.reduce((s: number, r: any) => s + Number(r.current_price) * Number(r.qty), 0);
                    const avgPrice = totalQty > 0 ? totalPriceQty / totalQty : 0;
                    const avgCost = totalQty > 0 ? totalCost / totalQty : 0;
                    const totalPnl = totalPriceQty - totalCost;
                    const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;
                    return (
                      <Table.Summary.Row>
                        <Table.Summary.Cell index={0}><strong>Total</strong></Table.Summary.Cell>
                        <Table.Summary.Cell index={1}>{totalQty.toFixed(2)}</Table.Summary.Cell>
                        <Table.Summary.Cell index={2}>${avgCost.toFixed(2)}</Table.Summary.Cell>
                        <Table.Summary.Cell index={3}>${avgPrice.toFixed(2)}</Table.Summary.Cell>
                        <Table.Summary.Cell index={4}>${totalMktVal.toFixed(2)}</Table.Summary.Cell>
                        <Table.Summary.Cell index={5}>
                          <span style={{ color: totalPnl >= 0 ? '#3f8600' : '#cf1322' }}>${totalPnl.toFixed(2)}</span>
                        </Table.Summary.Cell>
                        <Table.Summary.Cell index={6}>
                          <span style={{ color: totalPnlPct >= 0 ? '#3f8600' : '#cf1322' }}>{totalPnlPct.toFixed(2)}%</span>
                        </Table.Summary.Cell>
                      </Table.Summary.Row>
                    );
                  }}
                />
              )}
            </Card>

            {/* ── Trades ── */}
            <Card size="small" title={`Trades (${trades.length})`}
              extra={<CacheRefresh module="dashboard:trades" warmup={false} onRefresh={() => loadData(selectedExp, selectedRun)} />}>
              {trades.length === 0 ? <Empty description="No trades" /> : (
                <Table
                  dataSource={trades.map((t: any, i: number) => ({ ...t, _key: i }))}
                  rowKey="_key"
                  size="small"
                  pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: ['20', '50', '100', '200'] }}
                  scroll={{ x: 600 }}
                  columns={[
                    { title: 'Time', dataIndex: 'ts', width: 170, render: (v: string) => toLocal(v) },
                    { title: 'Bar', dataIndex: 'bar', width: 60 },
                    { title: 'Symbol', dataIndex: 'symbol', width: 80 },
                    { title: 'Side', dataIndex: 'side', width: 60, render: (v: string) => ({ children: v?.toUpperCase(), props: { style: { color: v === 'buy' ? '#3f8600' : '#cf1322', fontWeight: 600 } } }) },
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
        name: 'Cash',
        type: 'line',
        data: cash,
        stack: 'total',
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#52c41a', width: 1 },
        areaStyle: { color: 'rgba(82, 196, 26, 0.25)' },
      },
      {
        name: 'Positions',
        type: 'line',
        data: posValue,
        stack: 'total',
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#faad14', width: 1 },
        areaStyle: { color: 'rgba(250, 173, 20, 0.25)' },
      },
      {
        name: 'Equity',
        type: 'line',
        data: equity,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#1677ff', width: 2 },
        symbol: 'none',
      },
    ],
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
    ],
  };
}

function makeDrawdownOption(data: any[]) {
  const ts = data.map((d: any) => toLocal(d.ts) ?? '');
  const values = data.map((d: any) => Number(d.drawdown ?? 0) * 100);
  const yMin = Math.min(...values, 0);
  // Auto-scale with 10% padding below worst drawdown
  const yPad = Math.abs(yMin) * 0.1;

  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => `${v?.toFixed(2)}%` },
    grid: { left: 60, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: ts, axisLabel: { show: false } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: '{value}%' }, max: 0, min: yMin - yPad },
    series: [
      {
        type: 'line',
        data: values,
        showSymbol: false,
        lineStyle: { color: '#cf1322', width: 1 },
        areaStyle: { color: 'rgba(207, 19, 34, 0.15)' },
      },
    ],
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
    ],
  };
}
