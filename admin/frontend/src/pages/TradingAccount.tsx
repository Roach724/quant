import { Card, Row, Col, Statistic, Table, Button, Tag, Select, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useState, useEffect } from 'react';
import { api, toLocal } from '../api';

interface Deal {
  deal_id: string; order_id: string; symbol: string; side: string;
  qty: number; price: number; created_at: string;
}
interface Order {
  broker_id: string; symbol: string; side: string; qty: number;
  filled_qty: number; order_type: string; status: string;
  avg_price: number | null; created_at: string;
}

const statusColor: Record<string, string> = {
  filled_all: 'green', filled_part: 'blue', submitted: 'orange',
  pending: 'orange', cancelled_all: 'default', cancelled_part: 'default',
  failed: 'red', disabled: 'default',
};

export default function TradingAccount() {
  const [acct, setAcct] = useState<any>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [loading, setLoading] = useState(false);
  const [market, setMarket] = useState('hk');

  const fetchData = async () => {
    setAcct(null); setOrders([]); setDeals([]);
    setLoading(true);
    try {
      const [a, o, d] = await Promise.all([
        api.get(`/api/admin/trading/account/sim?market=${market}`),
        api.get(`/api/admin/trading/orders/sim?market=${market}`),
        api.get(`/api/admin/trading/deals/sim?market=${market}`),
      ]);
      setAcct(a || null);
      setOrders(Array.isArray(o) ? o : []);
      setDeals(Array.isArray(d) ? d : []);
    } catch { message.error('加载失败'); }
    setLoading(false);
  };

  useEffect(() => {
    fetchData();
    const i = setInterval(fetchData, 600000);
    return () => clearInterval(i);
  }, [market]);

  const posCols = [
    { title: '代码', dataIndex: 'symbol', width: 100 },
    { title: '数量', dataIndex: 'qty' },
    { title: '成本价', dataIndex: 'avg_entry_price', render: (v: number) => v != null ? `$${(v).toFixed(2)}` : '-' },
    { title: '市值', dataIndex: 'market_value', render: (v: number) => v != null ? `$${v.toLocaleString()}` : '-' },
    {
      title: '盈亏', dataIndex: 'unrealized_pnl',
      render: (v: number) => (
        <span style={{ color: (v || 0) >= 0 ? '#3f8600' : '#cf1322' }}>
          ${(v || 0).toFixed(2)}
        </span>
      ),
    },
  ];

  const dealCols = [
    { title: '时间', dataIndex: 'created_at', width: 160, render: (v: string) => toLocal(v) },
    { title: '代码', dataIndex: 'symbol', width: 100 },
    { title: '方向', dataIndex: 'side', width: 50, render: (v: string) => <Tag color={v === 'buy' ? 'green' : 'red'}>{v?.toUpperCase()}</Tag> },
    { title: '数量', dataIndex: 'qty', width: 60 },
    { title: '价格', dataIndex: 'price', render: (v: number) => `$${(v || 0).toFixed(2)}` },
  ];

  const ordCols = [
    { title: '时间', dataIndex: 'created_at', width: 160, render: (v: string) => toLocal(v) },
    { title: '代码', dataIndex: 'symbol', width: 100 },
    { title: '方向', dataIndex: 'side', width: 50, render: (v: string) => <Tag color={v === 'buy' ? 'green' : 'red'}>{v?.toUpperCase()}</Tag> },
    { title: '数量', dataIndex: 'qty', width: 60 },
    { title: '成交', dataIndex: 'filled_qty', width: 60, render: (v: number, r: any) => `${v ?? '-'}${r.status === 'filled_part' ? ' (部分)' : ''}` },
    { title: '均价', dataIndex: 'avg_price', width: 80, render: (v: number | null) => v != null ? `$${v.toFixed(2)}` : '-' },
    { title: '状态', dataIndex: 'status', width: 100, render: (v: string) => <Tag color={statusColor[v] || 'default'}>{v}</Tag> },
  ];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <span style={{ fontWeight: 600 }}>模拟账户</span>
        <Select value={market} onChange={setMarket} style={{ width: 120 }}
          options={[
            { value: 'all', label: '🌐 All' },
            { value: 'hk', label: '🇭🇰 HK' },
            { value: 'us', label: '🇺🇸 US' },
          ]} />
        <Button size="small" icon={<ReloadOutlined />} onClick={fetchData}>刷新</Button>
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card><Statistic title="资产净值" value={acct?.equity ?? '-'} prefix={acct ? '$' : ''} precision={0} /></Card></Col>
        <Col span={6}><Card><Statistic title="现金" value={acct?.cash ?? '-'} prefix={acct ? '$' : ''} precision={0} /></Card></Col>
        <Col span={6}><Card><Statistic title="购买力" value={acct?.buying_power ?? '-'} prefix={acct ? '$' : ''} precision={0} /></Card></Col>
        <Col span={6}><Card><Statistic title="持仓市值" value={acct?.market_value ?? '-'} prefix={acct ? '$' : ''} precision={0} /></Card></Col>
      </Row>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card><Statistic title="持仓盈亏" value={acct?.total_pnl ?? '-'} precision={2}
          prefix={acct ? '$' : ''} valueStyle={{ color: (acct?.total_pnl || 0) >= 0 ? '#3f8600' : '#cf1322' }} /></Card></Col>
        <Col span={6}><Card><Statistic title="盈亏率" value={acct?.pnl_pct ?? '-'} suffix={acct ? '%' : ''} precision={2}
          valueStyle={{ color: (acct?.pnl_pct || 0) >= 0 ? '#3f8600' : '#cf1322' }} /></Card></Col>
      </Row>

      <Card title="持仓" size="small" style={{ marginBottom: 16 }}>
        <Table dataSource={acct?.positions || []} columns={posCols}
          rowKey="symbol" size="small" pagination={false} loading={loading} />
      </Card>

      <Card title={`成交明细 (${deals.length})`} size="small" style={{ marginBottom: 16 }}>
        <Table dataSource={deals} columns={dealCols}
          rowKey="deal_id" size="small" pagination={{ pageSize: 20 }} loading={loading} />
      </Card>

      <Card title={`历史订单 (${orders.length})`} size="small">
        <Table dataSource={orders} columns={ordCols}
          rowKey="broker_id" size="small" pagination={{ pageSize: 20 }} loading={loading} />
      </Card>
    </div>
  );
}
