import { useState, useRef } from 'react';
import {
  PlayCircleOutlined, PauseCircleOutlined, ReloadOutlined,
  PlusOutlined, EyeOutlined, DeleteOutlined, SettingOutlined,
  FileAddOutlined,
} from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import type { ColumnsType } from 'antd/es/table';
import {
  Tag, Button, Space, message, Tooltip, Modal,
  Select, Input, InputNumber, Drawer, Descriptions, Table,
  Alert, Divider, Popconfirm, Typography, Tabs,
} from 'antd';
import { api } from '../api';

const { Text } = Typography;

// ── Types ────────────────────────────────────────────────────────────────────

interface ExperimentItem {
  exp_id: string; name: string; type: string; market: string;
  strategy: string; version: number; status: string;
  current_run: string | null; config_path: string; pid: number | null;
}

interface ConfigItem { name: string; path: string; size: number; }

interface RunRecord { run_id: string; status: string; started_at: string; ended_at: string; }

const statusColor: Record<string, string> = {
  running: 'green', paused: 'orange', stopped: 'red',
  pending: 'default', registered: 'default', archived: 'default',
};

// ── Poll helper ──────────────────────────────────────────────────────────────

const pollTask = (taskId: number): Promise<{ status: string; result: string | null }> =>
  new Promise((resolve, reject) => {
    const check = () => {
      api.get(`/api/admin/tasks/${taskId}`).then((d: any) => {
        if (d.status === 'completed') resolve(d);
        else if (d.status === 'failed') reject(new Error(d.result || 'Task failed'));
        else setTimeout(check, 2000);
      }).catch(reject);
    };
    check();
  });

// =============================================================================
// ExperimentDashboard — parent with two sub-tabs
// =============================================================================

const ExperimentDashboard: React.FC = () => {
  const [tab, setTab] = useState('lab');
  return (
    <Tabs activeKey={tab} onChange={setTab} items={[
      { key: 'configs', label: '实验配置', children: <ConfigsTab /> },
      { key: 'lab', label: '实验室', children: <LabTab /> },
    ]} />
  );
};

// =============================================================================
// ConfigsTab — manage YAML config templates
// =============================================================================

const ConfigsTab: React.FC = () => {
  const actionRef = useRef<ActionType>(undefined);
  const [createOpen, setCreateOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorName, setEditorName] = useState('');
  const [editorContent, setEditorContent] = useState('');

  const openEditor = async (name: string) => {
    try {
      const data = await api.get(`/api/admin/experiments/configs/${name}`);
      setEditorName(name);
      setEditorContent(data.content || '');
      setEditorOpen(true);
    } catch { message.error('Failed to load config'); }
  };

  const saveEditor = async () => {
    try {
      await api.put(`/api/admin/experiments/configs/${editorName}`, { content: editorContent });
      message.success('Config saved');
      setEditorOpen(false);
    } catch (e: any) { message.error(`Save failed: ${e.message}`); }
  };

  const deleteConfig = async (name: string) => {
    try {
      await api.del(`/api/admin/experiments/configs/${name}`);
      message.success(`Deleted ${name}`);
      actionRef.current?.reload();
    } catch (e: any) { message.error(`Delete failed: ${e.message}`); }
  };

  const columns: ProColumns<ConfigItem>[] = [
    { title: 'File', dataIndex: 'name', key: 'name', width: 240 },
    {
      title: 'Size', dataIndex: 'size', key: 'size', width: 100,
      render: (_, r) => `${(r.size / 1024).toFixed(1)} KB`,
    },
    {
      title: 'Actions', key: 'actions', width: 120,
      render: (_, r) => (
        <Space>
          <Tooltip title="Edit"><Button size="small" icon={<SettingOutlined />} onClick={() => openEditor(r.name)} /></Tooltip>
          <Popconfirm title={`删除 ${r.name}？`} onConfirm={() => deleteConfig(r.name)} okButtonProps={{ danger: true }}>
            <Tooltip title="Delete"><Button size="small" danger icon={<DeleteOutlined />} /></Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <ProTable<ConfigItem>
        headerTitle="实验配置模板"
        actionRef={actionRef}
        rowKey="name"
        search={false}
        columns={columns}
        request={async () => {
          const data = await api.get('/api/admin/experiments/configs');
          return { data, success: true, total: (data || []).length };
        }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建配置</Button>,
        ]}
        pagination={false}
      />

      {/* Create Config Modal */}
      <Modal title="新建配置模板" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={async () => {
          const el = document.getElementById('new-config-content') as HTMLTextAreaElement;
          const nm = (document.getElementById('new-config-name') as HTMLInputElement)?.value || '';
          if (!nm || !el) return;
          try {
            await api.put(`/api/admin/experiments/configs/${nm}.yaml`, { content: el.value });
            message.success('Config created');
            setCreateOpen(false);
            actionRef.current?.reload();
          } catch (e: any) { message.error(`Create failed: ${e.message}`); }
        }}
        width={700}
      >
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input id="new-config-name" placeholder="config name (e.g. my_strategy_us)" addonAfter=".yaml" />
          <Input.TextArea id="new-config-content" rows={20} placeholder="YAML content..." style={{ fontFamily: 'monospace', fontSize: 12 }} />
        </Space>
      </Modal>

      {/* Edit Config Drawer */}
      <Drawer title={`编辑: ${editorName}`} open={editorOpen} onClose={() => setEditorOpen(false)} width={700}
        extra={<Popconfirm title="保存修改？" onConfirm={saveEditor}><Button type="primary">Save</Button></Popconfirm>}
      >
        <Input.TextArea value={editorContent} onChange={(e) => setEditorContent(e.target.value)} rows={30}
          style={{ fontFamily: 'monospace', fontSize: 12 }} />
      </Drawer>
    </>
  );
};

// =============================================================================
// LabTab — experiment instances management
// =============================================================================

const LabTab: React.FC = () => {
  const actionRef = useRef<ActionType>(undefined);
  const [detailDrawer, setDetailDrawer] = useState(false);
  const [detailExp, setDetailExp] = useState<ExperimentItem | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [equityLatest, setEquityLatest] = useState<Record<string, any> | null>(null);
  const [equityLoading, setEquityLoading] = useState(false);
  const [configDrawer, setConfigDrawer] = useState<{ open: boolean; expId: string; content: string; loading: boolean }>({ open: false, expId: '', content: '', loading: false });

  // Create from template
  const [createOpen, setCreateOpen] = useState(false);
  const [templates, setTemplates] = useState<ConfigItem[]>([]);
  const [createTemplate, setCreateTemplate] = useState('');
  const [createExpId, setCreateExpId] = useState('');
  const [createType, setCreateType] = useState('live');
  const [createMarket, setCreateMarket] = useState('us');
  const [createStrategy, setCreateStrategy] = useState('ml');
  const [createVersion, setCreateVersion] = useState(1);

  const loadTemplates = async () => {
    const data = await api.get('/api/admin/experiments/configs');
    setTemplates(data || []);
  };

  // ── Actions ────────────────────────────────────────────────────
  const handleAction = async (expId: string, action: string) => {
    try {
      const data = await api.post(`/api/admin/experiments/${expId}/${action}`);
      const hide = message.loading(`Task #${data.task_id}: ${action}ing ${expId}...`, 0);
      try { await pollTask(data.task_id); hide(); message.success(`${action} ${expId} completed`); actionRef.current?.reload(); }
      catch (err: any) { hide(); message.error(`${action} ${expId}: ${err.message}`); actionRef.current?.reload(); }
    } catch (err: any) { message.error(`${action} ${expId} failed: ${err.message}`); }
  };

  const handleDelete = async (expId: string) => {
    try {
      const data = await api.post(`/api/admin/experiments/${expId}/delete`);
      if (data.status === 'ok') {
        message.success(`${expId} deleted`);
        actionRef.current?.reload();
      } else {
        message.error(`Delete failed: ${JSON.stringify(data)}`);
      }
    } catch (err: any) { message.error(`Delete ${expId} failed: ${err.message}`); }
  };

  const openDetail = async (exp: ExperimentItem) => {
    setDetailExp(exp); setDetailDrawer(true);
    setRunsLoading(true); setEquityLoading(true);
    setRuns([]); setEquityLatest(null);
    try { const d = await api.get(`/api/admin/experiments/${exp.exp_id}/runs`); setRuns(d); } catch { setRuns([]); } finally { setRunsLoading(false); }
    try {
      const eq = await api.get(`/api/admin/dashboard/equity/${exp.exp_id}`);
      setEquityLatest(Array.isArray(eq) && eq.length > 0 ? eq[eq.length - 1] : eq);
    } catch { setEquityLatest(null); } finally { setEquityLoading(false); }
  };

  const openConfig = async (expId: string) => {
    setConfigDrawer({ open: true, expId, content: '', loading: true });
    try {
      const data = await api.get(`/api/admin/experiments/${expId}/config`);
      setConfigDrawer(c => ({ ...c, content: data.content || '', loading: false }));
    } catch (err: any) { message.error(`Failed to load config: ${err.message}`); setConfigDrawer({ open: false, expId: '', content: '', loading: false }); }
  };

  const saveConfig = async () => {
    try {
      await api.put(`/api/admin/experiments/${configDrawer.expId}/config`, { content: configDrawer.content });
      message.success('Config saved'); setConfigDrawer({ open: false, expId: '', content: '', loading: false });
    } catch (err: any) { message.error(`Save failed: ${err.message}`); }
  };

  const doCreate = async () => {
    if (!createTemplate || !createExpId) return;
    try {
      await api.post('/api/admin/experiments/create-from-config', {
        template: createTemplate, exp_id: createExpId,
        type: createType, market: createMarket, strategy: createStrategy, version: createVersion,
      });
      message.success(`Created ${createExpId}`);
      setCreateOpen(false); actionRef.current?.reload();
    } catch (e: any) { message.error(`Create failed: ${e.message}`); }
  };

  // ── Columns ────────────────────────────────────────────────────
  const columns: ProColumns<ExperimentItem>[] = [
    { title: 'exp_id', dataIndex: 'exp_id', key: 'exp_id', width: 200 },
    { title: 'Name', dataIndex: 'name', key: 'name' },
    {
      title: 'Market', dataIndex: 'market', key: 'market', width: 60,
      render: (_, r) => <Tag>{r.market?.toUpperCase()}</Tag>,
    },
    {
      title: 'Status', dataIndex: 'status', key: 'status', width: 100,
      render: (_, r) => <Tag color={statusColor[r.status] || 'default'}>{r.status}</Tag>,
    },
    {
      title: 'Actions', key: 'actions', width: 280,
      render: (_, r) => (
        <Space>
          <Tooltip title="Config"><Button size="small" icon={<SettingOutlined />} onClick={() => openConfig(r.exp_id)} /></Tooltip>
          <Tooltip title="Detail"><Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)} /></Tooltip>
          {r.status !== 'running' && (
            <Popconfirm title={`启动 ${r.exp_id}？`} onConfirm={() => handleAction(r.exp_id, 'start')}>
              <Tooltip title="Start"><Button type="primary" size="small" icon={<PlayCircleOutlined />} /></Tooltip>
            </Popconfirm>
          )}
          {r.status === 'running' && (
            <Popconfirm title={`停止 ${r.exp_id}？`} onConfirm={() => handleAction(r.exp_id, 'stop')}>
              <Tooltip title="Stop"><Button size="small" icon={<PauseCircleOutlined />} /></Tooltip>
            </Popconfirm>
          )}
          <Popconfirm title={`重启 ${r.exp_id}？`} onConfirm={() => handleAction(r.exp_id, 'restart')}>
            <Tooltip title="Restart"><Button size="small" icon={<ReloadOutlined />} /></Tooltip>
          </Popconfirm>
          <Popconfirm title={`永久删除 ${r.exp_id}？`} description="将删除所有数据" onConfirm={() => handleDelete(r.exp_id)}
            okText="确认删除" okButtonProps={{ danger: true }}>
            <Tooltip title="Delete"><Button size="small" danger icon={<DeleteOutlined />} /></Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const runColumns: ColumnsType<RunRecord> = [
    { title: 'Run ID', dataIndex: 'run_id', key: 'run_id', width: 220 },
    { title: 'Status', dataIndex: 'status', key: 'status', width: 100, render: (_, r) => <Tag color={statusColor[r.status] || 'default'}>{r.status}</Tag> },
    { title: 'Started', dataIndex: 'started_at', key: 'started_at', width: 200 },
    { title: 'Ended', dataIndex: 'ended_at', key: 'ended_at', width: 200, render: (_, r) => r.ended_at || '-' },
  ];

  return (
    <>
      <ProTable<ExperimentItem>
        headerTitle="实验实例"
        actionRef={actionRef}
        rowKey="exp_id"
        search={false}
        columns={columns}
        request={async () => {
          const data = await api.get('/api/admin/experiments');
          return { data, success: true, total: (data || []).length };
        }}
        toolBarRender={() => [
          <Button key="create" type="primary" icon={<FileAddOutlined />}
            onClick={() => { loadTemplates(); setCreateOpen(true); }}>从模板创建</Button>,
        ]}
        pagination={false}
      />

      {/* Create from template Modal */}
      <Modal title="从模板创建实验" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={doCreate}
        okText="创建" okButtonProps={{ disabled: !createTemplate || !createExpId }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space><Text strong>模板:</Text>
            <Select value={createTemplate} onChange={setCreateTemplate} style={{ width: 220 }}
              options={templates.map(t => ({ value: t.name, label: t.name }))} />
          </Space>
          <Space><Text strong>exp_id:</Text>
            <Input value={createExpId} onChange={(e) => setCreateExpId(e.target.value)} placeholder="e.g. live_us_ml_v3" style={{ width: 220 }} />
          </Space>
          <Space>
            <Select value={createType} onChange={setCreateType} style={{ width: 100 }}
              options={['live','paper','debug'].map(v => ({ value: v, label: v }))} />
            <Select value={createMarket} onChange={setCreateMarket} style={{ width: 80 }}
              options={['us','hk'].map(v => ({ value: v, label: v.toUpperCase() }))} />
            <Select value={createStrategy} onChange={setCreateStrategy} style={{ width: 80 }}
              options={['ml','mom'].map(v => ({ value: v, label: v }))} />
            <InputNumber value={createVersion} onChange={(v) => setCreateVersion(v || 1)} min={1} style={{ width: 60 }} />
          </Space>
        </Space>
      </Modal>

      {/* Detail Drawer */}
      <Drawer title={detailExp?.exp_id} open={detailDrawer} onClose={() => setDetailDrawer(false)} width={700}>
        {detailExp && (
          <>
            <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="Name">{detailExp.name}</Descriptions.Item>
              <Descriptions.Item label="Status"><Tag color={statusColor[detailExp.status]}>{detailExp.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="Market">{detailExp.market?.toUpperCase()}</Descriptions.Item>
              <Descriptions.Item label="Strategy">{detailExp.strategy}</Descriptions.Item>
              <Descriptions.Item label="Config">{detailExp.config_path}</Descriptions.Item>
              <Descriptions.Item label="Run">{detailExp.current_run || '—'}</Descriptions.Item>
            </Descriptions>

            <Divider>Equity Snapshot</Divider>
            {equityLoading ? <Alert message="Loading..." type="info" /> :
              equityLatest ? (
                <Descriptions size="small" column={3} bordered style={{ marginBottom: 16 }}>
                  <Descriptions.Item label="Bar">{equityLatest.bar}</Descriptions.Item>
                  <Descriptions.Item label="Equity">${Math.round(equityLatest.equity || 0).toLocaleString()}</Descriptions.Item>
                  <Descriptions.Item label="PnL">${Math.round(equityLatest.daily_pnl || 0)}</Descriptions.Item>
                </Descriptions>
              ) : <Alert message="No equity data" type="warning" />}

            <Divider>Runs</Divider>
            <Table dataSource={runs} rowKey="run_id" loading={runsLoading} size="small" columns={runColumns} pagination={false} />

            <Divider />
            <Popconfirm title={`清除 ${detailExp.exp_id} 所有数据？`} onConfirm={async () => {
              await api.post(`/api/admin/experiments/${detailExp.exp_id}/clear`);
              message.success('Cleared'); setDetailDrawer(false); actionRef.current?.reload();
            }}>
              <Button danger>Clear All Data</Button>
            </Popconfirm>
          </>
        )}
      </Drawer>

      {/* Config Editor Drawer */}
      <Drawer title={`Config: ${configDrawer.expId}`} open={configDrawer.open}
        onClose={() => setConfigDrawer({ open: false, expId: '', content: '', loading: false })} width={700}
        extra={<Popconfirm title="确认保存？" onConfirm={saveConfig}><Button type="primary">Save</Button></Popconfirm>}>
        {configDrawer.loading ? <Text type="secondary">Loading...</Text> :
          <Input.TextArea value={configDrawer.content} onChange={(e) => setConfigDrawer(c => ({ ...c, content: e.target.value }))}
            rows={30} style={{ fontFamily: 'monospace', fontSize: 12 }} />}
      </Drawer>
    </>
  );
};

export default ExperimentDashboard;
