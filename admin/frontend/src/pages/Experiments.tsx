import { useState, useRef } from 'react';
import {
  PlayCircleOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
  PlusOutlined,
  EyeOutlined,
  DeleteOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import type { ColumnsType } from 'antd/es/table';
import {
  Tag,
  Button,
  Space,
  message,
  Tooltip,
  Modal,
  Form,
  Select,
  Input,
  InputNumber,
  Drawer,
  Descriptions,
  Table,
  Alert,
  Divider,
  Popconfirm,
  Typography,
} from 'antd';

const { Text } = Typography;
import { api } from '../api';

interface ExperimentItem {
  exp_id: string;
  name: string;
  type: string;
  market: string;
  strategy: string;
  version: number;
  status: string;
  current_run: string | null;
  config_path: string;
  pid: number | null;
}

interface RunRecord {
  run_id: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
}

const statusColor: Record<string, string> = {
  running: 'green',
  paused: 'blue',
  completed: 'default',
  failed: 'red',
  pending: 'orange',
  archived: 'default',
};

const pollTask = (taskId: number): Promise<{ status: string; result: string | null }> => {
  return new Promise((resolve, reject) => {
    const check = () => {
      api
        .get(`/api/admin/tasks/${taskId}`)
        .then((data) => {
          if (data.status === 'completed') {
            resolve(data);
          } else if (data.status === 'failed') {
            reject(new Error(data.result || 'Task failed'));
          } else {
            setTimeout(check, 2000);
          }
        })
        .catch(reject);
    };
    check();
  });
};

const Experiments: React.FC = () => {
  const actionRef = useRef<ActionType | undefined>(undefined);

  // ── A1: Registration Modal ─────────────────────────────────
  const [regOpen, setRegOpen] = useState(false);
  const [regForm] = Form.useForm();
  const [previewId, setPreviewId] = useState('');
  const [regLoading, setRegLoading] = useState(false);

  const buildPreviewId = (values: Record<string, any>) => {
    const t = values.type || 'live';
    const m = values.market || 'us';
    const s = values.strategy || 'ml';
    const v = values.version || 1;
    return `${t}_${m}_${s}_v${v}`;
  };

  const handleRegValuesChange = (_changed: any, allValues: Record<string, any>) => {
    setPreviewId(buildPreviewId(allValues));
  };

  const handleRegister = async () => {
    try {
      const values = await regForm.validateFields();
      setRegLoading(true);
      const data = await api.post('/api/admin/experiments/register', values);
      message.success(`Experiment ${data.exp_id} registered (status: ${data.status})`);
      setRegOpen(false);
      regForm.resetFields();
      setPreviewId('');
      actionRef.current?.reload();
    } catch (err: any) {
      // Form validation error or API error
      if (err?.response?.data?.detail) {
        message.error(err.response.data.detail);
      } else if (err?.message) {
        message.error(err.message);
      }
    } finally {
      setRegLoading(false);
    }
  };

  // ── A2: Detail Drawer ──────────────────────────────────────
  const [drawerExp, setDrawerExp] = useState<ExperimentItem | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [equityLatest, setEquityLatest] = useState<Record<string, any> | null>(null);
  const [equityLoading, setEquityLoading] = useState(false);
  const [clearLoading, setClearLoading] = useState(false);
  const [configDrawer, setConfigDrawer] = useState<{open: boolean; expId: string; content: string; loading: boolean}>({
    open: false, expId: '', content: '', loading: false,
  });
  const [selectedExpId, setSelectedExpId] = useState<string | null>(null);

  const openDrawer = async (exp: ExperimentItem) => {
    setDrawerExp(exp);
    setSelectedExpId(exp.exp_id);
    setDrawerOpen(true);

    // Load runs
    setRunsLoading(true);
    setRuns([]);
    try {
      const runData = await api.get(`/api/admin/experiments/${exp.exp_id}/runs`);
      setRuns(runData);
    } catch {
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }

    // Load equity snapshot
    setEquityLoading(true);
    setEquityLatest(null);
    try {
      const eqData = await api.get(`/api/admin/dashboard/equity/${exp.exp_id}`);
      if (Array.isArray(eqData) && eqData.length > 0) {
        setEquityLatest(eqData[eqData.length - 1]);
      } else if (eqData && typeof eqData === 'object') {
        setEquityLatest(eqData);
      }
    } catch {
      setEquityLatest(null);
    } finally {
      setEquityLoading(false);
    }
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    setDrawerExp(null);
    setRuns([]);
    setEquityLatest(null);
  };

  // ── Actions ────────────────────────────────────────────────
  const handleAction = async (expId: string, action: string) => {
    try {
      const data = await api.post(`/api/admin/experiments/${expId}/${action}`);
      const hide = message.loading(`Task #${data.task_id}: ${action}ing ${expId}...`, 0);
      try {
        await pollTask(data.task_id);
        hide();
        message.success(`${action} ${expId} completed`);
        actionRef.current?.reload();
      } catch (err: any) {
        hide();
        message.error(`${action} ${expId}: ${err.message}`);
        actionRef.current?.reload();
      }
    } catch (err: any) {
      message.error(`${action} ${expId} failed: ${err.message}`);
    }
  };

  const openConfig = async (expId: string) => {
    setConfigDrawer({ open: true, expId, content: '', loading: true });
    try {
      const data = await api.get(`/api/admin/experiments/${expId}/config`);
      setConfigDrawer(c => ({ ...c, content: data.content || '', loading: false }));
    } catch (err: any) {
      message.error(`Failed to load config: ${err.message}`);
      setConfigDrawer({ open: false, expId: '', content: '', loading: false });
    }
  };

  const saveConfig = async () => {
    try {
      await api.put(`/api/admin/experiments/${configDrawer.expId}/config`, { content: configDrawer.content });
      message.success('Config saved');
      setConfigDrawer({ open: false, expId: '', content: '', loading: false });
    } catch (err: any) {
      message.error(`Save failed: ${err.message}`);
    }
  };

  // ── Columns ────────────────────────────────────────────────
  const columns: ProColumns<ExperimentItem>[] = [
    {
      title: 'exp_id',
      dataIndex: 'exp_id',
      width: 200,
      key: 'exp_id',
    },
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: 'Type',
      dataIndex: 'type',
      width: 80,
      key: 'type',
      render: (_, r) => <Tag>{r.type}</Tag>,
    },
    {
      title: 'Market',
      dataIndex: 'market',
      width: 60,
      key: 'market',
    },
    {
      title: 'Ver',
      dataIndex: 'version',
      width: 60,
      key: 'version',
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      key: 'status',
      render: (_, r) => (
        <Tag color={statusColor[r.status] || 'default'}>{r.status}</Tag>
      ),
    },
    {
      title: 'PID',
      dataIndex: 'pid',
      width: 75,
      key: 'pid',
      render: (_, r) => <span>{r.pid ?? '-'}</span>,
    },
    {
      title: 'Current Run',
      dataIndex: 'current_run',
      width: 200,
      key: 'current_run',
      ellipsis: true,
      render: (_, r) => (
        <Tooltip title={r.current_run}>
          <span>{r.current_run || '-'}</span>
        </Tooltip>
      ),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 260,
      render: (_, r) => (
        <Space>
          <Tooltip title="Config">
            <Button size="small" icon={<SettingOutlined />} onClick={() => openConfig(r.exp_id)} />
          </Tooltip>
          <Tooltip title="Detail">
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => openDrawer(r)}
            />
          </Tooltip>
          {r.status !== 'running' && (
            <Popconfirm title={`启动 ${r.exp_id}？`} onConfirm={() => handleAction(r.exp_id, 'start')}>
              <Tooltip title="Start">
                <Button type="primary" size="small" icon={<PlayCircleOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
          {r.status === 'running' && (
            <Popconfirm title={`停止 ${r.exp_id}？`} onConfirm={() => handleAction(r.exp_id, 'stop')}>
              <Tooltip title="Stop">
                <Button size="small" icon={<PauseCircleOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
          <Popconfirm title={`重启 ${r.exp_id}？`} onConfirm={() => handleAction(r.exp_id, 'restart')}>
            <Tooltip title="Restart">
              <Button size="small" icon={<ReloadOutlined />} />
            </Tooltip>
          </Popconfirm>
          <Popconfirm
            title={`永久删除 ${r.exp_id}？`}
            description="将删除所有 BQ 数据、状态、日志和输出"
            onConfirm={() => handleAction(r.exp_id, 'delete')}
            okText="确认删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Tooltip title="Delete">
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── Run columns for drawer ─────────────────────────────────
  const runColumns: ColumnsType<RunRecord> = [
    { title: 'Run ID', dataIndex: 'run_id', key: 'run_id', width: 220 },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (_, r) => (
        <Tag color={statusColor[r.status] || 'default'}>{r.status}</Tag>
      ),
    },
    { title: 'Started', dataIndex: 'started_at', key: 'started_at', width: 200 },
    {
      title: 'Ended',
      dataIndex: 'ended_at',
      key: 'ended_at',
      width: 200,
      render: (_, r) => r.ended_at || '-',
    },
  ];

  return (
    <>
      <ProTable<ExperimentItem>
        headerTitle="Experiments"
        actionRef={actionRef}
        rowKey="exp_id"
        search={false}
        columns={columns}
        request={async () => {
          const data = await api.get('/api/admin/experiments');
          return { data, success: true, total: data.length };
        }}
        pagination={{ pageSize: 20 }}
        toolBarRender={() => [
          <Button
            key="register"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setRegOpen(true)}
          >
            注册实验
          </Button>,
        ]}
      />

      {/* ── A1: Registration Modal ─────────────────────────── */}
      <Modal
        title="注册实验"
        open={regOpen}
        onOk={handleRegister}
        onCancel={() => {
          setRegOpen(false);
          regForm.resetFields();
          setPreviewId('');
        }}
        confirmLoading={regLoading}
        destroyOnClose
        width={520}
      >
        <Form
          form={regForm}
          layout="vertical"
          onValuesChange={handleRegValuesChange}
          initialValues={{ type: 'live', market: 'us', version: 1 }}
        >
          <Form.Item name="type" label="Type" rules={[{ required: true }]}>
            <Select>
              <Select.Option value="live">live</Select.Option>
              <Select.Option value="paper">paper</Select.Option>
              <Select.Option value="prod">prod</Select.Option>
              <Select.Option value="debug">debug</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="market" label="Market" rules={[{ required: true }]}>
            <Select>
              <Select.Option value="us">us</Select.Option>
              <Select.Option value="hk">hk</Select.Option>
              <Select.Option value="crypto">crypto</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="strategy" label="Strategy" rules={[{ required: true }]}>
            <Input placeholder="e.g. ml, mom" />
          </Form.Item>
          <Form.Item name="version" label="Version" rules={[{ required: true }]}>
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="config_path" label="Config Path">
            <Input placeholder="e.g. live/configs/exp1_ml_us.yaml" />
          </Form.Item>
          <Form.Item name="name" label="Name (optional)">
            <Input placeholder="Human-readable name" />
          </Form.Item>

          <Alert
            type="info"
            showIcon
            message={
              <span>
                Preview ID:{' '}
                <code>{previewId || buildPreviewId(regForm.getFieldsValue())}</code>
              </span>
            }
            style={{ marginTop: 8 }}
          />
        </Form>
      </Modal>

      {/* ── A2: Detail Drawer ──────────────────────────────── */}
      <Drawer
        title={drawerExp ? `Experiment: ${drawerExp.exp_id}` : 'Experiment Detail'}
        open={drawerOpen}
        onClose={closeDrawer}
        width={800}
        destroyOnClose
      >
        {drawerExp && (
          <>
            <Descriptions
              title="Basic Info"
              bordered
              size="small"
              column={2}
              style={{ marginBottom: 24 }}
            >
              <Descriptions.Item label="ID">{drawerExp.exp_id}</Descriptions.Item>
              <Descriptions.Item label="Name">
                {drawerExp.name || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="Type">
                <Tag>{drawerExp.type}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Market">{drawerExp.market}</Descriptions.Item>
              <Descriptions.Item label="Strategy">{drawerExp.strategy}</Descriptions.Item>
              <Descriptions.Item label="Version">{drawerExp.version}</Descriptions.Item>
              <Descriptions.Item label="Status">
                <Tag color={statusColor[drawerExp.status] || 'default'}>
                  {drawerExp.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="PID">
                {drawerExp.pid ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="Config Path" span={2}>
                <code>{drawerExp.config_path || '-'}</code>
              </Descriptions.Item>
            </Descriptions>

            <Descriptions
              title="Run History"
              bordered={false}
              style={{ marginBottom: 24 }}
            />
            <Table
              dataSource={runs}
              rowKey="run_id"
              columns={runColumns}
              loading={runsLoading}
              pagination={false}
              size="small"
              style={{ marginBottom: 24 }}
              locale={{ emptyText: 'No runs recorded' }}
            />

            {equityLoading && (
              <Alert type="info" message="Loading equity data..." showIcon />
            )}
            {!equityLoading && equityLatest && (
              <Descriptions
                title="Latest Equity Snapshot"
                bordered
                size="small"
                column={2}
              >
                {Object.entries(equityLatest).map(([key, value]) => (
                  <Descriptions.Item key={key} label={key}>
                    {typeof value === 'number' ? value.toLocaleString() : String(value ?? '-')}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            )}
            {!equityLoading && !equityLatest && (
              <Alert
                type="warning"
                message="Equity data not available"
                description="Dashboard equity API returned no data for this experiment."
                showIcon
              />
            )}

            <Divider />
            <Popconfirm
              title="确定清空此实验的所有数据？"
              description="包括 BQ equity/trades、state 文件、run 历史。此操作不可恢复。"
              onConfirm={async () => {
                setClearLoading(true);
                try {
                  await api.post(`/api/admin/experiments/${selectedExpId}/clear`);
                  message.success('已清空');
                  actionRef.current?.reload();
                  closeDrawer();
                } catch (err: any) {
                  message.error(err?.response?.data?.detail || '清空失败');
                } finally {
                  setClearLoading(false);
                }
              }}
              okText="确认清空"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Button danger type="primary" loading={clearLoading}>
                清空所有数据
              </Button>
            </Popconfirm>
          </>
        )}
      </Drawer>

      {/* ── Config Editor Drawer ──────────────────────── */}
      <Drawer
        title={`Config: ${configDrawer.expId}`}
        open={configDrawer.open}
        onClose={() => setConfigDrawer({ open: false, expId: '', content: '', loading: false })}
        width={700}
        extra={
          <Popconfirm title="确认保存？旧配置将备份为 .bak" onConfirm={saveConfig} okText="保存">
            <Button type="primary" disabled={configDrawer.loading}>Save</Button>
          </Popconfirm>
        }
      >
        {configDrawer.loading ? (
          <Text type="secondary">Loading...</Text>
        ) : (
          <Input.TextArea
            value={configDrawer.content}
            onChange={(e) => setConfigDrawer(c => ({ ...c, content: e.target.value }))}
            rows={30}
            style={{ fontFamily: 'monospace', fontSize: 12 }}
          />
        )}
      </Drawer>
    </>
  );
};

export default Experiments;
