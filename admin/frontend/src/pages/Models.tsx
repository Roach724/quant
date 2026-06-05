import { useState, useEffect } from 'react';
import {
  PlusOutlined, DeleteOutlined, EyeOutlined, SettingOutlined,
  ThunderboltOutlined, ExperimentOutlined, CheckCircleOutlined,
} from '@ant-design/icons';
import {
  Tabs, Table, Button, Space, Modal, Select, Input, DatePicker,
  Tag, Popconfirm, message, Checkbox, Typography, Drawer, Descriptions,
} from 'antd';
import { api } from '../api';

const { Text } = Typography;
const { RangePicker } = DatePicker;
const { TextArea } = Input;

// ── ModelsPage ───────────────────────────────────────────────────────────────

const ModelsPage: React.FC = () => {
  const [tab, setTab] = useState('center');
  return (
    <Tabs activeKey={tab} onChange={setTab} items={[
      { key: 'datasets', label: '数据集', children: <DatasetsTab /> },
      { key: 'configs', label: 'ML 配置', children: <MlConfigsTab /> },
      { key: 'center', label: '模型中心', children: <ModelCenterTab /> },
    ]} />
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// DatasetsTab
// ═══════════════════════════════════════════════════════════════════════════════

interface FactorItem { factor_id: string; source: string; label: string; }
interface DatasetItem {
  id: number; name: string; market: string; label: string;
  factor_ids: string[]; train_range: string; val_range: string; test_range: string;
  bq_table: string | null; status: string; row_count: number;
}

const DatasetsTab: React.FC = () => {
  const [datasets, setDatasets] = useState<DatasetItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  // Create form
  const [cName, setCName] = useState('');
  const [cMarket, setCMarket] = useState('us');
  const [cLabel, setCLabel] = useState('fwd_ret_5d');
  const [cFactors, setCFactors] = useState<FactorItem[]>([]);
  const [cSelectedFactors, setCSelectedFactors] = useState<string[]>([]);
  const [cTrainRange, setCTrainRange] = useState<[string, string] | null>(null);
  const [cValRange, setCValRange] = useState<[string, string] | null>(null);
  const [cTestRange, setCTestRange] = useState<[string, string] | null>(null);

  const loadDatasets = async () => {
    setLoading(true);
    try { setDatasets(await api.get('/api/admin/ml/datasets')); } catch { }
    finally { setLoading(false); }
  };

  useEffect(() => { loadDatasets(); }, []);

  const openCreate = async () => {
    setCreateOpen(true);
    try { setCFactors(await api.get(`/api/admin/ml/datasets/${cMarket}/factors`)); } catch { }
  };

  const doCreate = async () => {
    if (!cName || cSelectedFactors.length === 0) return;
    try {
      await api.post('/api/admin/ml/datasets', {
        name: cName, market: cMarket, label: cLabel,
        factor_ids: cSelectedFactors,
        train_start: cTrainRange?.[0] || '', train_end: cTrainRange?.[1] || '',
        val_start: cValRange?.[0] || '', val_end: cValRange?.[1] || '',
        test_start: cTestRange?.[0] || '', test_end: cTestRange?.[1] || '',
      });
      message.success('Dataset registered');
      setCreateOpen(false); loadDatasets();
    } catch (e: any) { message.error(`Create failed: ${e.message}`); }
  };

  const doGenerate = async (id: number) => {
    try {
      const r = await api.post(`/api/admin/ml/datasets/${id}/generate`);
      message.success(`Generated: ${r.table} (${r.row_count} rows)`);
      loadDatasets();
    } catch (e: any) { message.error(`Generate failed: ${e.message}`); }
  };

  const doDelete = async (id: number) => {
    try { await api.del(`/api/admin/ml/datasets/${id}`); message.success('Deleted'); loadDatasets(); }
    catch (e: any) { message.error(`Delete failed: ${e.message}`); }
  };

  return (
    <>
      <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} style={{ marginBottom: 16 }}>新建数据集</Button>
      <Table dataSource={datasets} rowKey="id" loading={loading} size="small"
        columns={[
          { title: '名称', dataIndex: 'name', width: 160 },
          { title: '市场', dataIndex: 'market', width: 60, render: (v: string) => <Tag>{v.toUpperCase()}</Tag> },
          { title: '因子数', width: 70, render: (_, r) => r.factor_ids.length },
          { title: 'Label', dataIndex: 'label', width: 120 },
          { title: 'BQ表', dataIndex: 'bq_table', width: 200, ellipsis: true,
            render: (v: string | null) => v ? <Text code style={{ fontSize: 11 }}>{v}</Text> : <Text type="secondary">—</Text> },
          { title: '行数', dataIndex: 'row_count', width: 80, render: (v: number) => v?.toLocaleString() || '—' },
          { title: '操作', width: 160, render: (_, r) => (
              <Space>
                <Popconfirm title={r.bq_table ? '覆盖已有的 BQ 表？' : '生成 BQ 表？'} onConfirm={() => doGenerate(r.id)}>
                  <Button size="small" type="primary" icon={<ThunderboltOutlined />}>生成</Button>
                </Popconfirm>
                <Popconfirm title="删除数据集及 BQ 表？" onConfirm={() => doDelete(r.id)} okButtonProps={{ danger: true }}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </Space>
            ),
          },
        ]} />

      <Modal title="新建数据集" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={doCreate}
        okText="创建" width={800} okButtonProps={{ disabled: !cName || cSelectedFactors.length === 0 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Text strong>名称:</Text><Input value={cName} onChange={e => setCName(e.target.value)} style={{ width: 200 }} />
            <Text strong>市场:</Text><Select value={cMarket} onChange={(v) => { setCMarket(v); setCSelectedFactors([]); }} style={{ width: 80 }}
              options={['us', 'hk'].map(m => ({ value: m, label: m.toUpperCase() }))} />
            <Text strong>Label:</Text><Select value={cLabel} onChange={setCLabel} style={{ width: 140 }}
              options={['fwd_ret_5d', 'fwd_ret_20d'].map(l => ({ value: l, label: l }))} />
          </Space>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text strong>因子:</Text>
            <Checkbox.Group style={{ maxHeight: 300, overflow: 'auto', display: 'flex', flexDirection: 'column' }}
              value={cSelectedFactors} onChange={(v) => setCSelectedFactors(v as string[])}
              options={cFactors.map(f => ({ label: `${f.factor_id} (${f.source})`, value: f.factor_id }))} />
          </Space>
          <Space>
            <Text strong>训练集:</Text><RangePicker onChange={(d) => d && d[0] && d[1] ? setCTrainRange([d[0].format('YYYY-MM-DD'), d[1].format('YYYY-MM-DD')]) : setCTrainRange(null)} />
          </Space>
          <Space>
            <Text strong>验证集:</Text><RangePicker onChange={(d) => d && d[0] && d[1] ? setCValRange([d[0].format('YYYY-MM-DD'), d[1].format('YYYY-MM-DD')]) : setCValRange(null)} />
          </Space>
          <Space>
            <Text strong>测试集:</Text><RangePicker onChange={(d) => d && d[0] && d[1] ? setCTestRange([d[0].format('YYYY-MM-DD'), d[1].format('YYYY-MM-DD')]) : setCTestRange(null)} />
          </Space>
        </Space>
      </Modal>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// MlConfigsTab
// ═══════════════════════════════════════════════════════════════════════════════

interface ConfigItem {
  id: number; name: string; description: string;
  config_path: string; dataset_name: string;
  registry_model_name: string | null; status: string;
}

const MlConfigsTab: React.FC = () => {
  const [configs, setConfigs] = useState<ConfigItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorName, setEditorName] = useState('');
  const [editorContent, setEditorContent] = useState('');
  const [editorDesc, setEditorDesc] = useState('');

  const loadConfigs = async () => {
    setLoading(true);
    try { setConfigs(await api.get('/api/admin/ml/configs')); } catch { }
    finally { setLoading(false); }
  };
  useEffect(() => { loadConfigs(); }, []);

  const openEditor = async (name: string) => {
    try {
      const d = await api.get(`/api/admin/ml/configs/${name}`);
      setEditorName(name); setEditorContent(d.content || ''); setEditorOpen(true);
    } catch { message.error('Failed to load config'); }
  };

  const saveEditor = async () => {
    try {
      await api.put(`/api/admin/ml/configs/${editorName}`, { content: editorContent, description: editorDesc });
      message.success('Saved'); setEditorOpen(false); loadConfigs();
    } catch (e: any) { message.error(`Save failed: ${e.message}`); }
  };

  const doRegister = async (name: string) => {
    try { await api.post(`/api/admin/ml/configs/${name}/register`); message.success('Registered'); loadConfigs(); }
    catch (e: any) { message.error(`Register failed: ${e.message}`); }
  };

  const doDelete = async (name: string) => {
    try { await api.del(`/api/admin/ml/configs/${name}`); message.success('Deleted'); loadConfigs(); }
    catch (e: any) { message.error(e.message); }
  };

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setEditorName(''); setEditorContent(''); setEditorDesc(''); setEditorOpen(true); }}>新建配置</Button>
      </Space>
      <Table dataSource={configs} rowKey="id" loading={loading} size="small"
        columns={[
          { title: '配置名', dataIndex: 'name', width: 180 },
          { title: '数据集', dataIndex: 'dataset_name', width: 140 },
          { title: '模型名', dataIndex: 'registry_model_name', width: 140 },
          { title: '状态', dataIndex: 'status', width: 100, render: (v: string) => v === 'registered' ? <Tag color="green">已注册</Tag> : <Tag>草稿</Tag> },
          { title: '操作', width: 200, render: (_, r) => (
              <Space>
                <Button size="small" icon={<SettingOutlined />} onClick={() => openEditor(r.name)}>编辑</Button>
                {r.status !== 'registered' && (
                  <Popconfirm title="注册到模型中心？" onConfirm={() => doRegister(r.name)}>
                    <Button size="small" type="primary" icon={<CheckCircleOutlined />}>注册</Button>
                  </Popconfirm>
                )}
                <Popconfirm title="删除配置？如有已注册模型将先检查" onConfirm={() => doDelete(r.name)} okButtonProps={{ danger: true }}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </Space>
            ),
          },
        ]} />

      <Drawer title={editorName ? `编辑: ${editorName}` : '新建配置'} open={editorOpen}
        onClose={() => setEditorOpen(false)} width={700}
        extra={<Button type="primary" onClick={saveEditor}>保存</Button>}>
        <Space direction="vertical" style={{ width: '100%' }}>
          {!editorName && <Input placeholder="配置名 (e.g. lgb_us_v1.yaml)" value={editorDesc} onChange={e => { setEditorDesc(e.target.value); setEditorName(e.target.value + '.yaml'); }} />}
          <TextArea value={editorContent} onChange={e => setEditorContent(e.target.value)}
            rows={30} style={{ fontFamily: 'monospace', fontSize: 12 }}
            placeholder={`data:\n  dataset: us_tech_v1\n  label: fwd_ret_5d\n\nmodel:\n  type: lightgbm\n  params:\n    ...\n\nregistry:\n  model_name: us_tech`} />
        </Space>
      </Drawer>
    </>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// ModelCenterTab
// ═══════════════════════════════════════════════════════════════════════════════

interface CenterItem {
  model_name: string; dataset_name: string; config_name: string;
  versions: any[];
}

const ModelCenterTab: React.FC = () => {
  const [items, setItems] = useState<CenterItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailItem, setDetailItem] = useState<CenterItem | null>(null);

  const load = async () => {
    setLoading(true);
    try { setItems(await api.get('/api/admin/ml/center')); } catch { }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const doTrain = async (configName: string, skipTuning: boolean) => {
    try {
      const r = await api.post('/api/admin/ml/train', { config_name: configName, skip_tuning: skipTuning });
      message.success(`Training task #${r.task_id} submitted`);
    } catch (e: any) { message.error(`Train failed: ${e.message}`); }
  };

  const openDetail = (item: CenterItem) => { setDetailItem(item); setDetailOpen(true); };

  return (
    <>
      <Table dataSource={items} rowKey="model_name" loading={loading} size="small"
        columns={[
          { title: '模型名', dataIndex: 'model_name', width: 160 },
          { title: '数据集', dataIndex: 'dataset_name', width: 140 },
          { title: '版本', key: 'versions', width: 200,
            render: (_, r) => (r.versions || []).length === 0
              ? <Text type="secondary">暂无</Text>
              : <Space wrap>{(r.versions || []).map((v: any) => (
                  <Tag key={v.version} color={v.current_stage === 'Production' ? 'green' : 'default'}>
                    v{v.version} {v.current_stage}
                  </Tag>
                ))}</Space> },
          { title: '操作', width: 240, render: (_, r) => (
              <Space>
                <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)}>详情</Button>
                <Popconfirm title="确认训练？（不含调优）" onConfirm={() => doTrain(r.config_name, true)}>
                  <Button size="small" icon={<ThunderboltOutlined />}>快速训练</Button>
                </Popconfirm>
                <Popconfirm title="确认训练 + Optuna 调优？" onConfirm={() => doTrain(r.config_name, false)}>
                  <Button size="small" type="primary" icon={<ExperimentOutlined />}>调优训练</Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]} />

      <Drawer title={detailItem?.model_name} open={detailOpen} onClose={() => setDetailOpen(false)} width={600}>
        {detailItem && (
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="模型名">{detailItem.model_name}</Descriptions.Item>
            <Descriptions.Item label="数据集">{detailItem.dataset_name || '—'}</Descriptions.Item>
            <Descriptions.Item label="配置">{detailItem.config_name}</Descriptions.Item>
            <Descriptions.Item label="版本数">{(detailItem.versions || []).length}</Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </>
  );
};

export default ModelsPage;
