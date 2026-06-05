import {
  ExperimentOutlined,
  CodeOutlined,
  CloudOutlined,
} from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns } from '@ant-design/pro-table';
import {
  Tag,
  Button,
  Tabs,
  Drawer,
  message,
  Spin,
  List,
  Empty,
  Input,
  Table,
  Modal,
  Checkbox,
  Space,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState, useCallback } from 'react';
import { api } from '../api';

// ── Types ───────────────────────────────────────────────────────────────────

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

interface VersionDetail {
  version: string;
  stage: string;
  run_id: string;
  rmse: number | null;
  ic: number | null;
  n_features: number;
  dataset: string;
  training_time: number | null;
}

interface TrainingHistory {
  version: string;
  run_id: string;
  rmse: number | null;
  ic: number | null;
  dataset: string;
  n_features: number;
  n_trials: number;
}

interface CompareVersion extends VersionDetail {
  modelName: string;
}

interface StrategyItem {
  name: string;
  path: string;
}

const { TextArea } = Input;

// ── Models Tab ──────────────────────────────────────────────────────────────

const ModelsTab: React.FC = () => {
  const [versionData, setVersionData] = useState<Record<string, VersionDetail[]>>({});
  const [versionLoading, setVersionLoading] = useState<Record<string, boolean>>({});
  const [versionError, setVersionError] = useState<Record<string, string>>({});
  const [trainingHistory, setTrainingHistory] = useState<Record<string, TrainingHistory[]>>({});
  const [historyLoading, setHistoryLoading] = useState<Record<string, boolean>>({});
  const [selectedCompare, setSelectedCompare] = useState<CompareVersion[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [stageLoading, setStageLoading] = useState<string | null>(null);

  const handleTrain = useCallback(async (modelName: string) => {
    try {
      const market = modelName.startsWith('hk') ? 'hk' : 'us';
      await api.post(`/api/admin/models/train?model_name=${modelName}&market=${market}`);
      message.success(`Training queued for ${modelName}`);
    } catch (err: any) {
      message.error(`Failed to queue training: ${err.message}`);
    }
  }, []);

  const fetchVersions = useCallback(async (modelName: string) => {
    setVersionLoading((prev) => ({ ...prev, [modelName]: true }));
    setVersionError((prev) => {
      const next = { ...prev };
      delete next[modelName];
      return next;
    });
    try {
      const data = await api.get(`/api/admin/models/${modelName}/versions`);
      if (!Array.isArray(data)) {
        const errDetail = data && typeof data === 'object' ? (data as any).error : 'Invalid response';
        throw new Error(errDetail || 'Invalid response');
      }
      setVersionData((prev) => ({ ...prev, [modelName]: data as VersionDetail[] }));
    } catch (err: any) {
      const msg = err?.message || String(err);
      setVersionError((prev) => ({ ...prev, [modelName]: msg }));
      message.error(`Failed to load versions for ${modelName}: ${msg}`);
    } finally {
      setVersionLoading((prev) => ({ ...prev, [modelName]: false }));
    }
  }, []);

  const fetchTrainingHistory = useCallback(async (modelName: string) => {
    setHistoryLoading((prev) => ({ ...prev, [modelName]: true }));
    try {
      const data = await api.get(`/api/admin/models/${modelName}/history`);
      setTrainingHistory((prev) => ({ ...prev, [modelName]: data as TrainingHistory[] }));
    } catch (err: any) {
      message.error(`Failed to load history for ${modelName}: ${err.message}`);
    } finally {
      setHistoryLoading((prev) => ({ ...prev, [modelName]: false }));
    }
  }, []);

  const handleExpand = useCallback(
    (expanded: boolean, record: ModelItem) => {
      if (expanded) {
        if (!versionData[record.name] && !versionLoading[record.name]) {
          fetchVersions(record.name);
        }
        if (!trainingHistory[record.name] && !historyLoading[record.name]) {
          fetchTrainingHistory(record.name);
        }
      }
    },
    [versionData, versionLoading, fetchVersions, trainingHistory, historyLoading, fetchTrainingHistory],
  );

  const handleCompareToggle = useCallback(
    (version: CompareVersion, checked: boolean) => {
      setSelectedCompare((prev) => {
        if (checked) {
          if (prev.length >= 2) {
            message.warning('最多只能选择2个版本进行对比');
            return prev;
          }
          return [...prev, version];
        }
        return prev.filter(
          (v) => !(v.modelName === version.modelName && v.version === version.version),
        );
      });
    },
    [],
  );

  const handleStageChange = useCallback(
    async (modelName: string, version: string, stage: string, stageLabel: string) => {
      const loadingKey = `${modelName}::${version}::${stage}`;
      setStageLoading(loadingKey);
      try {
        await api.post(
          `/api/admin/models/${modelName}/stage?version=${version}&stage=${stage}`,
        );
        message.success(`Version ${version} → ${stageLabel}`);
        // Refresh version data
        fetchVersions(modelName);
        // Clear compare selection for this model
        setSelectedCompare((prev) =>
          prev.filter((v) => !(v.modelName === modelName && v.version === version)),
        );
      } catch (err: any) {
        message.error(`Stage change failed: ${err.message}`);
      } finally {
        setStageLoading(null);
      }
    },
    [fetchVersions],
  );

  // ── Version detail table columns ────────────────────────────────────────

  const versionColumns = (modelName: string): ColumnsType<VersionDetail> => [
    {
      title: '选择',
      width: 50,
      render: (_, r) => {
        const isChecked = selectedCompare.some(
          (v) => v.modelName === modelName && v.version === r.version,
        );
        return (
          <Checkbox
            checked={isChecked}
            onChange={(e) =>
              handleCompareToggle({ ...r, modelName }, e.target.checked)
            }
          />
        );
      },
    },
    {
      title: '版本',
      dataIndex: 'version',
      width: 60,
      key: 'version',
    },
    {
      title: 'Stage',
      dataIndex: 'stage',
      width: 100,
      key: 'stage',
      render: (s: string) => {
        const color =
          s === 'Production' ? 'green' : s === 'Archived' ? 'red' : 'default';
        return <Tag color={color}>{s || '-'}</Tag>;
      },
    },
    {
      title: 'RMSE',
      dataIndex: 'rmse',
      width: 90,
      key: 'rmse',
      render: (v: number | null) => (v != null ? v.toFixed(4) : '-'),
    },
    {
      title: 'IC',
      dataIndex: 'ic',
      width: 90,
      key: 'ic',
      render: (v: number | null) => (v != null ? v.toFixed(4) : '-'),
    },
    {
      title: '特征数',
      dataIndex: 'n_features',
      width: 70,
      key: 'n_features',
    },
    {
      title: '训练时长',
      dataIndex: 'training_time',
      width: 90,
      key: 'training_time',
      render: (v: number | null) => (v != null ? `${v}s` : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_, r) => {
        const lkPromote = `${modelName}::${r.version}::Production`;
        const lkArchive = `${modelName}::${r.version}::Archived`;
        return (
          <Space size="small">
            {r.stage !== 'Production' && (
              <Button
                size="small"
                loading={stageLoading === lkPromote}
                onClick={() => handleStageChange(modelName, r.version, 'Production', '生产')}
              >
                晋升生产
              </Button>
            )}
            {r.stage !== 'Archived' && (
              <Button
                size="small"
                danger
                loading={stageLoading === lkArchive}
                onClick={() => handleStageChange(modelName, r.version, 'Archived', '已归档')}
              >
                归档
              </Button>
            )}
          </Space>
        );
      },
    },
  ];

  // ── Expanded row render ─────────────────────────────────────────────────

  const expandedRowRender = (record: ModelItem) => {
    const versions = versionData[record.name];
    const loading = versionLoading[record.name];
    const err = versionError[record.name];
    const history = trainingHistory[record.name];
    const histLoading = historyLoading[record.name];

    if (err) {
      return <div style={{ padding: 16, color: '#ff4d4f' }}>加载失败: {err}</div>;
    }

    return (
      <div style={{ padding: '0 24px 16px' }}>
        <div style={{ marginBottom: 8 }}>
          <Button
            type="primary"
            size="small"
            disabled={selectedCompare.length !== 2}
            onClick={() => setCompareOpen(true)}
          >
            对比选中版本 ({selectedCompare.length}/2)
          </Button>
        </div>
        <Table
          dataSource={versions || []}
          loading={loading}
          rowKey="version"
          columns={versionColumns(record.name)}
          pagination={false}
          size="small"
          locale={{ emptyText: '暂无版本数据' }}
        />

        <h4 style={{ margin: '12px 0 8px', fontSize: 14 }}>Training History</h4>
        <Table<TrainingHistory>
          dataSource={history || []}
          loading={histLoading}
          rowKey="run_id"
          size="small"
          pagination={false}
          locale={{ emptyText: '暂无训练记录' }}
          columns={[
            { title: '版本', dataIndex: 'version', width: 60, key: 'version' },
            {
              title: 'RMSE',
              dataIndex: 'rmse',
              width: 90,
              key: 'rmse',
              render: (v: number | null) => (v != null ? v.toFixed(4) : '-'),
            },
            {
              title: 'IC',
              dataIndex: 'ic',
              width: 90,
              key: 'ic',
              render: (v: number | null) => (v != null ? v.toFixed(4) : '-'),
            },
            { title: 'Trials', dataIndex: 'n_trials', width: 70, key: 'n_trials' },
            { title: 'Features', dataIndex: 'n_features', width: 70, key: 'n_features' },
            { title: 'Dataset', dataIndex: 'dataset', key: 'dataset', ellipsis: true },
          ]}
        />
      </div>
    );
  };

  // ── Model table columns ─────────────────────────────────────────────────

  const columns: ProColumns<ModelItem>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      width: 150,
      key: 'name',
    },
    {
      title: '版本数',
      dataIndex: 'version_count',
      width: 70,
      key: 'version_count',
      align: 'center',
    },
    {
      title: '最新',
      dataIndex: 'latest_version',
      width: 70,
      key: 'latest_version',
      align: 'center',
    },
    {
      title: '状态',
      dataIndex: 'stage',
      width: 100,
      key: 'stage',
      render: (_, r) => {
        const color = r.stage === 'Production' ? 'green' : 'default';
        return <Tag color={color}>{r.stage || '-'}</Tag>;
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
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

  // ── Compare Modal ───────────────────────────────────────────────────────

  const renderCompareModal = () => {
    if (selectedCompare.length !== 2) return null;

    const [a, b] = selectedCompare;
    const metrics: { label: string; key: string; format: (_v: any) => string }[] = [
      { label: 'RMSE', key: 'rmse', format: (v: number | null) => (v != null ? v.toFixed(4) : '-') },
      { label: 'IC', key: 'ic', format: (v: number | null) => (v != null ? v.toFixed(4) : '-') },
      { label: '特征数', key: 'n_features', format: (v: number) => String(v) },
      { label: '训练时长', key: 'training_time', format: (v: number | null) => (v != null ? `${v}s` : '-') },
      { label: '数据集', key: 'dataset', format: (v: string) => v || '-' },
    ];

    return (
      <Modal
        title="版本对比"
        open={compareOpen}
        onCancel={() => setCompareOpen(false)}
        footer={null}
        width={600}
      >
        <Table
          dataSource={metrics.map((m, i) => ({
            key: i,
            metric: m.label,
            left: m.format((a as any)[m.key]),
            right: m.format((b as any)[m.key]),
          }))}
          pagination={false}
          size="small"
          columns={[
            { title: '指标', dataIndex: 'metric', width: 100, key: 'metric' },
            {
              title: `${a.modelName} v${a.version}${a.stage ? ` (${a.stage})` : ''}`,
              dataIndex: 'left',
              key: 'left',
            },
            {
              title: `${b.modelName} v${b.version}${b.stage ? ` (${b.stage})` : ''}`,
              dataIndex: 'right',
              key: 'right',
            },
          ]}
        />
      </Modal>
    );
  };

  return (
    <>
      <ProTable<ModelItem>
        headerTitle="注册模型"
        rowKey="name"
        search={false}
        columns={columns}
        expandable={{
          expandedRowRender,
          onExpand: handleExpand,
        }}
        request={async () => {
          try {
            const data: any = await api.get('/api/admin/models');
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
      {renderCompareModal()}
    </>
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
