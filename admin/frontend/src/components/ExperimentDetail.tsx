import { Card, Select, Table, Spin, Empty, Row, Col, Statistic, Button } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import ReactECharts from 'echarts-for-react';
import { api } from '../api';

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
                label: `${r.run_id}${r.created_at ? ' — ' + r.created_at?.slice(0, 19) : ''}`,
              }))}
            />
          </Col>
        )}
          <Col flex="auto" />
        <Col>
          <Button icon={<ReloadOutlined />} onClick={() => { loadExperiments(); if (selectedExp) loadData(selectedExp, selectedRun); }} size="small">刷新</Button>
        </Col>
      </Row>

      {/* ── Metric Cards ── */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <Card size="small"><Statistic title="Bar" value={last?.bar ?? '—'} /></Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Equity"
              value={last?.equity != null ? Math.round(Number(last.equity)).toLocaleString() : '—'}
              prefix="$"
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Day PnL"
              value={last?.daily_pnl != null ? Math.round(Number(last.daily_pnl)).toLocaleString() : '—'}
              prefix="$"
              valueStyle={{ color: Number(last?.daily_pnl ?? 0) >= 0 ? '#3f8600' : '#cf1322' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Max Drawdown"
              value={last?.drawdown != null ? (Number(last.drawdown) * 100).toFixed(2) + '%' : '—'}
              valueStyle={{ color: '#cf1322' }}
            />
          </Card>
        </Col>
      </Row>

      <Spin spinning={loading}>
        {equity.length === 0 ? (
          <Empty description="Select an experiment to view data" />
        ) : (
          <>
            {/* ── Equity Curve ── */}
            <Card size="small" title="Equity Curve" style={{ marginBottom: 16 }}>
              <ReactECharts option={makeEquityOption(equity)} style={{ height: 350 }} />
            </Card>

            {/* ── Drawdown ── */}
            <Card size="small" title="Drawdown" style={{ marginBottom: 16 }}>
              <ReactECharts option={makeDrawdownOption(equity)} style={{ height: 200 }} />
            </Card>

            {/* ── Positions ── */}
            <Card size="small" title={`Positions (${positions.length})`} style={{ marginBottom: 16 }}>
              {positions.length === 0 ? <Empty description="No open positions" /> : (
                <Table
                  dataSource={positions}
                  rowKey="symbol"
                  size="small"
                  pagination={false}
                  scroll={{ x: 600 }}
                  columns={[
                    { title: 'Symbol', dataIndex: 'symbol', width: 80 },
                    { title: 'Qty', dataIndex: 'qty', width: 80, render: (v: any) => Number(v).toFixed(2) },
                    { title: 'Avg Cost', dataIndex: 'avg_cost', width: 100, render: (v: any) => `$${Number(v).toFixed(2)}` },
                    { title: 'Price', dataIndex: 'current_price', width: 100, render: (v: any) => `$${Number(v).toFixed(2)}` },
                    { title: 'PnL', dataIndex: 'pnl', width: 100, render: (v: any) => ({ children: `$${Number(v).toFixed(2)}`, props: { style: { color: Number(v) >= 0 ? '#3f8600' : '#cf1322' } } }) },
                    { title: 'PnL%', dataIndex: 'pnl_pct', width: 80, render: (v: any) => ({ children: `${Number(v).toFixed(2)}%`, props: { style: { color: Number(v) >= 0 ? '#3f8600' : '#cf1322' } } }) },
                  ]}
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
                    { title: 'Time', dataIndex: 'ts', width: 170, render: (v: string) => v?.slice(0, 19) },
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
  const ts = data.map((d: any) => d.ts?.slice(0, 19) ?? '');
  const values = data.map((d: any) => Number(d.equity ?? 0));

  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 70, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: ts, axisLabel: { show: false } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: (v: number) => `$${(v / 1000).toFixed(0)}k` } },
    series: [
      {
        type: 'line',
        data: values,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#1677ff', width: 1.5 },
        areaStyle: { color: 'rgba(22, 119, 255, 0.08)' },
      },
    ],
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
    ],
  };
}

function makeDrawdownOption(data: any[]) {
  const ts = data.map((d: any) => d.ts?.slice(0, 19) ?? '');
  const values = data.map((d: any) => Number(d.drawdown ?? 0) * 100);

  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => `${v?.toFixed(2)}%` },
    grid: { left: 60, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: ts, axisLabel: { show: false } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: '{value}%' }, max: 0 },
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
