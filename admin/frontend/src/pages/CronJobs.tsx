import { ThunderboltOutlined } from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import { Tag, Button, Space, message, Switch } from 'antd';
import { useRef } from 'react';
import { api } from '../api';

interface CronJob {
  index?: number;
  raw?: string;
  enabled: boolean;
  schedule: string;
  command: string;
  comment?: string;
  name?: string;
  description?: string;
}

const CronJobs: React.FC = () => {
  const actionRef = useRef<ActionType | undefined>(undefined);

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

  const handleToggle = async (job: CronJob, checked: boolean) => {
    try {
      const updated = {
        ...job,
        enabled: checked,
        // If enabling from a comment line, set raw to empty so we use schedule+command
        raw: checked ? '' : job.raw,
      };
      // Reload current list, update this job, save
      const allJobs: CronJob[] = await api.get('/api/admin/cron');
      const idx = allJobs.findIndex((j) => j.index === job.index);
      if (idx >= 0) {
        allJobs[idx] = updated;
      } else {
        allJobs.push(updated);
      }
      await api.post('/api/admin/cron', allJobs);
      message.success(checked ? 'Job enabled' : 'Job disabled');
      actionRef.current?.reload();
    } catch (err: any) {
      message.error(`Failed: ${err.message}`);
    }
  };

  const columns: ProColumns<CronJob>[] = [
    {
      title: 'Name',
      dataIndex: 'name',
      width: 180,
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
      width: 130,
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
      width: 260,
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
      width: 90,
      key: 'enabled',
      render: (_, r) =>
        r.enabled ? (
          <Tag color="green">Enabled</Tag>
        ) : (
          <Tag color="red">Disabled</Tag>
        ),
    },
    {
      title: 'Toggle',
      dataIndex: 'enabled',
      width: 80,
      key: 'toggle',
      render: (_, r) =>
        r.schedule && r.command ? (
          <Switch
            size="small"
            checked={r.enabled}
            onChange={(checked) => handleToggle(r, checked)}
          />
        ) : null,
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 120,
      render: (_, r) =>
        r.enabled ? (
          <Space>
            <Button
              type="primary"
              size="small"
              icon={<ThunderboltOutlined />}
              onClick={() => handleRun(r.command)}
            >
              立即执行
            </Button>
          </Space>
        ) : null,
    },
  ];

  return (
    <ProTable<CronJob>
      headerTitle="Cron Jobs"
      actionRef={actionRef}
      rowKey="index"
      search={false}
      columns={columns}
      request={async () => {
        const allJobs: CronJob[] = await api.get('/api/admin/cron');
        // Show enabled jobs, or jobs with names (registry entries), or comment lines
        const filtered = allJobs.filter(
          (j) => j.enabled || j.name || (j.comment && !j.schedule && !j.command)
        );
        return { data: filtered, success: true, total: filtered.length };
      }}
      pagination={{ pageSize: 20 }}
    />
  );
};

export default CronJobs;
