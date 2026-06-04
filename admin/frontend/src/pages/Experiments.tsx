import {
  PlayCircleOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import { Tag, Button, Space, message, Tooltip } from 'antd';
import { useRef } from 'react';
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
      api.get(`/api/admin/tasks/${taskId}`).then((data) => {
        if (data.status === 'completed') {
          resolve(data);
        } else if (data.status === 'failed') {
          reject(new Error(data.result || 'Task failed'));
        } else {
          setTimeout(check, 2000);
        }
      }).catch(reject);
    };
    check();
  });
};

const Experiments: React.FC = () => {
  const actionRef = useRef<ActionType | undefined>(undefined);

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
      width: 200,
      render: (_, r) => (
        <Space>
          {r.status !== 'running' && (
            <Tooltip title="Start">
              <Button
                type="primary"
                size="small"
                icon={<PlayCircleOutlined />}
                onClick={() => handleAction(r.exp_id, 'start')}
              />
            </Tooltip>
          )}
          {r.status === 'running' && (
            <Tooltip title="Stop">
              <Button
                size="small"
                icon={<PauseCircleOutlined />}
                onClick={() => handleAction(r.exp_id, 'stop')}
              />
            </Tooltip>
          )}
          <Tooltip title="Restart">
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => handleAction(r.exp_id, 'restart')}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
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
    />
  );
};

export default Experiments;
