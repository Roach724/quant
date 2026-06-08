import { useState, useRef } from 'react';
import {
  PlayCircleOutlined, PauseCircleOutlined,
  PlusOutlined, EyeOutlined, DeleteOutlined, SettingOutlined,
  FileAddOutlined, FileTextOutlined, ClearOutlined, LinkOutlined,
} from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import type { ColumnsType } from 'antd/es/table';
import {
  Tag, Button, Space, message, Tooltip, Modal,
  Select, Input, Drawer, Descriptions, Table,
  Alert, Divider, Popconfirm, Typography, Tabs,
  Spin, Empty,
} from 'antd';
import { useNavigate } from 'react-router-dom';
import ReactECharts from 'echarts-for-react';
import { api } from '../api';

const { Text } = Typography;

const stripYaml = (name: string) => name.replace(/\.yaml$/, '');

// ── Types ────────────────────────────────────────────────────────────────────

interface ExperimentItem {
  exp_id: string; name: string; type: string; market: string;
  strategy: string; version: number; status: string;
  has_active_run: boolean; total_runs: number; active_run_id: string | null;
  current_run: string | null; config_path: string; pid: number | null;
  created_at?: string; latest_run_at?: string;
}

interface ConfigItem { name: string; path: string; size: number; created_at?: string; updated_at?: string; }

interface RunRecord { run_id: string; status: string; started_at: string; ended_at: string; base_run: string | null; }

const statusColor: Record<string, string> = {
  running: 'green', stopped: 'orange', completed: 'blue', failed: 'red',
  idle: 'default', pending: 'default', archived: 'default',
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
      { key: 'lab', label: '实验室', children: <LabTabs /> },
      { key: 'configs', label: '实验配置', children: <ConfigsTabs /> },
    ]} />
  );
};

// =============================================================================
// ConfigsTabs — Configs with Live/Paper sub-tabs
// =============================================================================

const ConfigsTabs: React.FC = () => {
  const [sub, setSub] = useState('live');
  return (
    <Tabs activeKey={sub} onChange={setSub} items={[
      { key: 'live', label: 'Live', children: <ConfigsTab filterPrefix="live_" /> },
      { key: 'paper', label: 'Paper', children: <ConfigsTab filterPrefix="paper_" /> },
    ]} />
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// ConfigsTab — manage YAML config templates
// =============================================================================

const ConfigsTab: React.FC<{ filterPrefix?: string }> = ({ filterPrefix }) => {
  const actionRef = useRef<ActionType>(undefined);
  const [createOpen, setCreateOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorName, setEditorName] = useState('');
  const [editorContent, setEditorContent] = useState('');
  const [viewOpen, setViewOpen] = useState(false);
  const [viewName, setViewName] = useState('');
  const [viewContent, setViewContent] = useState('');
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameOld, setRenameOld] = useState('');
  const [renameNew, setRenameNew] = useState('');

  const openViewer = async (name: string) => {
    try {
      const data = await api.get(`/api/admin/experiments/configs/${name}`);
      setViewName(name); setViewContent(data.content || ''); setViewOpen(true);
    } catch { message.error('Failed to load config'); }
  };

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

  const doRename = async () => {
    if (!renameNew) return;
    try {
      await api.post(`/api/admin/experiments/configs/${renameOld}/rename`, { new_name: renameNew });
      message.success(`Renamed to ${stripYaml(renameNew)}`);
      setRenameOpen(false);
      actionRef.current?.reload();
    } catch (e: any) { message.error(`Rename failed: ${e.message}`); }
  };

  const columns: ProColumns<ConfigItem>[] = [
    { title: 'File', dataIndex: 'name', key: 'name', width: 240, render: (_, r) => stripYaml(r.name) },
    {
      title: 'Size', dataIndex: 'size', key: 'size', width: 100,
      render: (_, r) => `${(r.size / 1024).toFixed(1)} KB`,
    },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: (_, r) => r.created_at?.slice(0, 10) || '-' },
    { title: '更新时间', dataIndex: 'updated_at', width: 160, render: (_, r) => r.updated_at?.slice(0, 10) || '-' },
    {
      title: 'Actions', key: 'actions', width: 200,
      render: (_, r) => (
        <Space>
          <Tooltip title="Rename"><Button size="small" onClick={() => { setRenameOld(r.name); setRenameNew(stripYaml(r.name)); setRenameOpen(true); }}>重命名</Button></Tooltip>
          <Tooltip title="View"><Button size="small" icon={<EyeOutlined />} onClick={() => openViewer(r.name)} /></Tooltip>
          <Tooltip title="Edit"><Button size="small" icon={<SettingOutlined />} onClick={() => openEditor(r.name)} /></Tooltip>
          <Popconfirm title={`删除 ${stripYaml(r.name)}？`} onConfirm={() => deleteConfig(r.name)} okButtonProps={{ danger: true }}>
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
          const filtered = filterPrefix ? (data || []).filter((c: any) => c.name.startsWith(filterPrefix)) : (data || []);
          return { data: filtered, success: true, total: filtered.length };
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
      <Drawer title={`编辑: ${stripYaml(editorName)}`} open={editorOpen} onClose={() => setEditorOpen(false)} width={700}
        extra={<Popconfirm title="保存修改？" onConfirm={saveEditor}><Button type="primary">Save</Button></Popconfirm>}
      >
        <Input.TextArea value={editorContent} onChange={(e) => setEditorContent(e.target.value)} rows={30}
          style={{ fontFamily: 'monospace', fontSize: 12 }} />
      </Drawer>

      {/* Rename Modal */}
      <Modal title={`重命名: ${stripYaml(renameOld)}`} open={renameOpen} onCancel={() => setRenameOpen(false)}
        onOk={doRename} okText="确认">
        <Input value={renameNew} onChange={e => setRenameNew(e.target.value)}
          addonAfter=".yaml" placeholder="新名称" style={{ marginTop: 8 }} />
      </Modal>

      {/* View Config Drawer */}
      <Drawer title={`查看: ${stripYaml(viewName)}`} open={viewOpen} onClose={() => setViewOpen(false)} width={700}>
        <pre style={{ fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: '#fafafa', padding: 16, borderRadius: 6, margin: 0 }}>
          {viewContent}
        </pre>
      </Drawer>
    </>
  );
};

// =============================================================================
// ═══════════════════════════════════════════════════════════════════════════════
// LabTabs — Lab with Live/Paper sub-tabs
// ═══════════════════════════════════════════════════════════════════════════════

const LabTabs: React.FC = () => {
  const [sub, setSub] = useState('live');
  return (
    <Tabs activeKey={sub} onChange={setSub} items={[
      { key: 'live', label: 'Live', children: <LabTab filterType="live" /> },
      { key: 'paper', label: 'Paper', children: <LabTab filterType="paper" /> },
    ]} />
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// LabTab — experiment instances management
// ═══════════════════════════════════════════════════════════════════════════════
// =============================================================================

const LabTab: React.FC<{ filterType?: string }> = ({ filterType }) => {
  const actionRef = useRef<ActionType>(undefined);
  const navigate = useNavigate();
  const [detailDrawer, setDetailDrawer] = useState(false);
  const [detailExp, setDetailExp] = useState<ExperimentItem | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [expandedRunKeys, setExpandedRunKeys] = useState<Record<string, { equity: any[]; positions: any[]; loading: boolean; error?: boolean }>>({});
  const [configDrawer, setConfigDrawer] = useState<{ open: boolean; expId: string; content: string; loading: boolean }>({ open: false, expId: '', content: '', loading: false });

  // Create from template
  const [createOpen, setCreateOpen] = useState(false);
  const [templates, setTemplates] = useState<ConfigItem[]>([]);
  const [createTemplate, setCreateTemplate] = useState('');
  const [createExpId, setCreateExpId] = useState('');

  const loadTemplates = async () => {
    const data = await api.get('/api/admin/experiments/configs');
    // Filter by type: paper lab only shows paper_* templates
    const filtered = filterType
      ? (data || []).filter((c: any) => c.name.startsWith(filterType + '_'))
      : (data || []);
    setTemplates(filtered);
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
    setRunsLoading(true);
    setRuns([]); setExpandedRunKeys({});
    try { const d = await api.get(`/api/admin/experiments/${exp.exp_id}/runs`); setRuns(d); } catch { setRuns([]); } finally { setRunsLoading(false); }
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
    if (!createTemplate) { message.warning('请选择配置模板'); return; }
    if (!createExpId) { message.warning('请输入 exp_id'); return; }
    const parts = createExpId.split('_');
    if (parts.length < 2) { message.error('exp_id 格式错误，例如: live_us_ml'); return; }
    try {
      await api.post('/api/admin/experiments/create-from-config', {
        template: createTemplate, exp_id: createExpId,
        type: parts[0] || 'live', market: parts[1] || 'us',
        strategy: parts[2] || 'ml', version: parseInt(parts[3]?.replace('v','') || '1'),
      });
      message.success(`Created ${createExpId}`);
      setCreateOpen(false); setCreateTemplate(''); setCreateExpId('');
      actionRef.current?.reload();
    } catch (e: any) { message.error(`创建失败: ${e.message || JSON.stringify(e)}`); }
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
      title: 'Status', dataIndex: 'has_active_run', key: 'status', width: 100,
      render: (_, r) => r.has_active_run
        ? <Tag color="green">🔵 活跃 Run</Tag>
        : <Tag>⚪ 无活跃 Run</Tag>,
    },
    { title: '创建时间', dataIndex: 'created_at', width: 100, render: (_, r) => r.created_at?.slice(0, 10) || '-' },
    { title: '最新Run', dataIndex: 'latest_run_at', width: 160, render: (_, r) => r.latest_run_at?.slice(0, 16) || '-' },
    {
      title: '累计 Run', dataIndex: 'total_runs', key: 'total_runs', width: 50,
    },
    {
      title: 'Actions', key: 'actions', width: 260,
      render: (_, r) => (
        <Space>
          <Tooltip title="Config"><Button size="small" icon={<SettingOutlined />} onClick={() => openConfig(r.exp_id)} /></Tooltip>
          <Tooltip title="Detail"><Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)} /></Tooltip>
          <Tooltip title={r.has_active_run ? `已有活跃 Run (${r.active_run_id})，请先停止` : '启动新 Run'}>
            <Popconfirm title={`为 ${r.exp_id} 启动新 Run？`} onConfirm={() => handleAction(r.exp_id, 'start')}
              disabled={r.has_active_run}>
              <Button type="primary" size="small" icon={<PlayCircleOutlined />} disabled={r.has_active_run}>启动新 Run</Button>
            </Popconfirm>
          </Tooltip>
          <Tooltip title={r.has_active_run ? `存在活跃 Run，无法删除` : '永久删除'}>
            <Popconfirm title={`永久删除 ${r.exp_id}？`} description="将删除所有数据" onConfirm={() => handleDelete(r.exp_id)}
              okText="确认删除" okButtonProps={{ danger: true }} disabled={r.has_active_run}>
              <Button size="small" danger icon={<DeleteOutlined />} disabled={r.has_active_run} />
            </Popconfirm>
          </Tooltip>
        </Space>
      ),
    },
  ];

  const handleViewRunLog = (runId: string) => {
    if (!detailExp) return;
    const logModule = detailExp.type === 'paper' ? 'paper_run' : 'live';
    const fileName = `${detailExp.exp_id}_${runId}.log`;
    navigate(`/logs?module=${logModule}&file=${encodeURIComponent(fileName)}`);
  };

  const handleStopRun = async (expId: string, runId: string) => {
    try {
      await api.post(`/api/admin/experiments/${expId}/runs/${runId}/stop`);
      message.success(`Run ${runId} stopped`);
      // Reload runs
      setRunsLoading(true);
      try { const d = await api.get(`/api/admin/experiments/${expId}/runs`); setRuns(d); } catch { setRuns([]); } finally { setRunsLoading(false); }
      actionRef.current?.reload();
    } catch (err: any) { message.error(`Stop run failed: ${err.message}`); }
  };

  const handleStartRun = async (expId: string, runId: string) => {
    try {
      const data = await api.post(`/api/admin/experiments/${expId}/runs/${runId}/start`);
      const hide = message.loading(`Starting run ${runId}...`, 0);
      try { await pollTask(data.task_id); hide(); message.success('Run started'); actionRef.current?.reload(); }
      catch (err: any) { hide(); message.error(`Start failed: ${err.message}`); }
    } catch (err: any) { message.error(`Start failed: ${err.message}`); }
  };

  const handleDeleteRun = async (expId: string, runId: string) => {
    try {
      await api.del(`/api/admin/experiments/${expId}/runs/${runId}`);
      message.success(`Run ${runId} deleted`);
      setRunsLoading(true);
      try { const d = await api.get(`/api/admin/experiments/${expId}/runs`); setRuns(d); } catch { setRuns([]); } finally { setRunsLoading(false); }
      actionRef.current?.reload();
    } catch (err: any) { message.error(`Delete failed: ${err.message}`); }
  };

  const handleClearRunState = async (expId: string, runId: string) => {
    try {
      await api.post(`/api/admin/experiments/${expId}/runs/${runId}/clear-state`);
      message.success(`State cleared for run ${runId}`);
    } catch (err: any) { message.error(`Clear state failed: ${err.message}`); }
  };

  const loadRunDetails = async (expId: string, runId: string) => {
    // Skip if already loaded
    if (expandedRunKeys[runId] && !expandedRunKeys[runId].loading) return;
    setExpandedRunKeys(prev => ({ ...prev, [runId]: { equity: [], positions: [], loading: true } }));
    try {
      const [equity, positions] = await Promise.all([
        api.get(`/api/admin/experiments/${expId}/runs/${runId}/equity`),
        api.get(`/api/admin/dashboard/experiments/${expId}/positions?run_id=${runId}`),
      ]);
      setExpandedRunKeys(prev => ({
        ...prev,
        [runId]: { equity: equity || [], positions: positions || [], loading: false },
      }));
    } catch {
      setExpandedRunKeys(prev => ({
        ...prev,
        [runId]: { equity: [], positions: [], loading: false, error: true },
      }));
    }
  };

  const runColumns: ColumnsType<RunRecord> = [
    { title: 'Run ID', dataIndex: 'run_id', key: 'run_id', width: 200 },
    { title: 'Status', dataIndex: 'status', key: 'status', width: 90, render: (_, r) => <Tag color={statusColor[r.status] || 'default'}>{r.status}</Tag> },
    { title: 'Started', dataIndex: 'started_at', key: 'started_at', width: 160, render: (_, r) => r.started_at?.slice(0,19) || '-' },
    { title: 'Ended', dataIndex: 'ended_at', key: 'ended_at', width: 160, render: (_, r) => r.ended_at?.slice(0,19) || '-' },
    {
      title: '', key: 'actions', width: 340,
      render: (_, r) => {
        const exp = detailExp;
        const expId = exp?.exp_id || '';
        return (
          <Space size={4}>
            {/* Start — blocked if any run is active */}
            <Tooltip title={exp?.has_active_run ? '已有活跃 Run' : '启动此 Run'}>
              <Button size="small" icon={<PlayCircleOutlined />}
                disabled={exp?.has_active_run || r.status === 'running'}
                onClick={() => r.status !== 'running' && handleStartRun(expId, r.run_id)} />
            </Tooltip>
            {/* Stop — only for running */}
            {r.status === 'running' && (
              <Popconfirm title={`停止 Run ${r.run_id}？`}
                onConfirm={() => handleStopRun(expId, r.run_id)}>
                <Button size="small" danger icon={<PauseCircleOutlined />} />
              </Popconfirm>
            )}
            {/* Log */}
            <Tooltip title="查看日志">
              <Button size="small" icon={<FileTextOutlined />}
                onClick={() => handleViewRunLog(r.run_id)} />
            </Tooltip>
            {/* Detail — jump to Dashboard */}
            <Tooltip title="实验详情">
              <Button size="small" icon={<LinkOutlined />}
                onClick={() => navigate(`/board?tab=${exp?.type === 'paper' ? 'paper' : 'live'}&exp_id=${expId}&run_id=${r.run_id}`)} />
            </Tooltip>
            {/* Clear state — blocked if running */}
            <Popconfirm title={`清除 Run ${r.run_id} 状态？`} description="将删除 checkpoint/state，保留 BQ 数据"
              onConfirm={() => handleClearRunState(expId, r.run_id)}
              disabled={r.status === 'running'} okButtonProps={{ danger: true }}>
              <Tooltip title={r.status === 'running' ? '活跃 Run 无法清除' : '清除状态'}>
                <Button size="small" icon={<ClearOutlined />} disabled={r.status === 'running'} />
              </Tooltip>
            </Popconfirm>
            {/* Delete — blocked if running */}
            <Popconfirm title={`永久删除 Run ${r.run_id}？`} description="将删除所有关联数据"
              onConfirm={() => handleDeleteRun(expId, r.run_id)}
              disabled={r.status === 'running'} okButtonProps={{ danger: true }}>
              <Tooltip title={r.status === 'running' ? '活跃 Run 无法删除' : '删除'}>
                <Button size="small" danger icon={<DeleteOutlined />} disabled={r.status === 'running'} />
              </Tooltip>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  // Position columns for expanded row
  const posColumns: ColumnsType<any> = [
    { title: 'Symbol', dataIndex: 'symbol', key: 'symbol', width: 80 },
    { title: 'Qty', dataIndex: 'qty', key: 'qty', width: 80, render: (v) => Number(v).toFixed(2) },
    { title: 'Avg Cost', dataIndex: 'avg_cost', key: 'avg_cost', width: 100, render: (v) => `$${Number(v).toFixed(2)}` },
    { title: 'Price', dataIndex: 'current_price', key: 'current_price', width: 100, render: (v) => `$${Number(v).toFixed(2)}` },
    { title: 'PnL', dataIndex: 'pnl', key: 'pnl', width: 100, render: (v) => `$${Number(v).toFixed(2)}` },
    { title: 'PnL%', dataIndex: 'pnl_pct', key: 'pnl_pct', width: 80, render: (v) => `${Number(v).toFixed(2)}%` },
  ];

  function buildEquityChart(data: any[]) {
    const bars = data.map((d: any) => d.bar?.toString() ?? '');
    const values = data.map((d: any) => Number(d.equity ?? 0));
    return {
      tooltip: { trigger: 'axis' },
      grid: { left: 70, right: 20, top: 10, bottom: 30 },
      xAxis: { type: 'category', data: bars, axisLabel: { show: false } },
      yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: (v: number) => `$${(v / 1000).toFixed(0)}k` } },
      series: [{
        type: 'line', data: values, smooth: true, showSymbol: false,
        lineStyle: { color: '#1677ff', width: 1.5 },
        areaStyle: { color: 'rgba(22, 119, 255, 0.08)' },
      }],
      dataZoom: [{ type: 'inside', start: 0, end: 100 }],
    };
  }

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
          const filtered = filterType ? (data || []).filter((e: any) => e.type === filterType) : (data || []);
          return { data: filtered, success: true, total: filtered.length };
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
              options={templates.map(t => ({ value: t.name, label: stripYaml(t.name) }))} />
          </Space>
          <Space><Text strong>exp_id:</Text>
            <Input value={createExpId} onChange={(e) => setCreateExpId(e.target.value)} placeholder="e.g. live_us_ml_v3" style={{ width: 220 }} />
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

            <Divider>Runs</Divider>
            <Table<RunRecord>
              dataSource={runs}
              rowKey="run_id"
              loading={runsLoading}
              size="small"
              columns={runColumns}
              pagination={false}
              expandable={{
                expandedRowRender: (record) => {
                  const details = expandedRunKeys[record.run_id];
                  if (!details || details.loading) {
                    if (!details) loadRunDetails(detailExp!.exp_id, record.run_id);
                    return <Spin />;
                  }
                  if (details.error) return <Alert message="Failed to load run details" type="error" />;
                  const equityEmpty = !details.equity || details.equity.length === 0;
                  const posEmpty = !details.positions || details.positions.length === 0;
                  if (equityEmpty && posEmpty) return <Text type="secondary">No data for this run</Text>;
                  return (
                    <div>
                      <Text strong style={{ marginBottom: 8, display: 'block' }}>权益曲线</Text>
                      {equityEmpty ? <Empty description="No equity data" /> :
                        <ReactECharts option={buildEquityChart(details.equity)} style={{ height: 200 }} />}
                      <div style={{ marginTop: 16 }} />
                      <Text strong style={{ marginBottom: 8, display: 'block' }}>当前持仓</Text>
                      {posEmpty ? <Empty description="No positions" /> :
                        <Table size="small" dataSource={details.positions} columns={posColumns} rowKey="symbol" pagination={false} />}
                    </div>
                  );
                },
                onExpand: (expanded, record) => {
                  if (expanded) loadRunDetails(detailExp!.exp_id, record.run_id);
                },
              }}
            />

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
