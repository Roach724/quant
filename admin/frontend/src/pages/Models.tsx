import {
  ExperimentOutlined,
  CodeOutlined,
  CloudOutlined,
} from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns } from '@ant-design/pro-table';
import { Tag, Button, Tabs, Drawer, message, Spin, List, Empty, Input } from 'antd';
import { useState, useCallback } from 'react';
import { api } from '../api';

interface ModelVersion {
  version: string;
  stage: string;
  run_id: string;
}

interface ModelItem {
  name: string;
  versions: ModelVersion[];
  version_count?: number;
  latest_version?: string;
  stage?: string;
}

interface StrategyItem {
  name: string;
  path: string;
}

const { TextArea } = Input;

// ── Models Tab ──────────────────────────────────────────────────────────────

const ModelsTab: React.FC = () => {
  const handleTrain = useCallback(async (modelName: string) => {
    try {
      const market = modelName.startsWith('hk') ? 'hk' : 'us';
      await api.post(`/api/admin/models/train?model_name=${modelName}&market=${market}`);
      message.success(`Training queued for ${modelName}`);
    } catch (err: any) {
      message.error(`Failed to queue training: ${err.message}`);
    }
  }, []);

  const columns: ProColumns<ModelItem>[] = [
    {
      title: 'Name',
      dataIndex: 'name',
      width: 150,
      key: 'name',
    },
    {
      title: 'Versions',
      dataIndex: 'version_count',
      width: 80,
      key: 'version_count',
      align: 'center',
    },
    {
      title: 'Latest',
      dataIndex: 'latest_version',
      width: 80,
      key: 'latest_version',
      align: 'center',
    },
    {
      title: 'Stage',
      dataIndex: 'stage',
      width: 100,
      key: 'stage',
      render: (_, r) => {
        const color = r.stage === 'Production' ? 'green' : 'default';
        return <Tag color={color}>{r.stage || '-'}</Tag>;
      },
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 120,
      render: (_, r) => (
        <Button
          type="primary"
          size="small"
          icon={<ExperimentOutlined />}
          onClick={() => handleTrain(r.name)}
        >
          训练
        </Button>
      ),
    },
  ];

  return (
    <ProTable<ModelItem>
      headerTitle="注册模型"
      rowKey="name"
      search={false}
      columns={columns}
      request={async () => {
        try {
          const data: any = await api.get('/api/admin/models');
          // Handle error response from backend
          if (!Array.isArray(data)) {
            const errMsg = data?.error || 'Unknown error';
            message.error(`MLflow: ${errMsg}`);
            return { data: [], success: false, total: 0 };
          }
          const enriched = (data as ModelItem[]).map((m: ModelItem) => ({
            ...m,
            version_count: m.versions?.length ?? 0,
            latest_version: m.versions?.[m.versions.length - 1]?.version ?? '-',
            stage: m.versions?.[m.versions.length - 1]?.stage ?? '-',
          }));
          return { data: enriched, success: true, total: enriched.length };
        } catch (err: any) {
          message.error(`Failed to load models: ${err?.message || err}`);
          return { data: [], success: false, total: 0 };
        }
      }}
      pagination={false}
    />
  );
};

// ── Strategies Tab ──────────────────────────────────────────────────────────

const StrategiesTab: React.FC = () => {
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingName, setEditingName] = useState('');
  const [sourceCode, setSourceCode] = useState('');
  const [saving, setSaving] = useState(false);

  const loadStrategies = useCallback(async () => {
    setLoading(true);
    try {
      const data: StrategyItem[] = await api.get('/api/admin/strategies');
      setStrategies(data || []);
    } catch (err: any) {
      message.error(`Failed to load strategies: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const openStrategy = useCallback(async (name: string) => {
    setEditingName(name);
    setSourceCode('');
    try {
      const data = await api.get(`/api/admin/strategies/${name}`);
      if (data.error) {
        message.error(data.error);
        return;
      }
      setSourceCode(data.source || '');
      setDrawerOpen(true);
    } catch (err: any) {
      message.error(`Failed to read strategy: ${err.message}`);
    }
  }, []);

  const saveStrategy = useCallback(async () => {
    setSaving(true);
    try {
      await api.put(`/api/admin/strategies/${editingName}`, { source: sourceCode });
      message.success(`Saved ${editingName}`);
      setDrawerOpen(false);
    } catch (err: any) {
      message.error(`Failed to save: ${err.message}`);
    } finally {
      setSaving(false);
    }
  }, [editingName, sourceCode]);

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Button
          type="primary"
          icon={<CodeOutlined />}
          onClick={loadStrategies}
          loading={loading}
        >
          加载策略列表
        </Button>
      </div>
      {loading ? (
        <Spin />
      ) : strategies.length === 0 ? (
        <Empty description="点击上方按钮加载策略列表" />
      ) : (
        <List
          bordered
          dataSource={strategies}
          renderItem={(item) => (
            <List.Item
              style={{ cursor: 'pointer' }}
              onClick={() => openStrategy(item.name)}
            >
              <List.Item.Meta
                avatar={<CodeOutlined style={{ fontSize: 18 }} />}
                title={item.name}
                description={item.path}
              />
            </List.Item>
          )}
        />
      )}
      <Drawer
        title={`编辑策略: ${editingName}`}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={700}
        footer={
          <div style={{ textAlign: 'right' }}>
            <Button onClick={() => setDrawerOpen(false)} style={{ marginRight: 8 }}>
              取消
            </Button>
            <Button type="primary" loading={saving} onClick={saveStrategy}>
              保存
            </Button>
          </div>
        }
      >
        <TextArea
          value={sourceCode}
          onChange={(e) => setSourceCode(e.target.value)}
          style={{
            fontFamily: '"Fira Code", "Consolas", "Monaco", monospace',
            fontSize: 13,
            background: '#1e1e1e',
            color: '#d4d4d4',
            minHeight: 500,
            borderColor: '#333',
          }}
        />
      </Drawer>
    </div>
  );
};

// ── MLflow Tab ──────────────────────────────────────────────────────────────

const MLflowTab: React.FC = () => (
  <iframe
    src="http://localhost:5000"
    title="MLflow UI"
    style={{ width: '100%', height: 'calc(100vh - 200px)', border: 'none' }}
  />
);

// ── Models Page ─────────────────────────────────────────────────────────────

const Models: React.FC = () => {
  const tabItems = [
    {
      key: 'models',
      label: (
        <span>
          <ExperimentOutlined /> 模型
        </span>
      ),
      children: <ModelsTab />,
    },
    {
      key: 'strategies',
      label: (
        <span>
          <CodeOutlined /> 策略
        </span>
      ),
      children: <StrategiesTab />,
    },
    {
      key: 'mlflow',
      label: (
        <span>
          <CloudOutlined /> MLflow
        </span>
      ),
      children: <MLflowTab />,
    },
  ];

  return <Tabs defaultActiveKey="models" items={tabItems} />;
};

export default Models;
