import { useState, useEffect, useCallback } from 'react';
import { Table, Button, message, Tag, Popconfirm, Typography, Space } from 'antd';
import { ReloadOutlined, DeleteOutlined, SyncOutlined } from '@ant-design/icons';
import { api } from '../api';

const { Text } = Typography;

interface CacheModuleInfo {
  name: string;
  ttl: number;
  max_size: number;
  current_size: number;
  hits: number;
  misses: number;
  hit_rate: number;
}

function fmtTTL(seconds: number): string {
  if (seconds >= 86400) {
    const days = seconds / 86400;
    return days === 1 ? '1d' : `${days}d`;
  }
  if (seconds >= 3600) return `${seconds / 3600}h`;
  if (seconds >= 60) return `${seconds / 60}min`;
  return `${seconds}s`;
}

export default function CacheManager() {
  const [modules, setModules] = useState<CacheModuleInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshingModule, setRefreshingModule] = useState<string | null>(null);

  const fetchModules = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get('/api/admin/cache/modules');
      setModules(data?.modules || []);
    } catch {
      message.error('获取缓存模块失败');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchModules();
  }, [fetchModules]);

  const handleInvalidate = async (moduleName: string) => {
    try {
      await api.post('/api/admin/cache/invalidate', { module: moduleName });
      message.success(`缓存 "${moduleName}" 已失效`);
      fetchModules();
    } catch {
      message.error('失效缓存失败');
    }
  };

  const handleRefresh = async (moduleName: string) => {
    setRefreshingModule(moduleName);
    try {
      await api.post('/api/admin/cache/refresh', { module: moduleName, params: {} });
      message.success(`缓存 "${moduleName}" 已刷新`);
      fetchModules();
    } catch {
      message.error('刷新缓存失败');
    }
    setRefreshingModule(null);
  };

  const handleInvalidateAll = async () => {
    try {
      await api.post('/api/admin/cache/invalidate', { module: '*' });
      message.success('全部缓存已失效');
      fetchModules();
    } catch {
      message.error('失效全部缓存失败');
    }
  };

  const columns = [
    {
      title: '模块',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => <Text strong style={{ fontFamily: 'monospace', fontSize: 13 }}>{name}</Text>,
    },
    {
      title: 'TTL',
      dataIndex: 'ttl',
      key: 'ttl',
      width: 80,
      render: (ttl: number) => <Tag>{fmtTTL(ttl)}</Tag>,
    },
    {
      title: '条目',
      dataIndex: 'current_size',
      key: 'size',
      width: 60,
      render: (size: number, row: CacheModuleInfo) => (
        <Text>{size} / {row.max_size}</Text>
      ),
    },
    {
      title: '命中率',
      dataIndex: 'hit_rate',
      key: 'hit_rate',
      width: 80,
      render: (rate: number) => (
        <Tag color={rate > 0.8 ? 'green' : rate > 0.5 ? 'orange' : 'red'}>
          {(rate * 100).toFixed(1)}%
        </Tag>
      ),
    },
    {
      title: 'Hits / Misses',
      key: 'hits_misses',
      width: 130,
      render: (_: any, row: CacheModuleInfo) => (
        <Space size="small">
          <Text type="success">{row.hits}</Text>
          <Text type="secondary">/</Text>
          <Text type="danger">{row.misses}</Text>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_: any, row: CacheModuleInfo) => (
        <Space>
          <Popconfirm
            title={`确定要刷新 "${row.name}" 缓存？`}
            onConfirm={() => handleRefresh(row.name)}
          >
            <Button
              size="small"
              icon={<SyncOutlined spin={refreshingModule === row.name} />}
              loading={refreshingModule === row.name}
            >
              刷新
            </Button>
          </Popconfirm>
          <Popconfirm
            title={`确定要失效 "${row.name}" 缓存？`}
            onConfirm={() => handleInvalidate(row.name)}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              失效
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const totalHits = modules.reduce((s, m) => s + m.hits, 0);
  const totalMisses = modules.reduce((s, m) => s + m.misses, 0);
  const totalSize = modules.reduce((s, m) => s + m.current_size, 0);
  const overallRate = totalHits + totalMisses > 0
    ? (totalHits / (totalHits + totalMisses) * 100).toFixed(1)
    : '0.0';

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <Text strong style={{ fontSize: 16 }}>📦 缓存管理</Text>
          <div style={{ marginTop: 4 }}>
            <Text type="secondary">
              模块: {modules.length} | 条目: {totalSize} | 总命中率: {overallRate}%
            </Text>
          </div>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={fetchModules} loading={loading}>
            刷新状态
          </Button>
          <Popconfirm title="确定要失效全部缓存？" onConfirm={handleInvalidateAll}>
            <Button danger icon={<DeleteOutlined />}>
              全部失效
            </Button>
          </Popconfirm>
        </Space>
      </div>

      <Table
        dataSource={modules}
        columns={columns}
        rowKey="name"
        loading={loading}
        size="small"
        pagination={false}
        locale={{ emptyText: '暂无注册的缓存模块' }}
      />
    </div>
  );
}
