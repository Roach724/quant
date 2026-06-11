import {
  ThunderboltOutlined,
  PlusOutlined,
  EditOutlined,
  HistoryOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import {
  Button,
  Space,
  message,
  Switch,
  Modal,
  Form,
  Input,
  Drawer,
  Table,
  Tag,
  Popconfirm,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useRef, useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, toLocal } from '../api';

interface CronJob {
  index?: number;
  raw?: string;
  enabled: boolean;
  schedule: string;
  command: string;
  comment?: string;
  name: string;
  description: string;
  latest_log?: string | null;
  last_run?: string | null;
}

interface HistoryEntry {
  id: number;
  job_name: string;
  status: string;
  trigger_type: string;
  exit_code: number | null;
  started_at: string | null;
  finished_at: string | null;
  log_file: string | null;
  error_tail: string | null;
}

const CronJobs: React.FC = () => {
  const actionRef = useRef<ActionType | undefined>(undefined);
  const navigate = useNavigate();
  const [modalOpen, setModalOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [form] = Form.useForm();

  // History drawer state
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyData, setHistoryData] = useState<HistoryEntry[]>([]);
  const [historyTitle, setHistoryTitle] = useState('');
  const [runningTasks, setRunningTasks] = useState<Set<number>>(new Set());
  const STORAGE_KEY = 'cron_running_tasks';

  // Restore running state on mount (survives page refresh)
  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      if (stored.length === 0) return;
      Promise.all(stored.map((item: any) => api.get(`/api/admin/tasks/${item.task_id}`)))
        .then((results) => {
          const stillRunning = new Set<number>();
          results.forEach((t: any, i: number) => {
            if (t.status === 'pending' || t.status === 'running') {
              stillRunning.add(stored[i].job_index);
            }
          });
          if (stillRunning.size > 0) {
            setRunningTasks(stillRunning);
            stillRunning.forEach((jobIndex) => {
              const item = stored.find((s: any) => s.job_index === jobIndex);
              if (item) pollBg(item.task_id, jobIndex);
            });
          }
        }).catch(() => {});
    } catch {}
  }, []);

  const pollBg = async (taskId: number, jobIndex: number) => {
    for (let i = 0; i < 360; i++) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const t = await api.get(`/api/admin/tasks/${taskId}`);
        if (t.status === 'completed') { message.success('Done'); actionRef.current?.reload(); break; }
        if (t.status === 'failed') { message.error(`Failed: ${(t.result || '').slice(-200)}`); break; }
      } catch { break; }
    }
    setRunningTasks(prev => { const next = new Set(prev); next.delete(jobIndex); return next; });
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
      localStorage.setItem(STORAGE_KEY, JSON.stringify(stored.filter((s: any) => s.job_index !== jobIndex)));
    } catch {}
  };

  // ── Run ────────────────────────────────────────────────────────────────────

  const handleRun = async (command: string, name: string, index: number) => {
    try {
      setRunningTasks(prev => new Set(prev).add(index));
      const data = await api.post(
        `/api/admin/cron/run?command=${encodeURIComponent(command)}&name=${encodeURIComponent(name || 'cron')}`
      );
      try {
        const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
        stored.push({ task_id: data.task_id, job_name: name, job_index: index });
        localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
      } catch {}
      pollBg(data.task_id, index);
    } catch (err: any) {
      message.error(`Failed: ${err.message}`);
      setRunningTasks(prev => { const next = new Set(prev); next.delete(index); return next; });
    }
  };

  const handleViewLog = (latestLog: string | null | undefined) => {
    if (latestLog) {
      navigate(`/logs?module=cron&file=${encodeURIComponent(latestLog)}`);
    } else {
      navigate('/logs?module=cron');
    }
  };

  // ── History ────────────────────────────────────────────────────────────────

  const handleHistory = async (index: number, label: string, _command: string) => {
    setHistoryTitle(`Execution History — ${label}`);
    setHistoryOpen(true);
    setHistoryLoading(true);
    setHistoryData([]);
    try {
      const name = label.includes('_') || label.includes('-') ? label : '';
      const data: HistoryEntry[] = await api.get(`/api/admin/cron/${index}/history?name=${encodeURIComponent(name)}`);
      setHistoryData(data || []);
    } catch (err: any) {
      message.error(`Failed to load history: ${err.message}`);
    } finally {
      setHistoryLoading(false);
    }
  };

  const historyColumns: ColumnsType<HistoryEntry> = [
    {
      title: 'ID',
      dataIndex: 'id',
      width: 50,
      key: 'id',
    },
    {
      title: '任务名',
      dataIndex: 'job_name',
      width: 150,
      key: 'job_name',
      ellipsis: true,
    },
    {
      title: '触发',
      dataIndex: 'trigger_type',
      width: 70,
      key: 'trigger_type',
      render: (v: string) => v === 'manual' ? '🏷️ 手动' : '⏰ 调度',
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      width: 160,
      key: 'started_at',
      render: (v: string | null) => toLocal(v),
    },
    {
      title: '退出码',
      dataIndex: 'exit_code',
      width: 65,
      key: 'exit_code',
      render: (v: number | null) => v != null ? <Tag color={v === 0 ? 'green' : 'red'}>{v}</Tag> : '-',
    },
    {
      title: '日志文件',
      dataIndex: 'log_file',
      width: 220,
      key: 'log_file',
      render: (v: string | null) => (
        <div style={{ wordBreak: 'break-all', whiteSpace: 'normal', fontSize: 12 }}>
          {v ? v.split('/').pop() : '-'}
        </div>
      ),
    },
  ];

  // ── Toggle enabled ─────────────────────────────────────────────────────────

  const handleToggle = async (index: number, enabled: boolean) => {
    try {
      await api.put(`/api/admin/cron/${index}`, { enabled });
      message.success(enabled ? 'Job enabled' : 'Job disabled');
      actionRef.current?.reload();
    } catch (err: any) {
      message.error(`Failed: ${err.message}`);
    }
  };

  // ── Open create / edit modal ───────────────────────────────────────────────

  const [editingJob, setEditingJob] = useState<CronJob | null>(null);

  // Reset form when editing job changes (applies initialValues)
  useEffect(() => {
    if (modalOpen) form.resetFields();
  }, [editingJob, modalOpen]);

  const openCreate = () => {
    setEditingIndex(null);
    setEditingJob(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (index: number, job: CronJob) => {
    setEditingIndex(index);
    setEditingJob(job);
    setModalOpen(true);
  };

  // ── Submit modal ───────────────────────────────────────────────────────────

  const handleModalOk = async () => {
    try {
      const values = await form.validateFields();
      if (editingIndex !== null) {
        await api.put(`/api/admin/cron/${editingIndex}`, values);
        message.success('Job updated');
        // Force full page reload to guarantee fresh data
        setTimeout(() => window.location.reload(), 500);
      } else {
        await api.post('/api/admin/cron/add', values);
        message.success('Job created');
      }
      setModalOpen(false);
      form.resetFields();
      actionRef.current?.reload();
    } catch (err: any) {
      if (err?.errorFields) return; // validation error, ignore
      message.error(`Failed: ${err.message}`);
    }
  };

  // ── Columns ────────────────────────────────────────────────────────────────

  const columns: ProColumns<CronJob>[] = [
    {
      title: 'Name',
      dataIndex: 'name',
      width: 200,
      key: 'name',
      ellipsis: true,
      render: (_, r) => (
        <span style={{ fontWeight: 600, fontSize: 13 }}>
          {r.name || r.command || r.comment || '—'}
        </span>
      ),
    },
    {
      title: 'Description',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (_, r) =>
        r.description ? (
          <span style={{ color: '#666', fontSize: 13 }}>{r.description}</span>
        ) : (
          <span style={{ color: '#bbb' }}>—</span>
        ),
    },
    {
      title: 'Schedule',
      dataIndex: 'schedule',
      width: 140,
      key: 'schedule',
      ellipsis: true,
      render: (_, r) => (
        <span style={{ fontFamily: 'monospace', fontSize: 13 }}>
          {r.schedule || '—'}
        </span>
      ),
    },
    {
      title: 'Command',
      dataIndex: 'command',
      key: 'command',
      width: 280,
      ellipsis: true,
      render: (_, r) => {
        const text = r.command || '—';
        return (
          <span
            title={text}
            style={{ fontFamily: 'monospace', fontSize: 12, color: '#555' }}
          >
            {text}
          </span>
        );
      },
    },
    {
      title: '最近运行',
      dataIndex: 'last_run',
      key: 'last_run',
      width: 160,
      render: (_: unknown, r: CronJob) => <span>{toLocal(r.last_run)}</span>,
    },
    {
      title: 'Enabled',
      dataIndex: 'enabled',
      width: 80,
      key: 'enabled',
      render: (_, r) => (
        <Switch
          size="small"
          checked={r.enabled}
          onChange={(checked) => handleToggle(r.index!, checked)}
        />
      ),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 320,
      render: (_, r) => (
        <Space>
          {r.command && r.index !== undefined && (
            <Popconfirm
              title={`执行 ${r.name || r.command?.slice(0, 30)}？`}
              onConfirm={() => handleRun(r.command, r.name || '', r.index!)}
              okText="确认执行"
            >
              <Button
                type="primary"
                size="small"
                icon={<ThunderboltOutlined />}
                loading={runningTasks.has(r.index!)}
                disabled={runningTasks.has(r.index!)}
              >
                执行
              </Button>
            </Popconfirm>
          )}
          <Button
            size="small"
            icon={<FileTextOutlined />}
            onClick={() => handleViewLog(r.latest_log)}
          >
            日志
          </Button>
          {r.index !== undefined && (
            <Button
              size="small"
              icon={<HistoryOutlined />}
              onClick={() =>
                handleHistory(r.index!, r.name || r.command?.slice(0, 40) || `#${r.index}`, r.command || '')
              }
            >
              历史
            </Button>
          )}
          {r.index !== undefined && r.name && (
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={() => openEdit(r.index!, r)}
            >
              编辑
            </Button>
          )}
        </Space>
      ),
    },
  ];

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <>
      <ProTable<CronJob>
        headerTitle="Cron Jobs"
        actionRef={actionRef}
        rowKey="index"
        search={false}
        columns={columns}
        toolBarRender={() => [
          <Button
            key="create"
            type="primary"
            icon={<PlusOutlined />}
            onClick={openCreate}
          >
            新建任务
          </Button>,
        ]}
        request={async () => {
          const allJobs: CronJob[] = await api.get('/api/admin/cron');
          // Assign stable row indices from registry order
          const indexed = allJobs.map((j, i) => ({
            ...j,
            index: j.index ?? i,
          }));
          return { data: indexed, success: true, total: indexed.length };
        }}
        pagination={{ pageSize: 20 }}
      />

      <Modal
        title={editingIndex !== null ? '编辑任务' : '新建任务'}
        open={modalOpen}
        onOk={handleModalOk}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        destroyOnClose
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}
          initialValues={editingJob || { enabled: true }}
        >
          <Form.Item
            name="name"
            label="Name"
            rules={[{ required: true, message: '请输入任务名称' }]}
          >
            <Input placeholder="e.g. collect-us-rating-summary" />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input placeholder="任务描述" />
          </Form.Item>
          <Form.Item
            name="schedule"
            label="Schedule"
            rules={[{ required: true, message: '请输入 cron 表达式' }]}
            extra="分 时 日 月 周（e.g. 0 6 * * 1-5）"
          >
            <Input placeholder="0 6 * * 1-5" />
          </Form.Item>
          <Form.Item
            name="command"
            label="Command"
            rules={[{ required: true, message: '请输入执行命令' }]}
          >
            <Input placeholder="python scripts/compute_factors_batch.py --incremental" />
          </Form.Item>
          <Form.Item name="enabled" label="Enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={historyTitle}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        width={700}
      >
        <Table
          dataSource={historyData}
          loading={historyLoading}
          rowKey="id"
          columns={historyColumns}
          pagination={{ pageSize: 20, size: 'small' }}
          size="small"
          locale={{ emptyText: '暂无执行记录' }}
        />
      </Drawer>
    </>
  );
};

export default CronJobs;
