import { useRef, useState } from 'react';
import ProTable from '@ant-design/pro-table';
import {
  Button, message, Modal, Form, Input, Select,
  InputNumber, Popconfirm, Tag, Card, Statistic, Row, Col,
} from 'antd';
import {
  PlayCircleOutlined, PauseCircleOutlined, PlusOutlined,
} from '@ant-design/icons';
import { api } from '../api';

export default function StrategyPanel({ env }: { env: string }) {
  const actionRef = useRef<any>(undefined);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [summary, setSummary] = useState({ totalEquity: 0, totalCash: 0, strategies: 0 });

  const columns = [
    { title: '名称', dataIndex: 'name' },
    { title: '市场', dataIndex: 'market', width: 60, render: (_: any, r: any) => <Tag>{r.market?.toUpperCase()}</Tag> },
    { title: '策略', dataIndex: 'strategy_class' },
    { title: '状态', dataIndex: 'status', width: 80, render: (_: any, r: any) => <Tag color={r.status === 'running' ? 'green' : 'default'}>{r.status}</Tag> },
    { title: '分配资金', dataIndex: 'capital_allocated', render: (_: any, r: any) => `$${r.capital_allocated?.toLocaleString()}` },
    { title: '现金', dataIndex: 'cash', render: (_: any, r: any) => `$${r.cash?.toLocaleString()}` },
    { title: '权益', dataIndex: 'equity', render: (_: any, r: any) => `$${r.equity?.toLocaleString()}` },
    { title: '持仓', dataIndex: 'positions' },
    {
      title: '操作', key: 'actions', width: 160,
      render: (_: any, r: any) => r.status !== 'running'
        ? (
          <Button size="small" type="primary" icon={<PlayCircleOutlined />}
            onClick={async () => { await api.post(`/api/admin/trading/strategies/${r.id}/start`); actionRef.current?.reload(); }}>
            启动
          </Button>
        )
        : (
          <Popconfirm title="停止交易？"
            onConfirm={async () => { await api.post(`/api/admin/trading/strategies/${r.id}/stop`); actionRef.current?.reload(); }}>
            <Button size="small" danger icon={<PauseCircleOutlined />}>停止</Button>
          </Popconfirm>
        ),
    },
  ];

  const title = env === 'sim' ? '模拟策略' : '实盘策略';

  return (
    <>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}><Card><Statistic title="策略数" value={summary.strategies} /></Card></Col>
        <Col span={8}><Card><Statistic title="总权益" value={summary.totalEquity} prefix="$" precision={0} /></Card></Col>
        <Col span={8}><Card><Statistic title="可用现金" value={summary.totalCash} prefix="$" precision={0} /></Card></Col>
      </Row>
      <ProTable
        headerTitle={title}
        actionRef={actionRef}
        rowKey="id"
        search={false}
        columns={columns}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        request={async () => {
          const d = await api.get('/api/admin/trading/strategies');
          setSummary({
            strategies: d.length,
            totalEquity: d.reduce((s: number, x: any) => s + (x.equity || 0), 0),
            totalCash: d.reduce((s: number, x: any) => s + (x.cash || 0), 0),
          });
          return { data: d, success: true, total: d.length };
        }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建策略</Button>,
        ]}
      />
      <Modal title="新建交易策略" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={async () => {
          const v = await form.validateFields();
          await api.post('/api/admin/trading/strategies', v);
          message.success('Created');
          setCreateOpen(false);
          actionRef.current?.reload();
        }}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="策略名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="market" label="市场" initialValue="us">
            <Select options={[{ value: 'us', label: 'US' }, { value: 'hk', label: 'HK' }]} />
          </Form.Item>
          <Form.Item name="strategy_class" label="策略类" rules={[{ required: true }]}>
            <Input placeholder="SimpleMomentum" />
          </Form.Item>
          <Form.Item name="capital_allocated" label="分配资金" initialValue={100000}>
            <InputNumber min={1000} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
