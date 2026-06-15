import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import ProTable from '@ant-design/pro-table';
import {
  Button, Space, message, Modal, Input, Select,
  InputNumber, Popconfirm, Tag, Drawer, Table, Spin, Empty, Typography,
} from 'antd';
import {
  PlayCircleOutlined, PauseCircleOutlined, PlusOutlined,
  DeleteOutlined, EyeOutlined, EditOutlined,
  DashboardOutlined, FileTextOutlined,
} from '@ant-design/icons';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import { api } from '../api';

const stripYaml = (name: string) => name.replace(/\.yaml$/, '');

const { Text } = Typography;

export default function StrategyPanel({ env, onJumpToDashboard }: { env: string; onJumpToDashboard: (id: number) => void }) {
  const navigate = useNavigate();
  const actionRef = useRef<ActionType>(undefined);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = () => setRefreshKey(k => k + 1);
  const [detail, setDetail] = useState<any>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [trades, setTrades] = useState<any[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [editName, setEditName] = useState('');

  // ── Create from template ──
  const [createOpen, setCreateOpen] = useState(false);
  const [templates, setTemplates] = useState<{ name: string; label: string }[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [createTemplate, setCreateTemplate] = useState('');
  const [createName, setCreateName] = useState('');
  const [createCapital, setCreateCapital] = useState(100000);
  const [createMarket, setCreateMarket] = useState('us');
  const prefix = env === 'sim' ? 'trading_sim_' : 'trading_real_';

  const loadTemplates = async () => {
    setTemplatesLoading(true);
    try {
      const data = await api.get('/api/admin/experiments/configs');
      const filtered = (data || [])
        .filter((c: any) => c.name.startsWith(prefix))
        .map((c: any) => ({ name: c.name, label: stripYaml(c.name).replace(prefix, '') }));
      setTemplates(filtered);
    } catch {
      message.error('加载配置模板失败');
    } finally {
      setTemplatesLoading(false);
    }
  };

  const doCreate = async () => {
    if (!createTemplate) { message.warning('请选择配置模板'); return; }
    try {
      const body: any = { template: createTemplate };
      if (createName) body.name = createName;
      if (createCapital) body.capital_allocated = createCapital;
      if (createMarket) body.market = createMarket;
      await api.post('/api/admin/trading/strategies?env=' + env, body);
      message.success('Created');
      setCreateOpen(false); setCreateTemplate(''); setCreateName(''); setCreateCapital(100000); setCreateMarket('us');
      refresh();
    } catch (e: any) {
      message.error(`创建失败: ${e.message}`);
    }
  };

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
      refresh();
    } catch (e: any) { message.error(`Save failed: ${e.message}`); }
  };

  const deleteStrat = async (id: number) => {
    await api.del(`/api/admin/trading/strategies/${id}?env=${env}`);
    message.success('Deleted'); refresh();
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
          <Button size="small" icon={<FileTextOutlined />} onClick={() => navigate(`/logs?module=trading_${env}&keyword=${r.name}`)}>日志</Button>
          {r.status !== 'running'
            ? <Button size="small" type="primary" icon={<PlayCircleOutlined />}
                onClick={async () => { await api.post(`/api/admin/trading/strategies/${r.id}/start?env=${env}`); refresh(); }}>启动</Button>
            : <Popconfirm title="停止？" onConfirm={async () => { await api.post(`/api/admin/trading/strategies/${r.id}/stop?env=${env}`); refresh(); }}>
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
        params={{ _t: refreshKey }}
        pagination={{ pageSize: 20, showSizeChanger: true }}
        request={async () => { const d = await api.get('/api/admin/trading/strategies?env='+env); return { data: d, success: true, total: d.length }; }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => { loadTemplates(); setCreateOpen(true); }}>从模板创建</Button>,
        ]}
      />
      <Modal title={env === 'sim' ? '从模板创建模拟策略' : '从模板创建实盘策略'} open={createOpen} onCancel={() => setCreateOpen(false)} onOk={doCreate}
        okText="创建">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text strong>模板:</Text>
            <Select
              style={{ width: '100%', marginTop: 4 }}
              value={createTemplate || undefined}
              onChange={setCreateTemplate}
              loading={templatesLoading}
              placeholder="选择模板..."
              showSearch
              optionFilterProp="label"
              options={templates.map(t => ({ value: t.name, label: t.label }))}
            />
          </div>
          <div>
            <Text strong>市场:</Text>
            <Select
              style={{ width: '100%', marginTop: 4 }}
              value={createMarket}
              onChange={setCreateMarket}
              options={[{ value: 'us', label: 'US' }, { value: 'hk', label: 'HK' }]}
            />
          </div>
          <div>
            <Text strong>名称（可选，覆盖模板默认名）:</Text>
            <Input
              style={{ marginTop: 4 }}
              value={createName}
              onChange={e => setCreateName(e.target.value)}
              placeholder="留空则使用模板默认名称"
            />
          </div>
          <div>
            <Text strong>分配资金（可选，覆盖模板默认值）:</Text>
            <InputNumber
              style={{ width: '100%', marginTop: 4 }}
              value={createCapital}
              onChange={v => setCreateCapital(v || 100000)}
              min={1000}
              formatter={v => `$ ${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
              parser={v => Number((v || '').replace(/\$\s?|(,*)/g, '')) as any}
            />
          </div>
        </Space>
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
