import { useState, useEffect } from 'react';
import {
  PlusOutlined, DeleteOutlined, SettingOutlined, EyeOutlined,
  ThunderboltOutlined, ExperimentOutlined, ReloadOutlined,
} from '@ant-design/icons';
import {
  Tabs, Table, Button, Space, Modal, Select, Input, DatePicker,
  Tag, Popconfirm, message, Checkbox, Typography, Drawer, Tooltip,
} from 'antd';
import { api } from '../api';

const { Text } = Typography;
const { RangePicker } = DatePicker;

interface FactorItem { factor_id: string; source: string; label: string; }
interface DatasetItem { id: number; name: string; market: string; label: string; factor_ids: string[]; train_range: string; val_range: string; test_range: string; bq_table: string | null; status: string; row_count: number; }
interface ConfigItem { id: number; name: string; description: string; config_path: string; dataset_name: string; registry_model_name: string | null; status: string; }
interface CenterItem { model_name: string; dataset_name: string; config_name: string; versions: VersionDetail[]; }
interface VersionDetail { version: string; stage: string; run_id: string; rmse?: number; ic?: number; icir?: number; n_features?: number; dataset?: string; }

const stageColor: Record<string, string> = { Production: 'green', Staging: 'orange', Archived: 'default', None: 'default' };

// ═══════════════════════════════════════════════════════════════════════════════
// ModelsPage
// ═══════════════════════════════════════════════════════════════════════════════

const ModelsPage: React.FC = () => {
  const [tab, setTab] = useState('datasets');
  return (
    <div>
      <div style={{ fontSize: 11, color: '#bbb', marginBottom: 8 }}>v2 — 模型中心 · ML 配置 · 数据集</div>
    <Tabs activeKey={tab} onChange={setTab} items={[
      { key: 'datasets', label: '数据集', children: <DatasetsTab /> },
      { key: 'configs', label: 'ML 配置', children: <MlConfigsTab /> },
      { key: 'center', label: '模型中心', children: <ModelCenterTab /> },
      { key: 'strategies', label: '策略', children: <StrategiesTab /> },
      { key: 'mlflow', label: 'MLflow', children: <MlflowTab /> },
    ]} />
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// DatasetsTab
// ═══════════════════════════════════════════════════════════════════════════════

const DatasetsTab: React.FC = () => {
  const [datasets, setDatasets] = useState<DatasetItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [cName, setCName] = useState(''); const [cMarket, setCMarket] = useState('us'); const [cLabel, setCLabel] = useState('fwd_ret_5d');
  const [cFactors, setCFactors] = useState<FactorItem[]>([]); const [cSelectedFactors, setCSelectedFactors] = useState<string[]>([]);
  const [cSourceFilter, setCSourceFilter] = useState('all');
  const [cTrainRange, setCTrainRange] = useState<[string, string] | null>(null); const [cValRange, setCValRange] = useState<[string, string] | null>(null); const [cTestRange, setCTestRange] = useState<[string, string] | null>(null);

  useEffect(() => { (async () => { setLoading(true); try { setDatasets(await api.get('/api/admin/ml/datasets')); } catch { } finally { setLoading(false); } })(); }, []);

  const openCreate = async () => {
    setCName(''); setCMarket('us'); setCLabel('fwd_ret_5d');
    setCSelectedFactors([]); setCSourceFilter('all');
    setCTrainRange(null); setCValRange(null); setCTestRange(null);
    setCreateOpen(true); fetchFactors('us');
  };
  const fetchFactors = async (market: string) => {
    try { setCFactors(await api.get(`/api/admin/ml/datasets/${market}/factors`)); } catch { }
  };
  const doCreate = async () => {
    try { await api.post('/api/admin/ml/datasets', { name: cName, market: cMarket, label: cLabel, factor_ids: cSelectedFactors, train_start: cTrainRange?.[0] || '', train_end: cTrainRange?.[1] || '', val_start: cValRange?.[0] || '', val_end: cValRange?.[1] || '', test_start: cTestRange?.[0] || '', test_end: cTestRange?.[1] || '' }); message.success('Registered'); setCreateOpen(false); (async () => { setDatasets(await api.get('/api/admin/ml/datasets')); })(); }
    catch (e: any) { message.error(e.message); }
  };

  return (
    <>
      <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} style={{ marginBottom: 16 }}>新建数据集</Button>
      <Tooltip title="刷新"><Button icon={<ReloadOutlined />} onClick={() => { (async () => { setDatasets(await api.get('/api/admin/ml/datasets')); })(); }} style={{ marginBottom: 16, marginLeft: 8 }} /></Tooltip>
      <Table dataSource={datasets} rowKey="id" loading={loading} size="small" columns={[
        { title: '名称', dataIndex: 'name', width: 160 },
        { title: '市场', dataIndex: 'market', width: 60, render: (v: string) => <Tag>{v.toUpperCase()}</Tag> },
        { title: 'Label', dataIndex: 'label', width: 120 },
        { title: 'BQ表', dataIndex: 'bq_table', width: 200, ellipsis: true, render: (v: string | null) => v ? <Text style={{ fontFamily: 'monospace', fontSize: 11, color: '#1677ff' }}>{v.split('.').pop()}</Text> : <Text type="secondary">—</Text> },
        { title: '操作', width: 160, render: (_, r) => (<Space>
          <Popconfirm title="生成/覆盖？" onConfirm={async () => { try { const res = await api.post(`/api/admin/ml/datasets/${r.id}/generate`); message.success(`${res.row_count || 0} rows`); (async () => { setDatasets(await api.get('/api/admin/ml/datasets')); })(); } catch (e: any) { message.error(e.message); } }}>
            <Button size="small" type="primary" icon={<ThunderboltOutlined />}>生成</Button>
          </Popconfirm>
          <Popconfirm title="删除？" onConfirm={async () => { await api.del(`/api/admin/ml/datasets/${r.id}`); (async () => { setDatasets(await api.get('/api/admin/ml/datasets')); })(); }} okButtonProps={{ danger: true }}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>) },
      ]} />
      <Modal title="新建数据集" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={doCreate} okText="创建" width={800}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space><Text strong>名称:</Text><Input value={cName} onChange={e => setCName(e.target.value)} style={{ width: 200 }} /><Text strong>市场:</Text><Select value={cMarket} onChange={(v) => { setCMarket(v); setCSelectedFactors([]); fetchFactors(v); }} style={{ width: 80 }} options={['us', 'hk'].map(m => ({ value: m, label: m.toUpperCase() }))} /><Text strong>Label:</Text><Select value={cLabel} onChange={setCLabel} style={{ width: 140 }} options={['fwd_ret_5d', 'fwd_ret_20d'].map(l => ({ value: l, label: l }))} /></Space>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Space><Text strong>因子类型:</Text>
              <Select value={cSourceFilter} onChange={setCSourceFilter} style={{ width: 120 }} options={[
                { value: 'all', label: '全部' },
                ...Array.from(new Set(cFactors.map(f => f.source))).map(s => ({ value: s, label: s })),
              ]} />
              <Text type="secondary">{cSelectedFactors.length} / {cFactors.filter(f => cSourceFilter === 'all' || f.source === cSourceFilter).length} 已选择</Text>
            </Space>
            <Space style={{ marginBottom: 4 }}>
              <Checkbox
                checked={cSelectedFactors.length === cFactors.filter(f => cSourceFilter === 'all' || f.source === cSourceFilter).length && cFactors.filter(f => cSourceFilter === 'all' || f.source === cSourceFilter).length > 0}
                indeterminate={cSelectedFactors.length > 0 && cSelectedFactors.length < cFactors.filter(f => cSourceFilter === 'all' || f.source === cSourceFilter).length}
                onChange={(e) => {
                  if (e.target.checked) {
                    const filtered = cFactors.filter(f => cSourceFilter === 'all' || f.source === cSourceFilter).map(f => f.factor_id);
                    setCSelectedFactors([...new Set([...cSelectedFactors, ...filtered])]);
                  } else {
                    const filteredIds = cFactors.filter(f => cSourceFilter === 'all' || f.source === cSourceFilter).map(f => f.factor_id);
                    setCSelectedFactors(cSelectedFactors.filter(f => !filteredIds.includes(f)));
                  }
                }}>
                全选当前筛选
              </Checkbox>
            </Space>
            <Checkbox.Group style={{ maxHeight: 300, overflow: 'auto', display: 'flex', flexDirection: 'column' }}
              value={cSelectedFactors} onChange={(v) => setCSelectedFactors(v as string[])}
              options={cFactors.filter(f => cSourceFilter === 'all' || f.source === cSourceFilter).map(f => ({ label: `${f.factor_id} (${f.source})`, value: f.factor_id }))} /></Space>
          <Space><Text strong>训练:</Text><RangePicker onChange={(d) => d && d[0] && d[1] ? setCTrainRange([d[0].format('YYYY-MM-DD'), d[1].format('YYYY-MM-DD')]) : setCTrainRange(null)} /></Space>
          <Space><Text strong>验证:</Text><RangePicker onChange={(d) => d && d[0] && d[1] ? setCValRange([d[0].format('YYYY-MM-DD'), d[1].format('YYYY-MM-DD')]) : setCValRange(null)} /></Space>
          <Space><Text strong>测试:</Text><RangePicker onChange={(d) => d && d[0] && d[1] ? setCTestRange([d[0].format('YYYY-MM-DD'), d[1].format('YYYY-MM-DD')]) : setCTestRange(null)} /></Space>
        </Space>
      </Modal>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// MlConfigsTab
// ═══════════════════════════════════════════════════════════════════════════════

const MlConfigsTab: React.FC = () => {
  const [configs, setConfigs] = useState<ConfigItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorName, setEditorName] = useState('');
  const [editorContent, setEditorContent] = useState('');
  const [newConfigName, setNewConfigName] = useState('');

  useEffect(() => { (async () => { setLoading(true); try { setConfigs(await api.get('/api/admin/ml/configs')); } catch { } finally { setLoading(false); } })(); }, []);

  const openEditor = async (name: string) => { try { const d = await api.get(`/api/admin/ml/configs/${name}`); setEditorName(name); setEditorContent(d.content || ''); setEditorOpen(true); } catch { message.error('Failed'); } };
  const saveEditor = async () => { try { await api.put(`/api/admin/ml/configs/${editorName}`, { content: editorContent, description: '' }); message.success('Saved'); setEditorOpen(false); (async () => { setConfigs(await api.get('/api/admin/ml/configs')); })(); } catch (e: any) { message.error(e.message); } };

  return (
    <>
      <Space style={{ marginBottom: 16 }}><Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditorName(''); setEditorContent(''); setEditorOpen(true); }}>新建配置</Button><Tooltip title="刷新"><Button icon={<ReloadOutlined />} onClick={() => { (async () => { setConfigs(await api.get("/api/admin/ml/configs")); })(); }} /></Tooltip></Space>
      <Table dataSource={configs} rowKey="id" loading={loading} size="small" columns={[
        { title: '配置名', dataIndex: 'name', width: 180 },
        { title: '数据集', dataIndex: 'dataset_name', width: 140 },
        { title: '模型名', dataIndex: 'registry_model_name', width: 140 },
        { title: '状态', dataIndex: 'status', width: 100, render: (v) => v === 'registered' ? <Tag color="green">已注册</Tag> : <Tag>草稿</Tag> },
        { title: '操作', width: 200, render: (_, r) => (<Space>
          <Button size="small" icon={<SettingOutlined />} onClick={() => openEditor(r.name)}>编辑</Button>
          <Popconfirm title="删除？" onConfirm={async () => { try { await api.del(`/api/admin/ml/configs/${r.name}`); (async () => { setConfigs(await api.get('/api/admin/ml/configs')); })(); } catch (e: any) { message.error(e.message); } }} okButtonProps={{ danger: true }}><Button size="small" danger icon={<DeleteOutlined />} /></Popconfirm>
        </Space>) },
      ]} />
      <Drawer title={editorName ? `编辑: ${editorName}` : '新建配置'} open={editorOpen} onClose={() => setEditorOpen(false)} width={700} extra={<Button type="primary" onClick={saveEditor}>保存</Button>}>
        {!editorName && (
          <Space style={{ marginBottom: 12 }}>
            <Input placeholder="配置文件名" value={newConfigName} onChange={e => setNewConfigName(e.target.value)} style={{ width: 200 }} />
            <Text type="secondary">.yaml</Text>
            <Button size="small" onClick={() => { setEditorName(newConfigName + '.yaml'); setNewConfigName(''); }} disabled={!newConfigName}>确定</Button>
          </Space>
        )}
        {editorName && (
          <Input.TextArea value={editorContent} onChange={e => setEditorContent(e.target.value)} rows={30} style={{ fontFamily: 'monospace', fontSize: 12 }} placeholder={`data:\n  dataset: us_tech_v1\n  label: fwd_ret_5d\n\nmodel:\n  type: lightgbm\n...`} />
        )}
      </Drawer>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// ModelCenterTab
// ═══════════════════════════════════════════════════════════════════════════════

const ModelCenterTab: React.FC = () => {
  const [items, setItems] = useState<CenterItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [createTemplate, setCreateTemplate] = useState('');
  const [templateList, setTemplateList] = useState<any[]>([]);

  const load = async () => { setLoading(true); try { setItems(await api.get('/api/admin/ml/center')); } catch { } finally { setLoading(false); } };
  useEffect(() => { load(); }, []);

  const doTrain = async (configName: string, skipTuning: boolean) => {
    try { const r = await api.post('/api/admin/ml/train', { config_name: configName, skip_tuning: skipTuning }); message.success(`Task #${r.task_id} submitted`); }
    catch (e: any) { message.error(e.message); }
  };

  const doDelete = async (modelName: string) => {
    try {
      await api.del(`/api/admin/ml/center/${encodeURIComponent(modelName)}`);
      message.success(`${modelName} unregistered`);
      load();
    } catch (e: any) { message.error(e.message); }
  };

  const openCreate = async () => {
    setCreateOpen(true); setCreateTemplate('');
    try { setTemplateList(await api.get('/api/admin/ml/configs')); } catch { }
  };
  const doCreate = async () => {
    if (!createTemplate) return;
    try {
      await api.post(`/api/admin/ml/configs/${createTemplate}/register`);
      message.success(`Registered ${createTemplate}`);
      setCreateOpen(false); load();
    } catch (e: any) { message.error(e.message); }
  };

  const doStage = async (modelName: string, version: string, stage: string) => {
    try { await api.post(`/api/admin/models/${modelName}/stage?version=${encodeURIComponent(version)}&stage=${stage}`); message.success(`${modelName} v${version} → ${stage}`); (async () => { setItems(await api.get('/api/admin/ml/center')); })(); }
    catch (e: any) { message.error(e.message); }
  };

  const columns = [
    { title: '模型名', dataIndex: 'model_name', width: 160 },
    { title: '数据集', dataIndex: 'dataset_name', width: 140 },
    { title: '配置', dataIndex: 'config_name', width: 160, ellipsis: true },
    { title: '版本', width: 70, render: (_: any, r: CenterItem) => (r.versions || []).length },
    { title: '最新', width: 120, render: (_: any, r: CenterItem) => {
      const prod = (r.versions || []).find(v => v.stage === 'Production');
      return prod ? <Tag color="green">v{prod.version} Prod</Tag> : <Text type="secondary">—</Text>;
    }},
    { title: '操作', width: 240, render: (_: any, r: CenterItem) => (<Space>
      <Popconfirm title="快速训练（无调优）？" onConfirm={() => doTrain(r.config_name, true)} disabled={r.config_name === '—'}>
        <Button size="small" icon={<ThunderboltOutlined />} disabled={r.config_name === '—'}>快速</Button>
      </Popconfirm>
      <Popconfirm title="Optuna 调优训练？" onConfirm={() => doTrain(r.config_name, false)} disabled={r.config_name === '—'}>
        <Button size="small" type="primary" icon={<ExperimentOutlined />} disabled={r.config_name === '—'}>调优</Button>
      </Popconfirm>
      <Popconfirm title="取消注册？配置模板会保留" onConfirm={() => doDelete(r.model_name)}>
        <Button size="small" danger icon={<DeleteOutlined />}>取消注册</Button>
      </Popconfirm>
    </Space>) },
  ];

  const renderVersions = (record: CenterItem) => {
    const modelName = record.model_name;
    return (
      <Table dataSource={record.versions || []} rowKey="version" size="small" pagination={false}
        columns={[
          { title: 'Ver', dataIndex: 'version', width: 60 },
          { title: 'Stage', dataIndex: 'stage', width: 100, render: (v: string) => <Tag color={stageColor[v] || 'default'}>{v}</Tag> },
          { title: 'RMSE', dataIndex: 'rmse', width: 90, render: (v: number) => v != null ? Number(v).toFixed(4) : '—' },
          { title: 'IC', dataIndex: 'ic', width: 90, render: (v: number) => v != null ? Number(v).toFixed(4) : '—' },
          { title: 'ICIR', dataIndex: 'icir', width: 80, render: (v: number) => v != null ? Number(v).toFixed(3) : '—' },
          { title: 'Feat', dataIndex: 'n_features', width: 60 },
          { title: 'Dataset', dataIndex: 'dataset', width: 120, render: (v: string) => v || '—' },
          { title: '操作', width: 260, render: (_: any, r: VersionDetail) => (<Space size={0}>
            {r.stage !== 'Production' && <Popconfirm title="Promote?" onConfirm={() => doStage(modelName, r.version, 'Production')}><Button size="small" type="link" style={{ color: '#52c41a' }}>Prod</Button></Popconfirm>}
            {r.stage !== 'Staging' && <Popconfirm title="Stage?" onConfirm={() => doStage(modelName, r.version, 'Staging')}><Button size="small" type="link">Staging</Button></Popconfirm>}
            {r.stage !== 'Archived' && <Popconfirm title="Archive?" onConfirm={() => doStage(modelName, r.version, 'Archived')}><Button size="small" type="link" danger>Archive</Button></Popconfirm>}
          </Space>) },
        ]}
      />
    );
  };

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>从模板创建</Button>
      </Space>
      <Table dataSource={items} rowKey="model_name" loading={loading} size="small"
      columns={columns}
      expandable={{
        expandedRowKeys: expandedKeys,
        onExpandedRowsChange: (keys) => setExpandedKeys(keys as string[]),
        expandedRowRender: renderVersions,
      }}
      />
      <Modal title="从模板创建模型" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={doCreate}
        okText="注册" okButtonProps={{ disabled: !createTemplate }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text strong>选择配置模板:</Text>
          <Select value={createTemplate} onChange={setCreateTemplate} style={{ width: '100%' }}
            options={templateList.map((c: any) => ({ value: c.name, label: `${c.name} (${c.registry_model_name || '—'})` }))} />
          {templateList.length === 0 && <Text type="secondary">暂无配置，请先在 ML 配置中新建</Text>}
        </Space>
      </Modal>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// StrategiesTab — browse/edit strategy files
// ═══════════════════════════════════════════════════════════════════════════════

const StrategiesTab: React.FC = () => {
  const [strategies, setStrategies] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorName, setEditorName] = useState('');
  const [editorSource, setEditorSource] = useState('');
  const [viewOpen, setViewOpen] = useState(false);
  const [viewName, setViewName] = useState('');
  const [viewSource, setViewSource] = useState('');

  const load = async () => { setLoading(true); try { setStrategies(await api.get('/api/admin/strategies')); } catch { } finally { setLoading(false); } };
  useEffect(() => { load(); }, []);

  const openView = async (name: string) => {
    try { const d = await api.get(`/api/admin/strategies/${name}`); setViewName(name); setViewSource(d.source || ''); setViewOpen(true); } catch { }
  };
  const openEdit = async (name: string) => {
    try { const d = await api.get(`/api/admin/strategies/${name}`); setEditorName(name); setEditorSource(d.source || ''); setEditorOpen(true); } catch { }
  };
  const saveEdit = async () => {
    try { await api.put(`/api/admin/strategies/${editorName}`, { source: editorSource }); message.success('Saved'); setEditorOpen(false); load(); } catch (e: any) { message.error(e.message); }
  };
  const doDelete = async (name: string) => {
    try { await api.del(`/api/admin/strategies/${name}`); message.success(`Deleted ${name}`); load(); } catch (e: any) { message.error(e.message); }
  };

  return (
    <>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditorName(''); setEditorSource(''); setEditorOpen(true); }} style={{ marginBottom: 16 }}>新建策略</Button>
      <Table dataSource={strategies} rowKey="name" loading={loading} size="small"
        columns={[
          { title: '文件', dataIndex: 'name', width: 220 },
          { title: '路径', dataIndex: 'path', ellipsis: true },
          { title: '操作', width: 200, render: (_, r) => (<Space>
            <Button size="small" icon={<EyeOutlined />} onClick={() => openView(r.name)}>查看</Button>
            <Button size="small" icon={<SettingOutlined />} onClick={() => openEdit(r.name)}>编辑</Button>
            {r.name !== '__init__.py' && <Popconfirm title={`删除 ${r.name}？`} onConfirm={() => doDelete(r.name)} okButtonProps={{ danger: true }}>
              <Button size="small" danger icon={<DeleteOutlined />} /></Popconfirm>}
          </Space>) },
        ]} />
      <Drawer title={`查看: ${viewName}`} open={viewOpen} onClose={() => setViewOpen(false)} width={700}>
        <pre style={{ fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap', background: '#fafafa', padding: 16, borderRadius: 6 }}>{viewSource}</pre>
      </Drawer>
      <Drawer title={editorName ? `编辑: ${editorName}` : '新建策略'} open={editorOpen} onClose={() => setEditorOpen(false)} width={700}
        extra={<Button type="primary" onClick={saveEdit}>保存</Button>}>
        {!editorName && <Input placeholder="策略文件名 (e.g. my_strat.py)" onChange={e => setEditorName(e.target.value + '.py')} style={{ marginBottom: 12 }} />}
        <Input.TextArea value={editorSource} onChange={e => setEditorSource(e.target.value)} rows={30} style={{ fontFamily: 'monospace', fontSize: 12 }} />
      </Drawer>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// MlflowTab — embedded MLflow Web UI
// ═══════════════════════════════════════════════════════════════════════════════

const MlflowTab: React.FC = () => {
  return (
    <div style={{ textAlign: 'center', padding: 60 }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>📊</div>
      <Text style={{ fontSize: 16, display: 'block', marginBottom: 24 }}>
        MLflow 运行在 VM :5000 端口，无法通过 cloudflared 隧道嵌入。
      </Text>
      <Space direction="vertical">
        <Button type="primary" size="large" onClick={() => window.open('http://127.0.0.1:5000', '_blank')}>
          从 VM 本机打开 MLflow
        </Button>
        <Text type="secondary" style={{ maxWidth: 400 }}>
          如需远程访问，可通过 SSH 端口转发: <br/>
          <Text code copyable>ssh -L 5000:127.0.0.1:5000 quant-vm</Text>
        </Text>
      </Space>
    </div>
  );
};

export default ModelsPage;
