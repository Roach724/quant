import { useRef, useState } from 'react';
import ProTable from '@ant-design/pro-table';
import { Button, Space, message, Modal, Input, Select, Popconfirm, Tag, Tabs, Card, Statistic, Row, Col, Tooltip, Collapse, Typography, Empty, Spin } from 'antd';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import { PlayCircleOutlined, PlusOutlined, DeleteOutlined, EyeOutlined, EditOutlined, CaretRightOutlined, PauseCircleOutlined } from '@ant-design/icons';
import { api } from '../api';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

// ── Helpers ──

const stripYaml = (name: string) => name.replace(/\.yaml$/, '');

const statusColor: Record<string, string> = {
  running: 'processing',
  success: 'success',
  completed: 'success',
  failed: 'error',
  pending: 'default',
};

const directionColor: Record<string, string> = {
  bullish: 'green',
  neutral: 'gold',
  bearish: 'red',
};

// ── Tab: 策略概览 ──

function StrategiesTab() {
  const actionRef = useRef<ActionType>(undefined);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = () => setRefreshKey(k => k + 1);

  // Create from template
  const [createOpen, setCreateOpen] = useState(false);
  const [templates, setTemplates] = useState<{ name: string; label: string }[]>([]);
  const [createTemplate, setCreateTemplate] = useState('');
  const [createName, setCreateName] = useState('');
  const [createMarket, setCreateMarket] = useState('us');
  const [createSchedule, setCreateSchedule] = useState('');

  // Detail drawer
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailRuns, setDetailRuns] = useState<any[]>([]);
  const [detailName, setDetailName] = useState('');
  const [detailLoading, setDetailLoading] = useState(false);

  const loadTemplates = async () => {
    try {
      const data = await api.get('/api/admin/ai/configs');
      setTemplates((data || []).map((c: any) => ({ name: c.name, label: c.name })));
    } catch { message.error('加载配置模板失败'); }
  };

  const doCreate = async () => {
    if (!createTemplate) { message.warning('请选择配置模板'); return; }
    try {
      await api.post('/api/admin/ai/strategies', {
        template: createTemplate,
        name: createName || undefined,
        market: createMarket,
        cron_schedule: createSchedule || undefined,
      });
      message.success('创建成功');
      setCreateOpen(false);
      resetCreate();
      refresh();
    } catch (e: any) { message.error(`创建失败: ${e.message}`); }
  };

  const resetCreate = () => {
    setCreateTemplate('');
    setCreateName('');
    setCreateMarket('us');
    setCreateSchedule('');
  };

  const openDetail = async (id: number, name: string) => {
    setDetailName(name);
    setDetailLoading(true);
    setDetailOpen(true);
    try {
      const data = await api.get(`/api/admin/ai/runs?strategy_id=${id}`);
      setDetailRuns(data || []);
    } catch { message.error('加载运行历史失败'); }
    setDetailLoading(false);
  };

  const columns: ProColumns<any>[] = [
    { title: '名称', dataIndex: 'name', width: 200 },
    { title: '市场', dataIndex: 'market', width: 80, render: (_, r) => <Tag>{r.market?.toUpperCase()}</Tag> },
    {
      title: '状态', dataIndex: 'enabled', width: 80,
      render: (_, r) => <Tag color={r.enabled ? 'green' : 'default'}>{r.enabled ? '启用' : '禁用'}</Tag>,
    },
    {
      title: '上次运行', dataIndex: 'last_run_at', width: 160,
      render: (_, r) => r.last_run_at ? new Date(r.last_run_at).toLocaleString('zh-CN') : '-',
    },
    {
      title: '上次状态', dataIndex: 'last_run_status', width: 100,
      render: (_, r) => r.last_run_status ? <Tag color={statusColor[r.last_run_status]}>{r.last_run_status}</Tag> : '-',
    },
    {
      title: '操作', key: 'actions', width: 280,
      render: (_, r) => (
        <Space>
          <Tooltip title={r.enabled ? '禁用' : '启用'}>
            <Button size="small"
              icon={r.enabled ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
              onClick={async () => {
                try {
                  await api.post(`/api/admin/ai/strategies/${r.id}/${r.enabled ? 'disable' : 'enable'}`);
                  message.success(r.enabled ? '已禁用' : '已启用');
                  refresh();
                } catch (e: any) { message.error(`操作失败: ${e.message}`); }
              }}
            />
          </Tooltip>
          <Tooltip title="立即运行">
            <Button size="small" icon={<CaretRightOutlined />} onClick={async () => {
              try { await api.post(`/api/admin/ai/strategies/${r.id}/run`); message.success('已提交运行'); refresh(); }
              catch (e: any) { message.error(`运行失败: ${e.message}`); }
            }} />
          </Tooltip>
          <Tooltip title="运行历史">
            <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r.id, r.name)} />
          </Tooltip>
          <Popconfirm title="删除此策略？所有运行记录也会删除。" onConfirm={async () => {
            try { await api.del(`/api/admin/ai/strategies/${r.id}`); message.success('Deleted'); refresh(); }
            catch (e: any) { message.error(`Delete failed: ${e.message}`); }
          }} okButtonProps={{ danger: true }}>
            <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />} /></Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <ProTable<any>
        headerTitle="AI 决策策略"
        actionRef={actionRef}
        rowKey="id"
        search={false}
        columns={columns}
        params={{ _t: refreshKey }}
        pagination={{ pageSize: 20 }}
        request={async () => {
          const data = await api.get('/api/admin/ai/strategies');
          return { data: data || [], success: true };
        }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => { loadTemplates(); setCreateOpen(true); }}>从模板创建</Button>,
        ]}
      />

      <Modal title="从模板创建 AI 策略" open={createOpen} onCancel={() => { setCreateOpen(false); resetCreate(); }}
        onOk={doCreate} okText="创建">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text strong>配置模板:</Text>
            <Select style={{ width: '100%', marginTop: 4 }} value={createTemplate || undefined}
              onChange={setCreateTemplate} placeholder="选择模板..." showSearch optionFilterProp="label"
              options={templates.map(t => ({ value: t.name, label: t.label }))} />
          </div>
          <div>
            <Text strong>名称（可选）:</Text>
            <Input style={{ marginTop: 4 }} value={createName} onChange={e => setCreateName(e.target.value)} placeholder="留空则用模板名" />
          </div>
          <div>
            <Text strong>市场:</Text>
            <Select style={{ width: '100%', marginTop: 4 }} value={createMarket} onChange={setCreateMarket}
              options={[{ value: 'us', label: 'US' }, { value: 'hk', label: 'HK' }]} />
          </div>
          <div>
            <Text strong>Cron 调度（可选）:</Text>
            <Input style={{ marginTop: 4 }} value={createSchedule} onChange={e => setCreateSchedule(e.target.value)} placeholder="如: 0 13 * * 1-5" />
          </div>
        </Space>
      </Modal>

      <Modal title={`运行历史: ${detailName}`} open={detailOpen} onCancel={() => setDetailOpen(false)}
        width={800} footer={null}>
        {detailLoading ? <Spin /> : detailRuns.length === 0 ? <Empty description="暂无运行记录" /> : (
          <ProTable<any>
            rowKey="id" search={false} pagination={{ pageSize: 10 }}
            columns={[
              { title: 'ID', dataIndex: 'id', width: 60 },
              { title: '状态', dataIndex: 'status', width: 100, render: (_, r) => <Tag color={statusColor[r.status]}>{r.status}</Tag> },
              { title: '开始时间', dataIndex: 'started_at', width: 160, render: (_, r) => new Date(r.started_at).toLocaleString('zh-CN') },
              { title: '结束时间', dataIndex: 'finished_at', width: 160, render: (_, r) => r.finished_at ? new Date(r.finished_at).toLocaleString('zh-CN') : '-' },
              {
                title: '结果', dataIndex: 'summary', render: (_, r) => {
                  if (!r.summary) return '-';
                  const s = typeof r.summary === 'string' ? JSON.parse(r.summary) : r.summary;
                  if (s.symbols_screened != null) return `筛选${s.symbols_screened} → 分析${s.symbols_analyzed} → 排序${s.symbols_ranked}`;
                  return JSON.stringify(s).slice(0, 100);
                },
              },
              { title: '错误', dataIndex: 'error', render: (_, r) => r.error ? <Text type="danger" ellipsis={{ tooltip: r.error }}>{r.error.slice(0, 80)}</Text> : '-' },
            ]}
            dataSource={detailRuns}
          />
        )}
      </Modal>
    </>
  );
}

// ── Tab: 配置管理 ──

function ConfigsTab() {
  const actionRef = useRef<ActionType>(undefined);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = () => setRefreshKey(k => k + 1);

  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editContent, setEditContent] = useState('');

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createMarket, setCreateMarket] = useState('us');
  const [createDesc, setCreateDesc] = useState('');
  const [createContent, setCreateContent] = useState('');

  const openEditor = async (name: string) => {
    try {
      const d = await api.get(`/api/admin/ai/configs/${name}`);
      setEditName(name);
      setEditContent(d.config_yaml || '');
      setEditOpen(true);
    } catch { message.error('Failed'); }
  };

  const saveEditor = async () => {
    try {
      await api.put(`/api/admin/ai/configs/${editName}`, { config_yaml: editContent });
      message.success('Saved');
      setEditOpen(false);
      refresh();
    } catch (e: any) { message.error(`Save failed: ${e.message}`); }
  };

  const doCreate = async () => {
    if (!createName.trim()) { message.warning('请输入名称'); return; }
    if (!createContent.trim()) { message.warning('请输入 YAML 内容'); return; }
    try {
      await api.post('/api/admin/ai/configs', {
        name: createName, market: createMarket, description: createDesc, config_yaml: createContent,
      });
      message.success('Created');
      setCreateOpen(false);
      setCreateName('');
      setCreateContent('');
      setCreateDesc('');
      refresh();
    } catch (e: any) { message.error(`Create failed: ${e.message}`); }
  };

  const columns: ProColumns<any>[] = [
    { title: '名称', dataIndex: 'name', width: 200 },
    { title: '市场', dataIndex: 'market', width: 80, render: (_, r) => <Tag>{r.market?.toUpperCase()}</Tag> },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: (_, r) => r.created_at ? new Date(r.created_at).toLocaleString('zh-CN') : '-' },
    {
      title: '操作', key: 'actions', width: 160,
      render: (_, r) => (
        <Space>
          <Tooltip title="编辑"><Button size="small" icon={<EditOutlined />} onClick={() => openEditor(r.name)} /></Tooltip>
          <Popconfirm title="删除此模板？" onConfirm={async () => {
            try { await api.del(`/api/admin/ai/configs/${r.name}`); message.success('Deleted'); refresh(); }
            catch (e: any) { message.error(`Delete failed: ${e.message}`); }
          }} okButtonProps={{ danger: true }}>
            <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />} /></Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <ProTable<any>
        headerTitle="配置模板"
        actionRef={actionRef}
        rowKey="name"
        search={false}
        columns={columns}
        params={{ _t: refreshKey }}
        pagination={{ pageSize: 20 }}
        request={async () => {
          const data = await api.get('/api/admin/ai/configs');
          return { data: data || [], success: true };
        }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建配置</Button>,
        ]}
      />

      <Modal title="编辑配置" open={editOpen} width={800} onCancel={() => setEditOpen(false)}
        onOk={saveEditor} okText="保存">
        <div style={{ marginBottom: 8 }}><Text strong>{editName}</Text></div>
        <TextArea rows={20} value={editContent} onChange={e => setEditContent(e.target.value)}
          style={{ fontFamily: 'monospace', fontSize: 13 }} />
      </Modal>

      <Modal title="新建配置模板" open={createOpen} width={800} onCancel={() => setCreateOpen(false)}
        onOk={doCreate} okText="创建">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text strong>名称:</Text>
            <Input style={{ marginTop: 4 }} value={createName} onChange={e => setCreateName(e.target.value)} placeholder="如: ai_us_default" />
          </div>
          <div>
            <Text strong>市场:</Text>
            <Select style={{ width: '100%', marginTop: 4 }} value={createMarket} onChange={setCreateMarket}
              options={[{ value: 'us', label: 'US' }, { value: 'hk', label: 'HK' }]} />
          </div>
          <div>
            <Text strong>描述:</Text>
            <Input style={{ marginTop: 4 }} value={createDesc} onChange={e => setCreateDesc(e.target.value)} placeholder="可选描述" />
          </div>
          <div>
            <Text strong>YAML 内容:</Text>
            <TextArea rows={15} value={createContent} onChange={e => setCreateContent(e.target.value)}
              style={{ fontFamily: 'monospace', fontSize: 13, marginTop: 4 }}
              placeholder="粘贴 AI 决策引擎配置 YAML..." />
          </div>
        </Space>
      </Modal>
    </>
  );
}

// ── Tab: 召回层（查看最新运行结果） ──

function RecallTab() {
  const [runs, setRuns] = useState<any[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [candidates, setCandidates] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const loadRuns = async () => {
    try {
      const data = await api.get('/api/admin/ai/runs?limit=20');
      setRuns(data || []);
      if ((data || []).length > 0 && !selectedRunId) {
        setSelectedRunId(data[0].id);
        loadRunDetail(data[0].id);
      }
    } catch { message.error('加载运行记录失败'); }
  };

  const loadRunDetail = async (id: number) => {
    setSelectedRunId(id);
    setLoading(true);
    try {
      const data = await api.get(`/api/admin/ai/runs/${id}`);
      const recall = data?.recall_result || [];
      setCandidates(Array.isArray(recall) ? recall : []);
    } catch { setCandidates([]); }
    setLoading(false);
  };

  const columns: ProColumns<any>[] = [
    { title: '标的', dataIndex: 'symbol', width: 120 },
    { title: '聚合得分', dataIndex: 'aggregate_score', width: 100, render: (_, r) => r.aggregate_score?.toFixed(3) },
    { title: '命中策略数', dataIndex: 'hitting_count', width: 100 },
    {
      title: '详细信号', dataIndex: 'hitting_strategies', ellipsis: true,
      render: (_, r) => {
        const sigs = r.hitting_strategies || r.signals || [];
        if (!sigs.length) return '-';
        return (
          <Collapse ghost size="small" items={[{
            key: 'sig', label: `${sigs.length} 个信号`,
            children: sigs.map((s: any, i: number) => (
              <div key={i} style={{ fontSize: 12, marginBottom: 2 }}>
                <Tag color={directionColor[s.direction]}>{s.direction}</Tag>
                {s.strategy}: {typeof s.score === 'number' ? s.score.toFixed(3) : s.score}
              </div>
            )),
          }]} />
        );
      },
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select style={{ width: 320 }} value={selectedRunId} onChange={loadRunDetail}
          placeholder="选择运行记录..." options={runs.map(r => ({
            value: r.id,
            label: `#${r.id} ${new Date(r.started_at).toLocaleString('zh-CN')} [${r.status}]`,
          }))} />
        <Button onClick={loadRuns}>刷新</Button>
      </Space>
      {loading ? <Spin /> : (
        candidates.length === 0 ? <Empty description="无候选数据（请先运行策略）" /> : (
          <ProTable<any>
            rowKey="symbol" search={false} pagination={{ pageSize: 20 }}
            columns={columns} dataSource={candidates}
          />
        )
      )}
    </div>
  );
}

// ── Tab: 分析层 ──

function AnalysisTab() {
  const [runs, setRuns] = useState<any[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [reports, setReports] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const loadRuns = async () => {
    try {
      const data = await api.get('/api/admin/ai/runs?limit=20');
      setRuns(data || []);
      if ((data || []).length > 0 && !selectedRunId) {
        setSelectedRunId(data[0].id);
        loadRunDetail(data[0].id);
      }
    } catch { message.error('加载运行记录失败'); }
  };

  const loadRunDetail = async (id: number) => {
    setSelectedRunId(id);
    setLoading(true);
    try {
      const data = await api.get(`/api/admin/ai/runs/${id}`);
      const analysis = data?.analysis_result || [];
      setReports(Array.isArray(analysis) ? analysis : []);
    } catch { setReports([]); }
    setLoading(false);
  };

  const columns: ProColumns<any>[] = [
    { title: '标的', dataIndex: 'symbol', width: 100 },
    {
      title: '方向', dataIndex: 'direction', width: 80,
      render: (_, r) => <Tag color={directionColor[r.direction]}>{r.direction}</Tag>,
    },
    { title: '置信度', dataIndex: 'confidence', width: 80, render: (_, r) => (r.confidence * 100).toFixed(0) + '%' },
    { title: '评级', dataIndex: 'rating', width: 80 },
    {
      title: '关键观点', dataIndex: 'key_arguments', ellipsis: true,
      render: (_, r) => (r.key_arguments || []).join('; '),
    },
    {
      title: '详情', key: 'detail', width: 80,
      render: (_, r) => {
        const args = r.key_arguments || [];
        const risks = r.risk_factors || [];
        const coverage = r.data_coverage || {};
        return (
          <Tooltip title={
            <div>
              <div><Text strong>关键观点:</Text></div>
              {args.map((a: string, i: number) => <div key={i}>• {a}</div>)}
              {risks.length > 0 && (<><div style={{ marginTop: 8 }}><Text strong>风险因素:</Text></div>
                {risks.map((r: string, i: number) => <div key={i}>⚠ {r}</div>)}</>)}
              <div style={{ marginTop: 8 }}><Text strong>数据覆盖:</Text></div>
              {Object.entries(coverage).map(([k, v]) => <div key={k}>{k}: {v ? '✅' : '❌'}</div>)}
            </div>
          } color="default" overlayStyle={{ maxWidth: 400 }}>
            <Button size="small" icon={<EyeOutlined />} />
          </Tooltip>
        );
      },
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select style={{ width: 320 }} value={selectedRunId} onChange={loadRunDetail}
          placeholder="选择运行记录..." options={runs.map(r => ({
            value: r.id,
            label: `#${r.id} ${new Date(r.started_at).toLocaleString('zh-CN')} [${r.status}]`,
          }))} />
        <Button onClick={loadRuns}>刷新</Button>
      </Space>
      {loading ? <Spin /> : (
        reports.length === 0 ? <Empty description="无分析数据（请先运行策略）" /> : (
          <ProTable<any>
            rowKey="symbol" search={false} pagination={{ pageSize: 20 }}
            columns={columns} dataSource={reports}
          />
        )
      )}
    </div>
  );
}

// ── Tab: 决策层 ──

function DecisionTab() {
  const [runs, setRuns] = useState<any[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [decision, setDecision] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const loadRuns = async () => {
    try {
      const data = await api.get('/api/admin/ai/runs?limit=20');
      setRuns(data || []);
      if ((data || []).length > 0 && !selectedRunId) {
        setSelectedRunId(data[0].id);
        loadRunDetail(data[0].id);
      }
    } catch { message.error('加载运行记录失败'); }
  };

  const loadRunDetail = async (id: number) => {
    setSelectedRunId(id);
    setLoading(true);
    try {
      const data = await api.get(`/api/admin/ai/runs/${id}`);
      setDecision(data?.decision_result || data);
    } catch { setDecision(null); }
    setLoading(false);
  };

  const buyOrders = decision?.buy_orders || [];
  const sellOrders = decision?.sell_orders || [];
  const summary = decision?.summary || {};

  const orderColumns: ProColumns<any>[] = [
    { title: '标的', dataIndex: 'symbol', width: 100 },
    { title: '方向', dataIndex: 'side', width: 80, render: (_, r) => <Tag color={r.side === 'buy' ? 'green' : 'red'}>{r.side?.toUpperCase()}</Tag> },
    { title: '数量', dataIndex: 'quantity', width: 80 },
    { title: '估值', dataIndex: 'estimated_value', width: 100, render: (_, r) => r.estimated_value ? `$${r.estimated_value.toFixed(2)}` : '-' },
    { title: '原因', dataIndex: 'reason', ellipsis: true },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Select style={{ width: 320 }} value={selectedRunId} onChange={loadRunDetail}
          placeholder="选择运行记录..." options={runs.map(r => ({
            value: r.id,
            label: `#${r.id} ${new Date(r.started_at).toLocaleString('zh-CN')} [${r.status}]`,
          }))} />
        <Button onClick={loadRuns}>刷新</Button>
      </Space>
      {loading ? <Spin /> : !decision ? <Empty description="无决策数据（请先运行策略）" /> : (
        <>
          {/* Summary Cards */}
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={6}><Card size="small"><Statistic title="操作" value={summary.action || 'rebalance'} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="换手率" value={summary.turnover_pct ? `${(summary.turnover_pct * 100).toFixed(1)}%` : '-'} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="剩余现金" value={summary.remaining_cash_estimate ? `$${summary.remaining_cash_estimate.toFixed(0)}` : '-'} /></Card></Col>
            <Col span={6}><Card size="small"><Statistic title="净现金变动" value={summary.net_cash_change ? `$${summary.net_cash_change.toFixed(0)}` : '-'} /></Card></Col>
          </Row>

          {summary.no_op_reason && (
            <Card size="small" style={{ marginBottom: 16, backgroundColor: '#fffbe6' }}>
              <Text type="warning">不调仓: {summary.no_op_reason}</Text>
            </Card>
          )}

          {/* Sell Orders */}
          <Card title={`卖出 (${sellOrders.length})`} size="small" style={{ marginBottom: 16 }}>
            {sellOrders.length === 0 ? <Empty description="无卖出" /> : (
              <ProTable<any> rowKey="symbol" search={false} pagination={false} columns={orderColumns} dataSource={sellOrders} />
            )}
          </Card>

          {/* Buy Orders */}
          <Card title={`买入 (${buyOrders.length})`} size="small">
            {buyOrders.length === 0 ? <Empty description="无买入" /> : (
              <ProTable<any> rowKey="symbol" search={false} pagination={false} columns={orderColumns} dataSource={buyOrders} />
            )}
          </Card>

          {/* Sector Distribution */}
          {summary.sector_distribution && Object.keys(summary.sector_distribution).length > 0 && (
            <Card title="行业分布" size="small" style={{ marginTop: 16 }}>
              {Object.entries(summary.sector_distribution).map(([sector, pct]) => (
                <Tag key={sector} style={{ marginBottom: 4 }}>{(pct as number * 100).toFixed(1)}% {sector}</Tag>
              ))}
            </Card>
          )}
        </>
      )}
    </div>
  );
}

// ── Main Page ──

export default function AIDecision() {
  return (
    <Tabs
      defaultActiveKey="strategies"
      tabBarStyle={{ marginBottom: 16 }}
      items={[
        { key: 'strategies', label: '策略概览', children: <StrategiesTab /> },
        { key: 'configs', label: '配置管理', children: <ConfigsTab /> },
        { key: 'recall', label: '召回层', children: <RecallTab /> },
        { key: 'analysis', label: '分析层', children: <AnalysisTab /> },
        { key: 'decision', label: '决策层', children: <DecisionTab /> },
      ]}
    />
  );
}
