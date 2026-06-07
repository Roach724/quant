import { Card, Select, Tabs, Spin, Empty, Typography, Space } from 'antd';
import { useEffect, useState, useCallback } from 'react';
import ReactECharts from 'echarts-for-react';
import { api } from '../api';

const { Text } = Typography;

/* ── Index symbols ── */
const INDEX_SYMBOLS: Record<string, string[]> = {
  us: ['^IXIC', '^GSPC', '^DJI'],
  hk: ['HK.800000', 'HK.800700', 'HK.800100'],
};

const BARS_PER_DAY: Record<string, number> = { us: 78, hk: 54 };

const DAYS_5M = [
  { value: 1, label: '1d' },
  { value: 3, label: '3d' },
  { value: 7, label: '7d' },
];

const DAYS_DAILY = [
  { value: 7, label: '7d' },
  { value: 30, label: '30d' },
  { value: 60, label: '60d' },
  { value: 90, label: '90d' },
];

/* ═══════════════════════════════════════════════════════════════════════════
   MarketCenter
   ═══════════════════════════════════════════════════════════════════════════ */
export default function MarketCenter() {
  const [market, setMarket] = useState<string>('us');
  const [subTab, setSubTab] = useState<string>('indices');

  const marketTabs = [
    { key: 'us', label: '🇺🇸 US' },
    { key: 'hk', label: '🇭🇰 HK' },
  ];

  return (
    <div>
      <Tabs
        activeKey={market}
        onChange={(k) => setMarket(k)}
        items={marketTabs.map((mt) => ({
          key: mt.key,
          label: mt.label,
          children: (
            <MarketSubTabs
              market={mt.key}
              subTab={subTab}
              onChangeSubTab={setSubTab}
            />
          ),
        }))}
      />
    </div>
  );
}

/* ── Sub-tabs: 指数 / 个股 ── */
function MarketSubTabs({
  market,
  subTab,
  onChangeSubTab,
}: {
  market: string;
  subTab: string;
  onChangeSubTab: (k: string) => void;
}) {
  const subItems = [
    { key: 'indices', label: '指数' },
    { key: 'stocks', label: '个股' },
  ];

  return (
    <Tabs
      activeKey={subTab}
      onChange={onChangeSubTab}
      items={subItems.map((si) => ({
        key: si.key,
        label: si.label,
        children:
          si.key === 'indices' ? (
            <IndicesPanel market={market} />
          ) : (
            <StocksPanel market={market} />
          ),
      }))}
    />
  );
}

/* ── 指数 Panel ── */
function IndicesPanel({ market }: { market: string }) {
  const symbols = INDEX_SYMBOLS[market] ?? [];

  if (symbols.length === 0) {
    return <Empty description="No index symbols configured" />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {symbols.map((sym) => (
        <IndexChartCard key={sym} market={market} symbol={sym} />
      ))}
    </div>
  );
}

/* ── Index chart card (5m + 日线) ── */
function IndexChartCard({ market, symbol }: { market: string; symbol: string }) {
  const bpd = BARS_PER_DAY[market] ?? 78;

  const [days5m, setDays5m] = useState(1);
  const [daysDaily, setDaysDaily] = useState(30);

  const [data5m, setData5m] = useState<any[]>([]);
  const [dataDaily, setDataDaily] = useState<any[]>([]);
  const [loading5m, setLoading5m] = useState(false);
  const [loadingDaily, setLoadingDaily] = useState(false);

  // ── 5m chart ──
  const load5m = useCallback(async () => {
    setLoading5m(true);
    const limit = days5m * bpd;
    try {
      const data = await api.get(
        `/api/admin/dashboard/market/${market}/${symbol}?limit=${limit}&days=${days5m}`,
      );
      setData5m(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error('load 5m failed', e);
    } finally {
      setLoading5m(false);
    }
  }, [market, symbol, days5m, bpd]);

  useEffect(() => {
    load5m();
  }, [load5m]);

  // ── 日线 chart ──
  const loadDaily = useCallback(async () => {
    setLoadingDaily(true);
    const limit = daysDaily * bpd;
    try {
      const data = await api.get(
        `/api/admin/dashboard/market/${market}/${symbol}?limit=${limit}&days=${daysDaily}`,
      );
      setDataDaily(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error('load daily failed', e);
    } finally {
      setLoadingDaily(false);
    }
  }, [market, symbol, daysDaily, bpd]);

  useEffect(() => {
    loadDaily();
  }, [loadDaily]);

  const displaySymbol = symbol.replace('^', '');

  return (
    <Card
      size="small"
      title={<Text strong>{displaySymbol}</Text>}
    >
      {/* ── 5m K-line ── */}
      <div style={{ marginBottom: 16 }}>
        <Space style={{ marginBottom: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            5m K-line
          </Text>
          <Select
            size="small"
            value={days5m}
            onChange={setDays5m}
            style={{ width: 70 }}
            options={DAYS_5M}
          />
        </Space>
        <Spin spinning={loading5m}>
          {data5m.length === 0 ? (
            <Empty description={`No 5m data for ${displaySymbol}`} />
          ) : (
            <ReactECharts
              option={makeCandlestickOption(data5m, displaySymbol)}
              style={{ height: 320 }}
            />
          )}
        </Spin>
      </div>

      {/* ── 日线 ── */}
      <div>
        <Space style={{ marginBottom: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            日线视图
          </Text>
          <Select
            size="small"
            value={daysDaily}
            onChange={setDaysDaily}
            style={{ width: 70 }}
            options={DAYS_DAILY}
          />
        </Space>
        <Spin spinning={loadingDaily}>
          {dataDaily.length === 0 ? (
            <Empty description={`No daily data for ${displaySymbol}`} />
          ) : (
            <ReactECharts
              option={makeCandlestickOption(dataDaily, displaySymbol)}
              style={{ height: 320 }}
            />
          )}
        </Spin>
      </div>
    </Card>
  );
}

/* ── 个股 Panel ── */
function StocksPanel({ market }: { market: string }) {
  const bpd = BARS_PER_DAY[market] ?? 78;

  const [symbols, setSymbols] = useState<string[]>([]);
  const [symbol, setSymbol] = useState('');
  const [symbolsLoading, setSymbolsLoading] = useState(false);

  const [days5m, setDays5m] = useState(1);
  const [daysDaily, setDaysDaily] = useState(30);

  const [data5m, setData5m] = useState<any[]>([]);
  const [dataDaily, setDataDaily] = useState<any[]>([]);
  const [loading5m, setLoading5m] = useState(false);
  const [loadingDaily, setLoadingDaily] = useState(false);

  // ── Load symbols ──
  useEffect(() => {
    (async () => {
      setSymbolsLoading(true);
      try {
        const res = await api.get(
          `/api/admin/dashboard/market/symbols/${market}`,
        );
        if (Array.isArray(res) && res.length > 0) {
          setSymbols(res);
          setSymbol((prev) => prev || res[0]);
        }
      } catch (e) {
        console.error('load symbols failed', e);
      } finally {
        setSymbolsLoading(false);
      }
    })();
  }, [market]);

  // ── 5m chart ──
  const load5m = useCallback(async () => {
    if (!symbol) return;
    setLoading5m(true);
    const limit = days5m * bpd;
    try {
      const data = await api.get(
        `/api/admin/dashboard/market/${market}/${symbol}?limit=${limit}&days=${days5m}`,
      );
      setData5m(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error('load 5m failed', e);
    } finally {
      setLoading5m(false);
    }
  }, [market, symbol, days5m, bpd]);

  useEffect(() => {
    load5m();
  }, [load5m]);

  // ── 日线 chart ──
  const loadDaily = useCallback(async () => {
    if (!symbol) return;
    setLoadingDaily(true);
    const limit = daysDaily * bpd;
    try {
      const data = await api.get(
        `/api/admin/dashboard/market/${market}/${symbol}?limit=${limit}&days=${daysDaily}`,
      );
      setDataDaily(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error('load daily failed', e);
    } finally {
      setLoadingDaily(false);
    }
  }, [market, symbol, daysDaily, bpd]);

  useEffect(() => {
    loadDaily();
  }, [loadDaily]);

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Text type="secondary">Symbol</Text>
        <Select
          showSearch
          value={symbol || undefined}
          onChange={setSymbol}
          loading={symbolsLoading}
          style={{ width: 160 }}
          placeholder="Select symbol"
          options={symbols.map((s) => ({ value: s, label: s }))}
          filterOption={(input, option) =>
            (option?.label as string)?.toLowerCase().includes(input.toLowerCase())
          }
        />
      </Space>

      {!symbol ? (
        <Empty description="Select a symbol" />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* 5m Card */}
          <Card
            size="small"
            title={<Text strong>{symbol}</Text>}
            extra={
              <Select
                size="small"
                value={days5m}
                onChange={setDays5m}
                style={{ width: 70 }}
                options={DAYS_5M}
              />
            }
          >
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
              5m K-line
            </Text>
            <Spin spinning={loading5m}>
              {data5m.length === 0 ? (
                <Empty description={`No 5m data for ${symbol}`} />
              ) : (
                <ReactECharts
                  option={makeCandlestickOption(data5m, symbol)}
                  style={{ height: 350 }}
                />
              )}
            </Spin>
          </Card>

          {/* 日线 Card */}
          <Card
            size="small"
            title={<Text strong>{symbol}</Text>}
            extra={
              <Select
                size="small"
                value={daysDaily}
                onChange={setDaysDaily}
                style={{ width: 70 }}
                options={DAYS_DAILY}
              />
            }
          >
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
              日线视图
            </Text>
            <Spin spinning={loadingDaily}>
              {dataDaily.length === 0 ? (
                <Empty description={`No daily data for ${symbol}`} />
              ) : (
                <ReactECharts
                  option={makeCandlestickOption(dataDaily, symbol)}
                  style={{ height: 350 }}
                />
              )}
            </Spin>
          </Card>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Shared chart option builder (same logic as DashboardOverview)
   ═══════════════════════════════════════════════════════════════════════════ */
function makeCandlestickOption(data: any[], symbol: string) {
  const ohlc = data.map((d: any) => [
    d.o ?? d.open,
    d.c ?? d.close,
    d.l ?? d.low,
    d.h ?? d.high,
  ]);
  const volumes = data.map((d: any) => [
    d.v ?? d.volume ?? 0,
    (d.c ?? d.close) >= (d.o ?? d.open) ? 1 : -1,
  ]);

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
        const o = row.o ?? row.open;
        const c = row.c ?? row.close;
        const h = row.h ?? row.high;
        const l = row.l ?? row.low;
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
      {
        type: 'category',
        data: dates,
        gridIndex: 1,
        axisLabel: { interval: labelInterval, rotate: 0, fontSize: 10 },
      },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true, axisLabel: { fontSize: 10 } },
      {
        type: 'value',
        gridIndex: 1,
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: symbol,
        type: 'candlestick',
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: '#ef5350',
          color0: '#26a69a',
          borderColor: '#ef5350',
          borderColor0: '#26a69a',
        },
      },
      {
        name: 'Volume',
        type: 'bar',
        data: volumes.map((v: any) => v[0]),
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: {
          color: (params: any) =>
            volumes[params.dataIndex]?.[1] > 0 ? '#ef5350' : '#26a69a',
        },
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        start: 60,
        end: 100,
        height: 20,
        bottom: 0,
      },
    ],
  };
}
