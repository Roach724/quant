import { useState, useEffect } from 'react';
import { Card, Row, Col, Statistic, Table, Tag, Empty } from 'antd';
import { api } from '../api';

export default function TradingDashboardPanel({ env }: { env: string }) {
  const [strategies, setStrategies] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    api.get('/api/admin/trading/strategies')
      .then(setStrategies)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [env]);

  const running = strategies.filter((s: any) => s.status === 'running');
  const totalEquity = strategies.reduce((s: number, x: any) => s + (x.equity || 0), 0);
  const totalCash = strategies.reduce((s: number, x: any) => s + (x.cash || 0), 0);

  const label = env === 'sim' ? '模拟看板' : '实盘看板';

  if (strategies.length === 0) {
    return (
      <Card title={label}>
        <Empty description="暂无交易策略 — 前往「量化交易」创建策略并启动" />
      </Card>
    );
  }

  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card><Statistic title="运行中" value={running.length} suffix={`/ ${strategies.length}`} /></Card></Col>
        <Col span={6}><Card><Statistic title="总权益" value={totalEquity} prefix="$" precision={0} /></Card></Col>
        <Col span={6}><Card><Statistic title="可用现金" value={totalCash} prefix="$" precision={0} /></Card></Col>
      </Row>
      <Card title="策略概览" size="small">
        <Table
          dataSource={strategies}
          rowKey="id"
          size="small"
          loading={loading}
          pagination={false}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '市场', dataIndex: 'market', width: 60, render: (v: string) => <Tag>{v?.toUpperCase()}</Tag> },
            { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => <Tag color={v === 'running' ? 'green' : 'default'}>{v}</Tag> },
            { title: '分配资金', dataIndex: 'capital_allocated', render: (v: number) => `$${v?.toLocaleString()}` },
            { title: '现金', dataIndex: 'cash', render: (v: number) => `$${v?.toLocaleString()}` },
            { title: '权益', dataIndex: 'equity', render: (v: number) => `$${v?.toLocaleString()}` },
            { title: '持仓', dataIndex: 'positions' },
          ]}
        />
      </Card>
    </div>
  );
}
