import { useRef, useState } from 'react';
import ProTable from '@ant-design/pro-table';
import {
  Button, Space, message, Modal, Form, Input, Select,
  InputNumber, Popconfirm, Tag, Drawer, Table, Spin, Empty,
} from 'antd';
import {
  PlayCircleOutlined, PauseCircleOutlined, PlusOutlined,
  DeleteOutlined, EyeOutlined, EditOutlined,
  DashboardOutlined,
} from '@ant-design/icons';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import { api } from '../api';

export default function StrategyPanel({ env, onJumpToDashboard }: { env: string; onJumpToDashboard: (id: number) => void }) {
  const actionRef = useRef<ActionType>(undefined);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();
  const [detail, setDetail] = useState<any>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [trades, setTrades] = useState<any[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [editName, setEditName] = useState('');

  const openDetail = async (strat: any) => {
    setDetail(strat); setDrawerOpen(true); setDetailLoading(true);
    try { const t = await api.get(`/api/admin/trading/strategies/${strat.id}/trades?env=${env}`); setTrades(t || []); }
    catch { setTrades([]); } finally { setDetailLoading(false); }
  };

  const openEdit = (strat: any) => {
    setEditName(strat.name); setEditContent(strat.config_yaml || ''); setEditOpen(true);
  };

  const saveEdit = async () => {
    try {
      await api.put(`/api/admin/trading/strategies/${detail.id}?env=${env}`, { config_yaml: editContent, name: editName });
      message.success('Saved'); setEditOpen(false);
      // Refresh detail
      setDetail({ ...detail, name: editName, config_yaml: editContent });
      actionRef.current?.reload();
    } catch (e: any) { message.error(`Save failed: ${e.message}`); }
  };

  const deleteStrat = async (id: number) => {
    await api.del(`/api/admin/trading/strategies/${id}?env=${env}`);
    message.success('Deleted'); actionRef.current?.reload();
    setDrawerOpen(false);
  };

  const columns: ProColumns<any>[] = [
    { title: '名称', dataIndex: 'name' },
    { title: '市场', dataIndex: 'market', width: 60, render: (_: any, r: any) => <Tag>{r.market?.toUpperCase()}</Tag> },
    { title: '策略', dataIndex: 'strategy_class' },
    { title: '状态', dataIndex: 'status', width: 80, render: (_: any, r: any) => <Tag color={r.status === 'running' ? 'green' : 'default'}>{r.status}</Tag> },
    { title: '资金', dataIndex: 'capital_allocated', render: (_: any, r: any) => `$${r.capital_allocated?.toLocaleString()}` },
    { title: '权益', dataIndex: 'equity', render: (_: any, r: any) => `$${r.equity?.toLocaleString()}` },
    { title: '持仓', dataIndex: 'positions' },
    {
      title: '操作', key: 'actions', width: 280,
      render: (_: any, r: any) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)}>详情</Button>
          <Button size="small" icon={<DashboardOutlined />} onClick={() => onJumpToDashboard(r.id)}>看板</Button>
          {r.status !== 'running'
            ? <Button size="small" type="primary" icon={<PlayCircleOutlined />}
                onClick={async () => { await api.post(`/api/admin/trading/strategies/${r.id}/start?env=${env}`); actionRef.current?.reload(); }}>启动</Button>
            : <Popconfirm title="停止？" onConfirm={async () => { await api.post(`/api/admin/trading/strategies/${r.id}/stop?env=${env}`); actionRef.current?.reload(); }}>
                <Button size="small" danger icon={<PauseCircleOutlined />}>停止</Button></Popconfirm>}
        </Space>
      ),
    },
  ];

  const tradeCols = [
    { title: '时间', dataIndex: 'created_at', width: 160, render: (v: string) => v?.slice(0, 19) || '-' },
    { title: '代码', dataIndex: 'symbol' },
    { title: '方向', dataIndex: 'side', width: 50, render: (v: string) => <span style={{ color: v === 'BUY' ? '#3f8600' : '#cf1322' }}>{v}</span> },
    { title: '数量', dataIndex: 'qty', width: 60 },
    { title: '价格', dataIndex: 'price', render: (v: number) => `$${(v || 0).toFixed(2)}` },
    { title: '佣金', dataIndex: 'commission', render: (v: number) => `$${(v || 0).toFixed(2)}` },
  ];

  return (
    <>
      <ProTable
        headerTitle={env === 'sim' ? '模拟策略' : '实盘策略'}
        actionRef={actionRef} rowKey="id" search={false} columns={columns}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        request={async () => { const d = await api.get('/api/admin/trading/strategies?env='+env); return { data: d, success: true, total: d.length }; }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建策略</Button>,
        ]}
      />
      <Modal title="新建策略" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={async () => { const v = await form.validateFields(); await api.post('/api/admin/trading/strategies?env='+env, { ...v, env }); message.success('Created'); setCreateOpen(false); actionRef.current?.reload(); }}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="market" label="市场" initialValue="us"><Select options={[{value:'us',label:'US'},{value:'hk',label:'HK'}]} /></Form.Item>
          <Form.Item name="strategy_class" label="策略类" rules={[{ required: true }]}><Input placeholder="SimpleMomentum" /></Form.Item>
          <Form.Item name="capital_allocated" label="分配资金" initialValue={100000}><InputNumber min={1000} style={{ width: '100%' }} /></Form.Item>
        </Form>
      </Modal>

      <Drawer title={detail?.name || '策略详情'} open={drawerOpen} onClose={() => setDrawerOpen(false)} width={700}
        extra={
          <Space>
            {detail && (
              <>
                <Button icon={<EditOutlined />} onClick={() => openEdit(detail)}>编辑</Button>
                <Button icon={<DashboardOutlined />} onClick={() => { onJumpToDashboard(detail.id); setDrawerOpen(false); }}>量化看板</Button>
                <Popconfirm title="永久删除？" onConfirm={() => deleteStrat(detail.id)} okButtonProps={{ danger: true }}>
                  <Button danger icon={<DeleteOutlined />}>删除</Button>
                </Popconfirm>
              </>
            )}
          </Space>
        }>
        {detail && (
          <Spin spinning={detailLoading}>
            <Table
              dataSource={[
                { label: '市场', value: detail.market?.toUpperCase() },
                { label: '策略类', value: detail.strategy_class },
                { label: '状态', value: detail.status },
                { label: '分配资金', value: `$${detail.capital_allocated?.toLocaleString()}` },
                { label: '现金', value: `$${detail.cash?.toLocaleString()}` },
                { label: '权益', value: `$${detail.equity?.toLocaleString()}` },
                { label: '持仓数', value: detail.positions || 0 },
              ]}
              columns={[
                { title: '字段', dataIndex: 'label', width: 120 },
                { title: '值', dataIndex: 'value' },
              ]}
              rowKey="label" size="small" pagination={false} showHeader={false}
              style={{ marginBottom: 16 }}
            />
            <Table
              title={() => '交易记录'}
              dataSource={trades} columns={tradeCols} rowKey="id"
              size="small" pagination={{ pageSize: 15 }}
              locale={{ emptyText: <Empty description="暂无交易" /> }}
            />
          </Spin>
        )}
      </Drawer>

      <Drawer title={`编辑 ${editName}`} open={editOpen} onClose={() => setEditOpen(false)} width={600}
        extra={<Button type="primary" onClick={saveEdit}>保存</Button>}>
        <Input addonBefore="名称" value={editName} onChange={e => setEditName(e.target.value)} style={{ marginBottom: 12 }} />
        <Input.TextArea value={editContent} onChange={e => setEditContent(e.target.value)}
          rows={25} style={{ fontFamily: 'monospace', fontSize: 12 }} placeholder="YAML 配置..." />
      </Drawer>
    </>
  );
}
