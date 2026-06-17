import {
  CloudServerOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
  HistoryOutlined,
} from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import { Tag, Button, Space, message, Tooltip, Card, Typography, Select, DatePicker, Popconfirm, Checkbox, Progress, Tabs } from 'antd';
import DashboardPipeline from './DashboardPipeline';
import { useEffect, useRef, useState } from 'react';
import dayjs from 'dayjs';
import { api } from '../api';

const { Text } = Typography;

// ── Types ────────────────────────────────────────────────────────────────────

interface CollectorStatus {
  ws_collector: string;
  last_heartbeat: string | null;
}

interface CollectorStatus {
  ws_collector: string;
  last_heartbeat: string | null;
}

interface BackfillCategory {
  key: string;
  label: string;
  tables: BackfillTable[];
}

interface BackfillTable {
  key: string;
  label: string;
  market: string;
}

interface OverviewRow {
  date: string;
  table: string;
  symbols: number;
  total: number;
  coverage: string;
  rows: number;
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
  const [subStats, setSubStats] = useState({ subscriptions: 0, buffer: 0, bars_received: 0 });
  const [rtQuota, setRtQuota] = useState<{ used: number; remain: number } | null>(null);
  const [histQuota, setHistQuota] = useState<{ remain: number; today_used: number } | null>(null);

  // ── Backfill state ──
  const [backfillCategories, setBackfillCategories] = useState<BackfillCategory[]>([]);
  const [backfillCategory, setBackfillCategory] = useState('kline');
  const [backfillTables, setBackfillTables] = useState<string[]>([]);
  const [backfillDates, setBackfillDates] = useState<[string, string] | null>(null);
  const [backfilling, setBackfilling] = useState(false);
  const BF_STORAGE_KEY = 'backfill_running';

  // Persist backfill state across page refreshes
  const setBackfillingPersist = (v: boolean, taskIds?: number[]) => {
    setBackfilling(v);
    try {
      if (v && taskIds) {
        localStorage.setItem(BF_STORAGE_KEY, JSON.stringify(taskIds));
      } else {
        localStorage.removeItem(BF_STORAGE_KEY);
      }
    } catch {}
  };

  // Restore backfill state on mount
  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(BF_STORAGE_KEY) || 'null');
      if (stored && Array.isArray(stored) && stored.length > 0) {
        Promise.all(stored.map((tid: number) => api.get(`/api/admin/tasks/${tid}`)))
          .then((results) => {
            const anyActive = results.some((t: any) => t.status === 'pending' || t.status === 'running');
            if (anyActive) {
              setBackfilling(true);
              // Poll until done
              const activeIds = stored.filter((_: any, i: number) =>
                results[i].status === 'pending' || results[i].status === 'running'
              );
              Promise.all(activeIds.map((tid: number) => pollTask(tid).catch(() => {})))
                .finally(() => setBackfillingPersist(false));
            } else {
              localStorage.removeItem(BF_STORAGE_KEY);
            }
          }).catch(() => {});
      }
    } catch {}
  }, []);
  const [backfillSources, setBackfillSources] = useState<{key: string; label: string}[]>([]);
  const [backfillSource, setBackfillSource] = useState('auto');
  const [backfillMode, setBackfillMode] = useState<'skip_existing' | 'replace'>('skip_existing');
  const [backfillProgress, setBackfillProgress] = useState<{ done: number; total: number } | null>(null);
  const [_bfTaskId, setBackfillTaskId] = useState<number | null>(null);

  // ── Collector action state (survives page refresh) ──
  const [collectorAction, setCollectorAction] = useState<string | null>(null);

  const currentCategory = backfillCategories.find((c) => c.key === backfillCategory);
  const availableTables = currentCategory?.tables || [];
  const filteredTables = availableTables.filter((t) => backfillTables.includes(t.key));

  const handleBackfill = async () => {
    if (!backfillDates || backfillTables.length === 0) return;
    setBackfillingPersist(true);
    try {
      const params = new URLSearchParams({
        tables: backfillTables.join(','),
        start: backfillDates[0],
        end: backfillDates[1],
        source: backfillSource,
        mode: backfillMode,
      });
      const data = await api.post(`/api/admin/data/backfill?${params.toString()}`);
      message.success(`${data.count || 0} 个回填任务已创建`);
      if (data.task_ids?.length) {
        setBackfillTaskId(data.task_ids[0]);
        setBackfillProgress(null);
        localStorage.setItem(BF_STORAGE_KEY, JSON.stringify(data.task_ids));
        // Poll until all tasks complete
        await Promise.all(data.task_ids.map((tid: number) => pollTask(tid).catch(() => {})));
      }
      actionRef.current?.reload();
    } catch (err: any) {
      message.error(`回填失败: ${err.message}`);
    } finally {
      setBackfillingPersist(false);
    }
  };

  const loadCollector = () => {
    api.get('/api/admin/data/collectors').then((data) => {
      setCollector({ ws_collector: data.ws_collector, last_heartbeat: data.last_heartbeat });
      setSubStats({ subscriptions: data.subscriptions || 0, buffer: data.buffer || 0, bars_received: data.bars_received || 0 });
      setRtQuota(data.rt_quota || null);
      setHistQuota(data.hist_quota || null);
    }).catch(() => {});
  };

  const loadBackfillOptions = () => {
    api.get('/api/admin/data/backfill/options').then((data) => {
      if (data?.categories) setBackfillCategories(data.categories);
      if (data?.sources) setBackfillSources(data.sources);
    }).catch(() => {});
  };

  useEffect(() => {
    loadCollector();
    loadBackfillOptions();
    // Resume in-flight collector actions on page refresh
    (async () => {
      try {
        const d = await api.get('/api/tasks');
        for (const t of (d.tasks || [])) {
          if (t.status === 'pending' || t.status === 'running') {
            const cmd = t.params?.cmd || '';
            if (cmd.includes('ws_collector')) {
              const action = cmd.includes('stop') ? 'stop' : cmd.includes('restart') ? 'restart' : cmd.includes('start') ? 'start' : null;
              if (action) {
                setCollectorAction(action);
                try { await pollTask(t.id); } catch {}
                setCollectorAction(null);
                loadCollector();
              }
            }
          }
        }
      } catch {}
    })();
  }, []);

  const handleCollectorAction = async (action: string) => {
    setCollectorAction(action);
    try {
      const data = await api.post(`/api/admin/data/collector/${action}`);
      message.loading(`Task #${data.task_id}: ${action}ing ws-collector...`, 1);
      try {
        await pollTask(data.task_id);
        message.success(`${action} ws-collector completed`);
        loadCollector();
      } catch (err: any) {
        message.error(`${action} ws-collector: ${err.message}`);
      }
    } catch (err: any) {
      message.error(`${action} ws-collector failed: ${err.message}`);
    } finally {
      setCollectorAction(null);
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

  // ── Data Overview (daily coverage) ─────────────────────────────────────────

  const DataOverview = () => {
    const [data, setData] = useState<OverviewRow[]>([]);
    const [loading, setLoading] = useState(false);
    const [dates, setDates] = useState<[dayjs.Dayjs, dayjs.Dayjs]>([
      dayjs().subtract(7, 'day'),
      dayjs(),
    ]);

    const fetch = async () => {
      if (!dates[0] || !dates[1]) return;
      setLoading(true);
      try {
        const res = await api.get(
          `/api/admin/data/overview?start=${dates[0].format('YYYY-MM-DD')}&end=${dates[1].format('YYYY-MM-DD')}`
        );
        setData(res.rows || []);
      } finally {
        setLoading(false);
      }
    };

    useEffect(() => { fetch(); }, [dates]);

    // Group by date for a compact view, or show flat table
    const overviewColumns: ProColumns<OverviewRow>[] = [
      {
        title: '日期',
        dataIndex: 'date',
        width: 110,
        sorter: (a, b) => a.date.localeCompare(b.date),
      },
      {
        title: '表名',
        dataIndex: 'table',
        width: 160,
        render: (_, r) => <Tag>{r.table}</Tag>,
      },
      {
        title: '覆盖',
        dataIndex: 'coverage',
        width: 120,
        align: 'right',
        render: (_, r) => {
          const pct = r.total > 0 ? (r.symbols / r.total) * 100 : 0;
          const color = pct >= 99 ? 'green' : pct >= 80 ? 'orange' : 'red';
          return <Tag color={color}>{r.coverage}</Tag>;
        },
      },
      {
        title: '行数',
        dataIndex: 'rows',
        width: 120,
        align: 'right',
        render: (_, r) => r.rows.toLocaleString(),
      },
    ];

    return (
      <>
        <Card size="small" style={{ marginBottom: 12 }}>
          <Space>
            <Text strong>日期范围：</Text>
            <DatePicker.RangePicker
              value={dates}
              onChange={(v) => { if (v?.[0] && v?.[1]) setDates([v[0], v[1]]); }}
              allowClear={false}
            />
            <Button icon={<ReloadOutlined />} onClick={fetch} loading={loading}>刷新</Button>
          </Space>
        </Card>
        <ProTable<OverviewRow>
          headerTitle={`数据概览 (${dates[0]?.format('MM-DD')} ~ ${dates[1]?.format('MM-DD')})`}
          rowKey={(r) => `${r.date}_${r.table}`}
          search={false}
          loading={loading}
          columns={overviewColumns}
          dataSource={data}
          pagination={{ pageSize: 50 }}
        />
      </>
    );
  };

  const tabItems = [
    {
      key: 'collector',
      label: 'Collector',
      children: (
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
                    loading={collectorAction === 'start'}
                    disabled={collectorAction !== null}
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
                    loading={collectorAction === 'stop'}
                    disabled={collectorAction !== null}
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
                  loading={collectorAction === 'restart'}
                  disabled={collectorAction !== null}
                >
                  Restart
                </Button>
              </Tooltip>
            </Space>
          }
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
            <Space>
              <Text strong>实时订阅:</Text>
              <Text>{subStats.subscriptions} 个</Text>
              {rtQuota ? (
                <Text>配额: {rtQuota.used}/{rtQuota.used + rtQuota.remain}</Text>
              ) : (
                <Text type="secondary">配额: 加载中...</Text>
              )}
              <Text type="secondary">| 缓冲: {subStats.buffer}</Text>
              <Text type="secondary">| 已收: {subStats.bars_received.toLocaleString()} bars</Text>
            </Space>
            <Space>
              <Text strong>历史订阅:</Text>
              {histQuota ? (
                <Text>今日已用: {histQuota.today_used} / 剩余: {histQuota.remain}</Text>
              ) : (
                <Text type="secondary">加载中...</Text>
              )}
            </Space>
          </Space>
        </Card>
      ),
    },
    {
      key: 'backfill',
      label: '数据回填',
      children: (
        <Card
          title={
            <Space>
              <HistoryOutlined />
              <span>数据回填</span>
            </Space>
          }
        >
          <Space direction="vertical" style={{ width: '100%' }}>
            <Space wrap>
              <Text strong>数据类别:</Text>
              <Select
                value={backfillCategory}
                onChange={(v) => { setBackfillCategory(v); setBackfillTables([]); }}
                style={{ width: 160 }}
                options={backfillCategories.map((c) => ({ value: c.key, label: c.label }))}
              />
              <Text strong>数据源:</Text>
              <Select
                value={backfillSource}
                onChange={setBackfillSource}
                style={{ width: 220 }}
                options={backfillSources.map((s) => ({ value: s.key, label: s.label }))}
              />
              <Text strong>模式:</Text>
              <Select
                value={backfillMode}
                onChange={setBackfillMode}
                style={{ width: 160 }}
                options={[
                  { value: 'skip_existing', label: '🛡️ 跳过已有 (安全)' },
                  { value: 'replace', label: '⚠️ 覆盖已有' },
                ]}
              />
              <Text strong>日期:</Text>
              <DatePicker.RangePicker
                onChange={(dates) => {
                  if (dates && dates[0] && dates[1]) {
                    setBackfillDates([dates[0].format('YYYY-MM-DD'), dates[1].format('YYYY-MM-DD')]);
                  } else {
                    setBackfillDates(null);
                  }
                }}
              />
              <Popconfirm
                title={`确认回填 ${filteredTables.length} 个表？`}
                description={backfillDates ? `${backfillDates[0]} ~ ${backfillDates[1]}` : ''}
                onConfirm={handleBackfill}
                okText="确认"
                cancelText="取消"
                disabled={backfillTables.length === 0 || !backfillDates}
              >
                <Button
                  type="primary"
                  icon={<HistoryOutlined />}
                  loading={backfilling}
                  disabled={backfillTables.length === 0 || !backfillDates}
                >
                  开始回填
                </Button>
              </Popconfirm>
            </Space>
            {backfillProgress && (
              <Progress percent={Math.round(backfillProgress.done / backfillProgress.total * 100)}
                format={() => `${backfillProgress.done}/${backfillProgress.total}`}
                status="active" style={{ maxWidth: 400, marginTop: 8 }} />
            )}
            {availableTables.length > 0 && (
              <Checkbox.Group
                options={availableTables.map((t) => ({ label: t.label, value: t.key }))}
                value={backfillTables}
                onChange={(v) => setBackfillTables(v as string[])}
              />
            )}
          </Space>
        </Card>
      ),
    },
    {
      key: 'pipeline',
      label: 'Pipeline',
      children: <DashboardPipeline />,
    },
    {
      key: 'alert',
      label: 'Alert',
      children: (
        <Card>
          <Text>Alert — 告警中心（待完善）</Text>
        </Card>
      ),
    },
    {
      key: 'overview',
      label: '数据概览',
      children: <DataOverview />,
    },
  ];

  return (
    <Tabs defaultActiveKey="collector" items={tabItems} />
  );
};

export default DataMap;
