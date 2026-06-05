import {
  ThunderboltOutlined,
  PlusOutlined,
  EditOutlined,
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
} from 'antd';
import { useRef, useState } from 'react';
import { api } from '../api';

interface CronJob {
  index?: number;
  raw?: string;
  enabled: boolean;
  schedule: string;
  command: string;
  comment?: string;
  name: string;
  description: string;
}

const CronJobs: React.FC = () => {
  const actionRef = useRef<ActionType | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [form] = Form.useForm();

  // ── Run ────────────────────────────────────────────────────────────────────

  const handleRun = async (command: string) => {
    try {
      const data = await api.post(
        `/api/admin/cron/run?command=${encodeURIComponent(command)}`
      );
      message.success(`Task queued — #${data.task_id}`);
    } catch (err: any) {
      message.error(`Failed: ${err.message}`);
    }
  };

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

  const openCreate = () => {
    setEditingIndex(null);
    form.resetFields();
    form.setFieldsValue({ enabled: true });
    setModalOpen(true);
  };

  const openEdit = (index: number, job: CronJob) => {
    setEditingIndex(index);
    form.setFieldsValue({
      name: job.name || '',
      description: job.description || '',
      schedule: job.schedule || '',
      command: job.command || '',
      enabled: job.enabled,
    });
    setModalOpen(true);
  };

  // ── Submit modal ───────────────────────────────────────────────────────────

  const handleModalOk = async () => {
    try {
      const values = await form.validateFields();
      if (editingIndex !== null) {
        await api.put(`/api/admin/cron/${editingIndex}`, values);
        message.success('Job updated');
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
      width: 160,
      render: (_, r) => (
        <Space>
          {r.command && (
            <Button
              type="primary"
              size="small"
              icon={<ThunderboltOutlined />}
              onClick={() => handleRun(r.command)}
            >
              执行
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
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
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
    </>
  );
};

export default CronJobs;
