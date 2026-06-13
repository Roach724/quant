import { useState, useEffect } from 'react';
import {
  Card, Row, Col, Statistic, Table, Button, Space,
  Input, InputNumber, Select, Radio, Tag, message,
} from 'antd';
import { api } from '../api';

export default function AccountPanel({ env }: { env: 'sim' | 'real' }) {
  const [acct, setAcct] = useState<any>({});
  const [orders, setOrders] = useState<any[]>([]);
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [symbol, setSymbol] = useState('');
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market');
  const [price, setPrice] = useState(0);
  const [qty, setQty] = useState(100);
  const [loading, setLoading] = useState(false);
  const [market, setMarket] = useState('hk');

  const fetchData = async () => {
    setLoading(true);
    try {
      const [a, o] = await Promise.all([
        api.get(`/api/admin/trading/account/${env}?market=${market}`),
        api.get(`/api/admin/trading/orders/${env}?market=${market}`),
      ]);
      setAcct(a); setOrders(o);
    } catch { message.error('加载失败'); }
    setLoading(false);
  };

  useEffect(() => {
    fetchData();
    const i = setInterval(fetchData, 600000);
    return () => clearInterval(i);
  }, [env, market]);

  const placeOrder = async () => {
    if (!symbol) { message.warning('请输入代码'); return; }
    try {
      const r = await api.post(`/api/admin/trading/order/${env}`, {
        symbol, side, qty, market,
        order_type: orderType,
        limit_price: orderType === 'limit' ? price : undefined,
      });
      message.success(`已提交: ${r.order_id}`);
      fetchData();
    } catch (e: any) { message.error(`下单失败: ${e.message}`); }
  };

  const cancelOrder = async (orderId: string) => {
    try {
      await api.post(`/api/admin/trading/order/${env}/cancel/${orderId}?market=${market}`);
      message.success(`已撤单: ${orderId}`);
      fetchData();
    } catch (e: any) { message.error(`撤单失败: ${e.message}`); }
  };

  const posCols = [
    { title: '代码', dataIndex: 'symbol', width: 100 },
    { title: '市值', dataIndex: 'market_value', render: (v: number) => `$${v?.toLocaleString()}` },
    { title: '数量', dataIndex: 'qty' },
    { title: '成本价', dataIndex: 'avg_entry_price', render: (v: number) => `$${(v || 0).toFixed(2)}` },
    {
      title: '盈亏', dataIndex: 'unrealized_pnl',
      render: (v: number) => (
        <span style={{ color: v >= 0 ? '#3f8600' : '#cf1322' }}>
          ${(v || 0).toFixed(2)}
        </span>
      ),
    },
  ];

  const ordCols = [
    { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => <Tag>{v}</Tag> },
    { title: '代码', dataIndex: 'symbol', width: 100 },
    { title: '方向', dataIndex: 'side', width: 50, render: (v: string) => <Tag color={v === 'buy' ? 'green' : 'red'}>{v?.toUpperCase()}</Tag> },
    { title: '数量', dataIndex: 'qty', width: 60 },
    { title: '类型', dataIndex: 'order_type', width: 60 },
    { title: '时间', dataIndex: 'created_at', width: 140 },
    { title: '操作', key: 'act', width: 60, render: (_: any, r: any) =>
      r.status === 'submitted' || r.status === 'pending'
        ? <Button size="small" danger onClick={() => cancelOrder(r.broker_id)}>撤单</Button>
        : null
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <span style={{ fontWeight: 600 }}>账户:</span>
        <Select value={market} onChange={setMarket} style={{ width: 100 }}
          options={[{ value: 'hk', label: '🇭🇰 HK' }, { value: 'us', label: '🇺🇸 US' }]} />
        <Button size="small" onClick={fetchData}>刷新</Button>
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card><Statistic title="资产净值" value={acct.equity || 0} prefix="$" precision={0} /></Card></Col>
        <Col span={6}><Card><Statistic title="持仓市值" value={acct.market_value || 0} prefix="$" precision={0} /></Card></Col>
        <Col span={6}><Card><Statistic title="持仓盈亏" value={acct.total_pnl || 0} prefix="$" precision={2}
          valueStyle={{ color: (acct.total_pnl || 0) >= 0 ? '#3f8600' : '#cf1322' }} /></Card></Col>
        <Col span={6}><Card><Statistic title="盈亏率" value={acct.pnl_pct || 0} suffix="%" precision={2}
          valueStyle={{ color: (acct.pnl_pct || 0) >= 0 ? '#3f8600' : '#cf1322' }} /></Card></Col>
      </Row>

      <Card title="下单" size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Radio.Group value={side} onChange={e => setSide(e.target.value)}>
              <Radio.Button value="buy" style={{ color: '#3f8600' }}>买入</Radio.Button>
              <Radio.Button value="sell" style={{ color: '#cf1322' }}>卖出</Radio.Button>
            </Radio.Group>
            <Select value={orderType} onChange={setOrderType} style={{ width: 100 }}
              options={[{ value: 'market', label: '市价单' }, { value: 'limit', label: '限价单' }]} />
          </Space>
          <Space>
            <Input placeholder="代码 (e.g. HK.00700)" value={symbol}
              onChange={e => setSymbol(e.target.value)} style={{ width: 160 }} />
            <span>价格:</span>
            <Button size="small" onClick={() => setPrice(p => Math.max(0, p - 0.1))}>-</Button>
            <InputNumber value={price} onChange={v => setPrice(v || 0)} step={0.1}
              style={{ width: 100 }} disabled={orderType === 'market'} />
            <Button size="small" onClick={() => setPrice(p => p + 0.1)}>+</Button>
            <span>数量:</span>
            <Button size="small" onClick={() => setQty(q => Math.max(1, q - 1))}>-</Button>
            <InputNumber value={qty} onChange={v => setQty(v || 1)} step={1} min={1} style={{ width: 80 }} />
            <Button size="small" onClick={() => setQty(q => q + 1)}>+</Button>
            <span>金额: ${((price || 0) * qty).toLocaleString()}</span>
            <Button type="primary" onClick={placeOrder}>下单</Button>
          </Space>
        </Space>
      </Card>

      <Card title="持仓" size="small" style={{ marginBottom: 16 }}>
        <Table dataSource={acct.positions || []} columns={posCols}
          rowKey="symbol" size="small" pagination={false} loading={loading} />
      </Card>

      <Card title="订单" size="small">
        <Table dataSource={orders} columns={ordCols}
          rowKey="broker_id" size="small" pagination={{ pageSize: 20 }} loading={loading} />
      </Card>
    </div>
  );
}
