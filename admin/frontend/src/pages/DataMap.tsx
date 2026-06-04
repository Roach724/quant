import {
  CloudServerOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import { Tag, Button, Space, message, Tooltip, Card, Drawer, Table, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import dayjs from 'dayjs';
import { api } from '../api';

const { Text } = Typography;

// ── Types ────────────────────────────────────────────────────────────────────

interface CollectorStatus {
  ws_collector: string;
  last_heartbeat: string | null;
}

interface TableSchema {
  name: string;
  type: string;
}

interface DataTableItem {
  table_name: string;
  row_count: number;
  last_write: string | null;
  schema: TableSchema[];
}

// ── Poll helper ──────────────────────────────────────────────────────────────

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

// ── Component ────────────────────────────────────────────────────────────────

const DataMap: React.FC = () => {
  const actionRef = useRef<ActionType | undefined>(undefined);
  const [collector, setCollector] = useState<CollectorStatus>({
    ws_collector: 'unknown',
    last_heartbeat: null,
  });
  const [schemaDrawer, setSchemaDrawer] = useState<{
    open: boolean;
    tableName: string;
    columns: TableSchema[];
  }>({ open: false, tableName: '', columns: [] });

  const loadCollector = () => {
    api.get('/api/admin/data/collectors').then(setCollector).catch(() => {});
  };

  useEffect(() => {
    loadCollector();
  }, []);

  const handleCollectorAction = async (action: string) => {
    try {
      const data = await api.post(`/api/admin/data/collector/${action}`);
      const hide = message.loading(`Task #${data.task_id}: ${action}ing ws-collector...`, 0);
      try {
        await pollTask(data.task_id);
        hide();
        message.success(`${action} ws-collector completed`);
        loadCollector();
      } catch (err: any) {
        hide();
        message.error(`${action} ws-collector: ${err.message}`);
      }
    } catch (err: any) {
      message.error(`${action} ws-collector failed: ${err.message}`);
    }
  };

  const statusTag = (status: string) => {
    switch (status) {
      case 'active':
        return <Tag color="green">Running</Tag>;
      case 'inactive':
      case 'failed':
        return <Tag color="red">Stopped</Tag>;
      default:
        return <Tag color="default">{status}</Tag>;
    }
  };

  const columns: ProColumns<DataTableItem>[] = [
    {
      title: 'Table',
      dataIndex: 'table_name',
      width: 220,
      key: 'table_name',
      ellipsis: true,
    },
    {
      title: 'Rows',
      dataIndex: 'row_count',
      width: 120,
      key: 'row_count',
      render: (_, r) => r.row_count.toLocaleString(),
    },
    {
      title: 'Last Write',
      dataIndex: 'last_write',
      width: 180,
      key: 'last_write',
      render: (_, r) =>
        r.last_write ? dayjs(r.last_write).format('YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: 'Schema',
      key: 'schema',
      width: 100,
      render: (_, r) => (
        <Button
          size="small"
          onClick={() =>
            setSchemaDrawer({
              open: true,
              tableName: r.table_name,
              columns: r.schema,
            })
          }
        >
          {r.schema.length} cols
        </Button>
      ),
    },
  ];

  return (
    <>
      {/* ── Collector Status Card ─────────────────────────────────────────── */}
      <Card
        title={
          <Space>
            <CloudServerOutlined />
            <span>ws-collector</span>
          </Space>
        }
        extra={
          <Space>
            {collector.ws_collector !== 'active' && (
              <Tooltip title="Start">
                <Button
                  type="primary"
                  size="small"
                  icon={<PlayCircleOutlined />}
                  onClick={() => handleCollectorAction('start')}
                >
                  Start
                </Button>
              </Tooltip>
            )}
            {collector.ws_collector === 'active' && (
              <Tooltip title="Stop">
                <Button
                  size="small"
                  icon={<PauseCircleOutlined />}
                  onClick={() => handleCollectorAction('stop')}
                >
                  Stop
                </Button>
              </Tooltip>
            )}
            <Tooltip title="Restart">
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => handleCollectorAction('restart')}
              >
                Restart
              </Button>
            </Tooltip>
          </Space>
        }
        style={{ marginBottom: 16 }}
      >
        <Space direction="vertical">
          <Space>
            <Text strong>Status:</Text>
            {statusTag(collector.ws_collector)}
          </Space>
          <Space>
            <Text strong>Last Heartbeat:</Text>
            <Text>
              {collector.last_heartbeat
                ? dayjs(collector.last_heartbeat).format('YYYY-MM-DD HH:mm:ss')
                : 'N/A'}
            </Text>
          </Space>
        </Space>
      </Card>

      {/* ── Data Map Table ─────────────────────────────────────────────────── */}
      <ProTable<DataTableItem>
        headerTitle="BQ Tables"
        actionRef={actionRef}
        rowKey="table_name"
        search={false}
        columns={columns}
        request={async () => {
          const data = await api.get('/api/admin/data/tables');
          return { data, success: true, total: data.length };
        }}
        pagination={{ pageSize: 20 }}
      />

      {/* ── Schema Drawer ──────────────────────────────────────────────────── */}
      <Drawer
        title={`Schema: ${schemaDrawer.tableName}`}
        open={schemaDrawer.open}
        onClose={() => setSchemaDrawer({ open: false, tableName: '', columns: [] })}
        width={400}
      >
        <Table
          dataSource={schemaDrawer.columns}
          rowKey="name"
          pagination={false}
          size="small"
          columns={[
            { title: 'Column', dataIndex: 'name', key: 'name' },
            {
              title: 'Type',
              dataIndex: 'type',
              key: 'type',
              render: (t: string) => <Tag>{t}</Tag>,
            },
          ]}
        />
      </Drawer>
    </>
  );
};

export default DataMap;
