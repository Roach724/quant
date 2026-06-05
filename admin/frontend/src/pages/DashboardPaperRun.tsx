import { Card, Table, Spin, Empty, Row, Col, Statistic, Tag, Button } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useEffect, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { api } from '../api';

export default function DashboardPaperRun() {
  const [runs, setRuns] = useState<any[]>([]);
  const [selectedRun, setSelectedRun] = useState<any | null>(null);
  const [detail, setDetail] = useState<any | null>(null);
  const [loading, setLoading] = useState(false);
  const [listLoading, setListLoading] = useState(true);

  useEffect(() => { loadRuns(); }, []);

  const loadRuns = async () => {
    try {
      const data = await api.get('/api/admin/dashboard/paper-runs');
      setRuns(Array.isArray(data) ? data : []);
    } catch (e) { console.error('load paper runs failed', e); }
    finally { setListLoading(false); }
  };

  const loadDetail = async (runId: string) => {
    setLoading(true);
    try {
      const data = await api.get(`/api/admin/dashboard/paper-runs/${runId}`);
      setDetail(data);
    } catch (e) { console.error('load detail failed', e); }
    finally { setLoading(false); }
  };

  const handleRowClick = (record: any) => {
    setSelectedRun(record);
    loadDetail(record.run_id);
  };

  // Detail View
  if (selectedRun) {
    const m = detail?.metrics ?? {};
    const eq = detail?.equity ?? [];
    const tr = detail?.trades ?? [];

    const metricsGrid = [
      { label: 'Total Return', value: m.total_return ? `${(Number(m.total_return) * 100).toFixed(2)}%` : '—', color: (m.total_return ?? 0) >= 0 ? '#3f8600' : '#cf1322' },
      { label: 'Annual Return', value: m.annual_return ? `${(Number(m.annual_return) * 100).toFixed(2)}%` : '—', color: (m.annual_return ?? 0) >= 0 ? '#3f8600' : '#cf1322' },
      { label: 'Sharpe', value: m.sharpe ? Number(m.sharpe).toFixed(2) : '—' },
      { label: 'Sortino', value: m.sortino ? Number(m.sortino).toFixed(2) : '—' },
      { label: 'Max Drawdown', value: m.max_drawdown ? `${(Number(m.max_drawdown) * 100).toFixed(2)}%` : '—', color: '#cf1322' },
      { label: 'Calmar', value: m.calmar ? Number(m.calmar).toFixed(2) : '—' },
      { label: 'Win Rate', value: m.win_rate ? `${(Number(m.win_rate) * 100).toFixed(1)}%` : '—' },
      { label: 'Profit Factor', value: m.profit_factor ? Number(m.profit_factor).toFixed(2) : '—' },
      { label: 'Total Trades', value: m.total_trades ?? '—' },
      { label: 'Start Equity', value: m.start_equity ? `$${Number(m.start_equity).toLocaleString()}` : '—' },
      { label: 'End Equity', value: m.end_equity ? `$${Number(m.end_equity).toLocaleString()}` : '—' },
      { label: 'Annual Vol', value: m.annual_vol ? `${(Number(m.annual_vol) * 100).toFixed(2)}%` : '—' },
    ];

    return (
      <div>
        <Button icon={<ArrowLeftOutlined />} onClick={() => { setSelectedRun(null); setDetail(null); }} style={{ marginBottom: 16 }}>
          Back to list
        </Button>

        <Card size="small" title={`${selectedRun.name} (${selectedRun.run_id})`} style={{ marginBottom: 16 }}>
          <Row gutter={12}>
            <Col><Tag color="blue">{selectedRun.strategy}</Tag></Col>
            <Col><Tag>{selectedRun.market?.toUpperCase()}</Tag></Col>
            <Col><Tag color={selectedRun.status === 'completed' ? 'green' : 'orange'}>{selectedRun.status}</Tag></Col>
            <Col><span style={{ color: '#999', fontSize: 13 }}>{selectedRun.n_periods} periods</span></Col>
          </Row>
        </Card>

        <Spin spinning={loading}>
          {/* Metrics Cards */}
          <Row gutter={[8, 8]} style={{ marginBottom: 16 }}>
            {metricsGrid.map((item) => (
              <Col key={item.label} xs={12} sm={8} md={6} lg={4}>
                <Card size="small">
                  <Statistic
                    title={item.label}
                    value={item.value}
                    valueStyle={{ fontSize: 18, color: item.color }}
                  />
                </Card>
              </Col>
            ))}
          </Row>

          {/* Equity Chart */}
          {Array.isArray(eq) && eq.length > 0 && (
            <Card size="small" title="Equity Curve" style={{ marginBottom: 16 }}>
              <ReactECharts
                option={{
                  tooltip: { trigger: 'axis' },
                  grid: { left: 70, right: 20, top: 20, bottom: 30 },
                  xAxis: { type: 'category', data: eq.map((d: any) => d.ts?.slice(0, 19) ?? d.bar ?? ''), axisLabel: { show: false } },
                  yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: (v: number) => `$${(v / 1000).toFixed(0)}k` } },
                  series: [{
                    type: 'line',
                    data: eq.map((d: any) => Number(d.equity ?? 0)),
                    smooth: true,
                    showSymbol: false,
                    lineStyle: { color: '#1677ff', width: 1.5 },
                    areaStyle: { color: 'rgba(22, 119, 255, 0.08)' },
                  }],
                  dataZoom: [{ type: 'inside', start: 0, end: 100 }],
                }}
                style={{ height: 300 }}
              />
            </Card>
          )}

          {/* Trades Table */}
          {Array.isArray(tr) && tr.length > 0 && (
            <Card size="small" title={`Trades (${tr.length})`}>
              <Table
                dataSource={tr.map((t: any, i: number) => ({ ...t, _key: i }))}
                rowKey="_key"
                size="small"
                pagination={{ pageSize: 50, showSizeChanger: true }}
                scroll={{ x: 600 }}
                columns={[
                  { title: 'Time', dataIndex: 'ts', width: 170, render: (v: string) => v?.slice(0, 19) },
                  { title: 'Bar', dataIndex: 'bar', width: 60 },
                  { title: 'Symbol', dataIndex: 'symbol', width: 80 },
                  { title: 'Side', dataIndex: 'side', width: 60, render: (v: string) => ({ children: v?.toUpperCase(), props: { style: { color: v === 'buy' ? '#3f8600' : '#cf1322', fontWeight: 600 } } }) },
                  { title: 'Qty', dataIndex: 'qty', width: 80, render: (v: any) => Number(v).toFixed(2) },
                  { title: 'Price', dataIndex: 'price', width: 100, render: (v: any) => `$${Number(v).toFixed(2)}` },
                ]}
              />
            </Card>
          )}

          {(!Array.isArray(eq) || eq.length === 0) && (!Array.isArray(tr) || tr.length === 0) && !loading && (
            <Empty description="No equity or trade data for this run" />
          )}
        </Spin>
      </div>
    );
  }

  // List View
  return (
    <div>
      <div style={{ marginBottom: 16, fontWeight: 600, fontSize: 16 }}>Paper Runs</div>
      <Spin spinning={listLoading}>
        {runs.length === 0 ? <Empty description="No paper runs found" /> : (
          <Table
            dataSource={runs}
            rowKey="run_id"
            size="small"
            onRow={(record) => ({
              onClick: () => handleRowClick(record),
              style: { cursor: 'pointer' },
            })}
            pagination={{ pageSize: 20 }}
            columns={[
              { title: 'Run ID', dataIndex: 'run_id', width: 200, ellipsis: true },
              { title: 'Name', dataIndex: 'name', width: 200 },
              { title: 'Strategy', dataIndex: 'strategy', width: 150 },
              { title: 'Market', dataIndex: 'market', width: 70, render: (v: string) => v?.toUpperCase() },
              { title: 'Status', dataIndex: 'status', width: 100, render: (v: string) => <Tag color={v === 'completed' ? 'green' : v === 'failed' ? 'red' : 'orange'}>{v}</Tag> },
              { title: 'Periods', dataIndex: 'n_periods', width: 80 },
              { title: 'Created', dataIndex: 'created_at', width: 170, render: (v: string) => v?.slice(0, 19) },
            ]}
          />
        )}
      </Spin>
    </div>
  );
}
