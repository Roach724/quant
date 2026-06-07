import { Card, Tag, Row, Col, Switch, Select, Spin, Empty, Space, Typography } from 'antd';
const { Text } = Typography;
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
  const [usSymbol, setUsSymbol] = useState('');
  const [hkSymbol, setHkSymbol] = useState('');
  const [usData, setUsData] = useState<any[]>([]);
  const [hkData, setHkData] = useState<any[]>([]);
  const [usLoading, setUsLoading] = useState(false);
  const [hkLoading, setHkLoading] = useState(false);
  const [usDays, setUsDays] = useState(1);
  const [hkDays, setHkDays] = useState(1);

  // Index chart state
  const INDEX_SYMBOLS: Record<string, string[]> = {
    us: ['^IXIC', '^GSPC', '^DJI', '^RUT'],
    hk: ['HK.800000', 'HK.800700', 'HK.800100'],
  };
  const [usIndexSymbol, setUsIndexSymbol] = useState('^IXIC');
  const [hkIndexSymbol, setHkIndexSymbol] = useState('HK.800000');
  const [usIndexData, setUsIndexData] = useState<any[]>([]);
  const [hkIndexData, setHkIndexData] = useState<any[]>([]);
  const [usIndexLoading, setUsIndexLoading] = useState(false);
  const [hkIndexLoading, setHkIndexLoading] = useState(false);
  const [usIndexDays, setUsIndexDays] = useState(1);
  const [hkIndexDays, setHkIndexDays] = useState(1);

  useEffect(() => { loadData(); }, []);
  useEffect(() => { if (usSymbol) loadChart('us', usSymbol, usDays); }, [usSymbol, usDays]);
  useEffect(() => { if (hkSymbol) loadChart('hk', hkSymbol, hkDays); }, [hkSymbol, hkDays]);

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
      if (Array.isArray(symbols) && symbols.length > 0) {
        if (market === 'us') { setUsSymbols(symbols); if (!usSymbol) { const s = symbols[0]; setUsSymbol(s); loadChart('us', s, usDays); } }
        else { setHkSymbols(symbols); if (!hkSymbol) { const s = symbols[0]; setHkSymbol(s); loadChart('hk', s, hkDays); } }
      }
    } catch (e) { console.error('load symbols failed', e); }
  };

  useEffect(() => { loadSymbols('us'); loadSymbols('hk'); }, []);
  useEffect(() => { if (usIndexSymbol) loadIndexChart('us', usIndexSymbol, usIndexDays); }, [usIndexSymbol, usIndexDays]);
  useEffect(() => { if (hkIndexSymbol) loadIndexChart('hk', hkIndexSymbol, hkIndexDays); }, [hkIndexSymbol, hkIndexDays]);

  const loadChart = useCallback(async (market: 'us' | 'hk', symbol: string, days: number = 1) => {
    if (!symbol) return;
    if (market === 'us') setUsLoading(true); else setHkLoading(true);
    const barsPerDay = market === 'us' ? 78 : 54;  // ~78 5m bars/day US, ~54 HK
    const limit = days * barsPerDay;
    try {
      const data = await api.get(`/api/admin/dashboard/market/${market}/${symbol}?limit=${limit}&days=${days}`);
      if (Array.isArray(data)) {
        if (market === 'us') setUsData(data); else setHkData(data);
      }
    } catch (e) { console.error('load chart failed', e); }
    finally { if (market === 'us') setUsLoading(false); else setHkLoading(false); }
  }, []);

  const loadIndexChart = useCallback(async (market: 'us' | 'hk', symbol: string, days: number = 1) => {
    if (!symbol) return;
    if (market === 'us') setUsIndexLoading(true); else setHkIndexLoading(true);
    const barsPerDay = market === 'us' ? 78 : 54;
    const limit = days * barsPerDay;
    try {
      const data = await api.get(`/api/admin/dashboard/market/${market}/${symbol}?limit=${limit}&days=${days}`);
      if (Array.isArray(data)) {
        if (market === 'us') setUsIndexData(data); else setHkIndexData(data);
      }
    } catch (e) { console.error('load index chart failed', e); }
    finally { if (market === 'us') setUsIndexLoading(false); else setHkIndexLoading(false); }
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
        <Text style={{ fontSize: 14, fontWeight: 600, display: 'block', marginBottom: 8 }}>🇺🇸 US Market — 5m K-line</Text>
        <Card size="small" title={<Space>US<Select size="small" value={usDays} onChange={setUsDays} style={{ width: 80, marginLeft: 8 }}
          options={[{ value: 1, label: '1d' }, { value: 3, label: '3d' }, { value: 7, label: '7d' }]} /></Space>}
          extra={<Select size="small" value={usSymbol} onChange={setUsSymbol} showSearch style={{ width: 130 }}
            options={usSymbols.map(s => ({ value: s, label: s }))} />}
          style={{ marginBottom: 12 }}>
          <Spin spinning={usLoading}>
            {usData.length === 0 ? <Empty description="No data" /> : <ReactECharts option={makeCandlestickOption(usData, usSymbol)} style={{ height: 350 }} />}
          </Spin>
        </Card>

        <Text style={{ fontSize: 14, fontWeight: 600, display: 'block', marginBottom: 8, marginTop: 24 }}>🇭🇰 HK Market — 5m K-line</Text>
        <Card size="small" title={<Space>HK<Select size="small" value={hkDays} onChange={setHkDays} style={{ width: 80, marginLeft: 8 }}
          options={[{ value: 1, label: '1d' }, { value: 3, label: '3d' }, { value: 7, label: '7d' }]} /></Space>}
          extra={<Select size="small" value={hkSymbol} onChange={setHkSymbol} showSearch style={{ width: 130 }}
            options={hkSymbols.map(s => ({ value: s, label: s }))} />}>
          <Spin spinning={hkLoading}>
            {hkData.length === 0 ? <Empty description="No data" /> : <ReactECharts option={makeCandlestickOption(hkData, hkSymbol)} style={{ height: 350 }} />}
          </Spin>
        </Card>

        {/* ── Index Charts ── */}
        <Text style={{ fontSize: 14, fontWeight: 600, display: 'block', marginBottom: 8, marginTop: 24 }}>🇺🇸 US Indices — 5m K-line</Text>
        <Card size="small" title={<Space>US Index<Select size="small" value={usIndexDays} onChange={setUsIndexDays} style={{ width: 80, marginLeft: 8 }}
          options={[{ value: 1, label: '1d' }, { value: 3, label: '3d' }, { value: 7, label: '7d' }]} /></Space>}
          extra={<Select size="small" value={usIndexSymbol} onChange={setUsIndexSymbol} style={{ width: 130 }}
            options={INDEX_SYMBOLS.us.map(s => ({ value: s, label: s.replace('^', '') }))} />}
          style={{ marginBottom: 12 }}>
          <Spin spinning={usIndexLoading}>
            {usIndexData.length === 0 ? <Empty description="No data" /> : <ReactECharts option={makeCandlestickOption(usIndexData, usIndexSymbol.replace('^', ''))} style={{ height: 300 }} />}
          </Spin>
        </Card>

        <Text style={{ fontSize: 14, fontWeight: 600, display: 'block', marginBottom: 8, marginTop: 24 }}>🇭🇰 HK Indices — 5m K-line</Text>
        <Card size="small" title={<Space>HK Index<Select size="small" value={hkIndexDays} onChange={setHkIndexDays} style={{ width: 80, marginLeft: 8 }}
          options={[{ value: 1, label: '1d' }, { value: 3, label: '3d' }, { value: 7, label: '7d' }]} /></Space>}
          extra={<Select size="small" value={hkIndexSymbol} onChange={setHkIndexSymbol} style={{ width: 150 }}
            options={INDEX_SYMBOLS.hk.map(s => ({ value: s, label: s }))} />}>
          <Spin spinning={hkIndexLoading}>
            {hkIndexData.length === 0 ? <Empty description="No data" /> : <ReactECharts option={makeCandlestickOption(hkIndexData, hkIndexSymbol)} style={{ height: 300 }} />}
          </Spin>
        </Card>
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
  // API returns {ts, o, h, l, c, v} — map to ECharts candlestick format
  const ohlc = data.map((d: any) => [d.o ?? d.open, d.c ?? d.close, d.l ?? d.low, d.h ?? d.high]);
  const volumes = data.map((d: any) => [d.v ?? d.volume ?? 0, (d.c ?? d.close) >= (d.o ?? d.open) ? 1 : -1]);
  // Format timestamps: HH:mm for axis, full for tooltip
  const formatTime = (ts: string) => {
    if (!ts) return '';
    const m = ts.match(/[T ](\d{2}:\d{2})/);
    return m ? m[1] : ts;
  };
  const dates = data.map((d: any) => formatTime(d.ts ?? d.time ?? ''));
  const fullDates = data.map((d: any) => {
    const t = d.ts ?? d.time ?? '';
    return t.replace('T', ' ').replace(/\+00:00$/, '').slice(0, 19);
  });
  // Show sparse labels (every Nth point)
  const labelInterval = Math.max(1, Math.floor(data.length / 10));

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params: any) => {
        const idx = params[0]?.dataIndex ?? 0;
        const row = data[idx];
        if (!row) return '';
        const ts = fullDates[idx];
        const o = row.o ?? row.open; const c = row.c ?? row.close;
        const h = row.h ?? row.high; const l = row.l ?? row.low;
        const v = row.v ?? row.volume ?? 0;
        return `${symbol}<br/>${ts}<br/>O: ${o}  C: ${c}<br/>H: ${h}  L: ${l}<br/>Vol: ${v.toLocaleString()}`;
      },
    },
    grid: [
      { left: 60, right: 20, top: 20, height: '60%' },
      { left: 60, right: 20, top: '75%', height: '15%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: {
        interval: labelInterval, rotate: 0, fontSize: 10,
      }},
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
