import { Card, Tag, Row, Col, Switch, Select, Spin, Empty } from 'antd';
import { useEffect, useState, useCallback } from 'react';
import ReactECharts from 'echarts-for-react';
import { api } from '../api';

export default function DashboardOverview() {
  const [experiments, setExperiments] = useState<any[]>([]);
  const [activeOnly, setActiveOnly] = useState(true);
  const [loading, setLoading] = useState(true);

    // Pipeline / market state
  const [pipeline, setPipeline] = useState<any>({});

  // K-line states
  const [usSymbols, setUsSymbols] = useState<string[]>([]);
  const [hkSymbols, setHkSymbols] = useState<string[]>([]);
  const [usSymbol, setUsSymbol] = useState('AAPL');
  const [hkSymbol, setHkSymbol] = useState('00700');
  const [usData, setUsData] = useState<any[]>([]);
  const [hkData, setHkData] = useState<any[]>([]);
  const [chartLoading, setChartLoading] = useState(false);

  useEffect(() => { loadData(); }, []);
  useEffect(() => { loadChart('us', usSymbol); }, [usSymbol]);
  useEffect(() => { loadChart('hk', hkSymbol); }, [hkSymbol]);

  const loadData = async () => {
    try {
      const [bqData, meta, pipeData] = await Promise.all([
        api.get('/api/admin/dashboard/experiments'),
        api.get('/api/admin/dashboard/experiments/meta'),
        api.get('/api/admin/dashboard/pipeline'),
      ]);
      if (pipeData) setPipeline(pipeData);
      const bqMap: Record<string, any> = {};
      if (Array.isArray(bqData)) bqData.forEach((e: any) => { bqMap[e.exp_id] = e; });
      if (Array.isArray(meta)) {
        setExperiments(meta.map((m: any) => ({
          ...m,
          ...bqMap[m.exp_id],
          sleeping: m.status !== 'running',
        })));
      }
    } catch (e) {
      console.error('load experiments failed', e);
    } finally {
      setLoading(false);
    }
  };

  const loadSymbols = async (market: 'us' | 'hk') => {
    try {
      const symbols = await api.get(`/api/admin/dashboard/market/symbols/${market}`);
      if (Array.isArray(symbols)) {
        if (market === 'us') { setUsSymbols(symbols); if (!usSymbol) setUsSymbol(symbols[0]); }
        else { setHkSymbols(symbols); if (!hkSymbol) setHkSymbol(symbols[0]); }
      }
    } catch (e) { console.error('load symbols failed', e); }
  };

  useEffect(() => { loadSymbols('us'); loadSymbols('hk'); }, []);

  const loadChart = useCallback(async (market: 'us' | 'hk', symbol: string) => {
    if (!symbol) return;
    setChartLoading(true);
    try {
      const data = await api.get(`/api/admin/dashboard/market/${market}/${symbol}?limit=78`);
      if (Array.isArray(data)) {
        if (market === 'us') setUsData(data); else setHkData(data);
      }
    } catch (e) { console.error('load chart failed', e); }
    finally { setChartLoading(false); }
  }, []);

  const filtered = activeOnly
    ? experiments.filter((e: any) => !e.sleeping)
    : experiments;

  return (
    <div>
      {/* ── Experiment Cards ── */}
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontWeight: 600, fontSize: 16 }}>Experiments</span>
        <Switch checked={activeOnly} onChange={setActiveOnly} size="small" />
        <span style={{ color: '#999', fontSize: 13 }}>Active Only ({filtered.length}/{experiments.length})</span>
      </div>
      <Spin spinning={loading}>
        {filtered.length === 0 ? <Empty description="No experiments" /> : (
          <Row gutter={[12, 12]}>
            {filtered.map((exp: any) => (
              <Col key={exp.exp_id} xs={24} sm={12} md={8} lg={6}>
                <Card
                  size="small"
                  title={
                    <span style={{ fontSize: 13 }}>
                      {exp.exp_id}
                      <MarketDot expId={exp.exp_id} pipeline={pipeline} />
                    </span>
                  }
                >
                  <p>Status: <Tag color={exp.status === 'running' ? 'green' : 'default'}>{exp.status || '?'}</Tag></p>
                  <p>Bar: {exp.bar ?? '—'}</p>
                  <p>Equity: {exp.equity != null ? `$${Math.round(exp.equity).toLocaleString()}` : '—'}</p>
                  <p style={{ marginBottom: 0, fontSize: 12, color: (exp.daily_pnl ?? 0) >= 0 ? '#52c41a' : '#ff4d4f' }}>
                    Day PnL: {exp.daily_pnl != null ? `${exp.daily_pnl >= 0 ? '+' : ''}${exp.daily_pnl.toFixed(0)}` : '—'}
                  </p>
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Spin>

      {/* ── K-line Charts ── */}
      <div style={{ marginTop: 24 }}>
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card
              size="small"
              title="🇺🇸 US K-line (5m)"
              extra={
                <Select
                  size="small"
                  value={usSymbol}
                  onChange={setUsSymbol}
                  showSearch
                  style={{ width: 120 }}
                  options={usSymbols.map((s) => ({ value: s, label: s }))}
                />
              }
            >
              <Spin spinning={chartLoading}>
                {usData.length === 0 ? <Empty description="No data" /> : (
                  <ReactECharts option={makeCandlestickOption(usData, usSymbol)} style={{ height: 350 }} />
                )}
              </Spin>
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card
              size="small"
              title="🇭🇰 HK K-line (5m)"
              extra={
                <Select
                  size="small"
                  value={hkSymbol}
                  onChange={setHkSymbol}
                  showSearch
                  style={{ width: 120 }}
                  options={hkSymbols.map((s) => ({ value: s, label: s }))}
                />
              }
            >
              <Spin spinning={chartLoading}>
                {hkData.length === 0 ? <Empty description="No data" /> : (
                  <ReactECharts option={makeCandlestickOption(hkData, hkSymbol)} style={{ height: 350 }} />
                )}
              </Spin>
            </Card>
          </Col>
        </Row>
      </div>
    </div>
  );
}

/* ── Market open dot ── */
function MarketDot({ expId, pipeline }: { expId: string; pipeline: any }) {
  const isHk = expId.includes('_hk_') || expId.endsWith('_hk');
  const isOpen = isHk ? pipeline?.hk_open : pipeline?.us_open;
  if (isOpen === undefined || isOpen === null) return null;
  return (
    <span
      style={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        marginLeft: 6,
        backgroundColor: isOpen ? '#52c41a' : '#d9d9d9',
      }}
      title={isOpen ? 'Market Open' : 'Market Closed'}
    />
  );
}

function makeCandlestickOption(data: any[], symbol: string) {
  const dates = data.map((d: any) => d.ts ?? d.time ?? '');
  const ohlc = data.map((d: any) => [d.open, d.close, d.low, d.high]);
  const volumes = data.map((d: any) => [d.volume ?? 0, d.close >= d.open ? 1 : -1]);

  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: [
      { left: 60, right: 20, top: 20, height: '60%' },
      { left: 60, right: 20, top: '75%', height: '15%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { rotate: 30, fontSize: 10 } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true, axisLabel: { fontSize: 10 } },
      { type: 'value', gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      {
        name: symbol,
        type: 'candlestick',
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: { color: '#ef5350', color0: '#26a69a', borderColor: '#ef5350', borderColor0: '#26a69a' },
      },
      {
        name: 'Volume',
        type: 'bar',
        data: volumes.map((v: any) => v[0]),
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: {
          color: (params: any) => volumes[params.dataIndex]?.[1] > 0 ? '#ef5350' : '#26a69a',
        },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], start: 60, end: 100, height: 20, bottom: 0 },
    ],
  };
}
