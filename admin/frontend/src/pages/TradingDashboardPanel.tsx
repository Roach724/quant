import { useState, useEffect } from 'react';
import { Card, Select, Table, Empty, Row, Col, Statistic, Button, Spin } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';

interface Strategy { id: number; name: string; market: string; status: string; strategy_class: string; capital_allocated: number; cash: number; equity: number; positions: number; }

export default function TradingDashboardPanel({ env, preSelectedId }: { env: string; preSelectedId?: number }) {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedId, setSelectedId] = useState<number | undefined>(preSelectedId);
  const [trades, setTrades] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const selected = strategies.find(s => s.id === selectedId);

  useEffect(() => {
    api.get('/api/admin/trading/strategies').then(setStrategies).catch(() => {});
  }, [env]);

  useEffect(() => {
    if (!selectedId) { setTrades([]); return; }
    setLoading(true);
    Promise.all([
      api.get(`/api/admin/trading/strategies/${selectedId}/trades`),
    ]).then(([t]) => { setTrades(t || []); }).catch(() => {})
      .finally(() => setLoading(false));
  }, [selectedId]);

  const tradeCols = [
    { title: '时间', dataIndex: 'created_at', width: 160, render: (v: string) => v?.slice(0, 19) || '-' },
    { title: '代码', dataIndex: 'symbol' },
    { title: '方向', dataIndex: 'side', width: 60, render: (v: string) => <span style={{ color: v === 'BUY' ? '#3f8600' : '#cf1322' }}>{v}</span> },
    { title: '数量', dataIndex: 'qty', width: 60 },
    { title: '价格', dataIndex: 'price', render: (v: number) => `$${(v || 0).toFixed(2)}` },
    { title: '佣金', dataIndex: 'commission', render: (v: number) => `$${(v || 0).toFixed(2)}` },
  ];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <span style={{ fontWeight: 600 }}>策略:</span>
        <Select
          value={selectedId}
          onChange={setSelectedId}
          style={{ width: 260 }}
          placeholder="选择策略..."
          options={strategies.map(s => ({
            value: s.id,
            label: `${s.name} (${s.market?.toUpperCase()}) — ${s.status}`,
          }))}
        />
        {selectedId && <Button size="small" icon={<ReloadOutlined />} onClick={() => setSelectedId(selectedId)}>刷新</Button>}
      </div>

      {!selected ? (
        <Empty description="选择一个策略查看详情" />
      ) : (
        <Spin spinning={loading}>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={6}><Card><Statistic title="状态" value={selected.status} /></Card></Col>
            <Col span={6}><Card><Statistic title="分配资金" value={selected.capital_allocated} prefix="$" precision={0} /></Card></Col>
            <Col span={6}><Card><Statistic title="当前现金" value={selected.cash} prefix="$" precision={0} /></Card></Col>
            <Col span={6}><Card><Statistic title="权益" value={selected.equity} prefix="$" precision={0} /></Card></Col>
          </Row>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={6}><Card><Statistic title="Strategy" value={selected.strategy_class} /></Card></Col>
            <Col span={6}><Card><Statistic title="总交易数" value={trades.length} /></Card></Col>
          </Row>

          <Card title="交易记录" size="small" style={{ marginBottom: 16 }}>
            <Table dataSource={trades} columns={tradeCols} rowKey="id"
              size="small" pagination={{ pageSize: 20 }} />
          </Card>

          <Card title={<span>⚠️ 权益曲线 — 待交易运行器启动后接入</span>} size="small">
            <Empty description="启动策略后，交易运行器将产出权益数据，此处展示权益曲线和回撤图" />
          </Card>
        </Spin>
      )}
    </div>
  );
}
