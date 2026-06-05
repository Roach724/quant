import { EyeOutlined, PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons';
import ProTable from '@ant-design/pro-table';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import { Tag, Button, Drawer, Select, DatePicker, Space, message, Table, Descriptions, Tooltip, Popconfirm } from 'antd';
import { useRef, useState } from 'react';
import { api } from '../api';

const { RangePicker } = DatePicker;

interface CoverageItem {
  market: string;
  symbols: number;
  min_date: string | null;
  max_date: string | null;
  total_rows: number;
}

interface FactorItem {
  factor_id: string;
  name: string;
  category: string;
  status: string;
  markets: string[];
  coverage: CoverageItem[];
  latest_ic: number | null;
}

const marketColor: Record<string, string> = {
  us: 'blue',
  hk: 'orange',
  crypto: 'purple',
};

const Factors: React.FC = () => {
  const actionRef = useRef<ActionType | undefined>(undefined);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedFactor, setSelectedFactor] = useState<FactorItem | null>(null);
  const [evaluating, setEvaluating] = useState(false);

  // Batch compute state
  const [computeSource, setComputeSource] = useState('tech');
  const [computeMarket, setComputeMarket] = useState('us');
  const [computeDates, setComputeDates] = useState<[string, string] | null>(null);
  const [computing, setComputing] = useState(false);

  const handleCompute = async () => {
    if (!computeDates) {
      message.warning('请选择日期范围');
      return;
    }
    setComputing(true);
    try {
      const params = new URLSearchParams({
        source: computeSource,
        market: computeMarket,
        start: computeDates[0],
        end: computeDates[1],
      });
      await api.post(`/api/admin/factors/compute?${params.toString()}`);
      message.success('Task queued — factor computation started');
    } catch (err: any) {
      message.error(`Failed: ${err.message}`);
    } finally {
      setComputing(false);
    }
  };

  const handleToggle = async (factorId: string, currentActive: boolean) => {
    const newActive = !currentActive;
    try {
      const params = new URLSearchParams({ active: String(newActive) });
      await api.post(`/api/admin/factors/${factorId}/toggle?${params.toString()}`);
      message.success(`${factorId}: ${newActive ? '已启用' : '已暂停'}`);
      actionRef.current?.reload();
    } catch (err: any) {
      message.error(`Toggle failed: ${err.message}`);
    }
  };

  const handleEvaluate = async (factorId: string) => {
    setEvaluating(true);
    try {
      const data = await api.post(`/api/admin/factors/${factorId}/evaluate`);
      message.success(`Evaluation queued as task #${data.task_id}`);
    } catch (err: any) {
      message.error(`Evaluate failed: ${err.message}`);
    } finally {
      setEvaluating(false);
    }
  };

  const coverageColumns = [
    { title: 'Market', dataIndex: 'market', key: 'market', width: 60 },
    { title: 'Symbols', dataIndex: 'symbols', key: 'symbols', width: 80 },
    { title: 'Min Date', dataIndex: 'min_date', key: 'min_date', width: 100 },
    { title: 'Max Date', dataIndex: 'max_date', key: 'max_date', width: 100 },
    {
      title: 'Total Rows',
      dataIndex: 'total_rows',
      key: 'total_rows',
      width: 120,
      render: (_: any, r: CoverageItem) => r.total_rows?.toLocaleString() || '-',
    },
  ];

  const columns: ProColumns<FactorItem>[] = [
    {
      title: 'Factor ID',
      dataIndex: 'factor_id',
      width: 200,
      key: 'factor_id',
      ellipsis: true,
    },
    {
      title: 'Name',
      dataIndex: 'name',
      width: 150,
      key: 'name',
    },
    {
      title: 'Category',
      dataIndex: 'category',
      width: 100,
      key: 'category',
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 80,
      key: 'status',
      render: (_, r) => (
        <Tag color={r.status === 'active' ? 'green' : 'default'}>
          {r.status}
        </Tag>
      ),
    },
    {
      title: 'Markets',
      dataIndex: 'markets',
      width: 120,
      key: 'markets',
      render: (_, r) => (
        <Space size={4} wrap>
          {(r.markets || []).map((m) => (
            <Tag key={m} color={marketColor[m] || 'default'}>
              {m.toUpperCase()}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: 'Latest IC',
      dataIndex: 'latest_ic',
      width: 80,
      key: 'latest_ic',
      render: (_, r) =>
        r.latest_ic != null ? r.latest_ic.toFixed(4) : '-',
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 120,
      render: (_, r) => {
        const isActive = r.status === 'active';
        return (
          <Space size={4}>
            <Tooltip title={isActive ? '暂停' : '启用'}>
              <Popconfirm
                title={isActive ? `确定暂停 ${r.factor_id}?` : `确定启用 ${r.factor_id}?`}
                onConfirm={() => handleToggle(r.factor_id, isActive)}
                okText="确定"
                cancelText="取消"
              >
                <Button
                  size="small"
                  type={isActive ? 'default' : 'primary'}
                  icon={isActive ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                  danger={isActive}
                >
                  {isActive ? '暂停' : '启用'}
                </Button>
              </Popconfirm>
            </Tooltip>
          </Space>
        );
      },
    },
    {
      title: 'Detail',
      key: 'detail',
      width: 80,
      render: (_, r) => (
        <Button
          size="small"
          icon={<EyeOutlined />}
          onClick={() => {
            setSelectedFactor(r);
            setDrawerOpen(true);
          }}
        >
          查看
        </Button>
      ),
    },
  ];

  return (
    <>
      {/* Batch compute section */}
      <div
        style={{
          marginBottom: 16,
          padding: '12px 16px',
          background: '#fafafa',
          borderRadius: 6,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <span style={{ fontWeight: 500 }}>批量计算:</span>
        <Select
          value={computeSource}
          onChange={setComputeSource}
          style={{ width: 130 }}
          options={[
            { value: 'tech', label: 'Tech' },
            { value: 'fundamental', label: 'Fundamental' },
            { value: 'all', label: 'All' },
          ]}
        />
        <Select
          value={computeMarket}
          onChange={setComputeMarket}
          style={{ width: 80 }}
          options={[
            { value: 'us', label: 'US' },
            { value: 'hk', label: 'HK' },
          ]}
        />
        <RangePicker
          onChange={(dates) => {
            if (dates && dates[0] && dates[1]) {
              setComputeDates([
                dates[0].format('YYYY-MM-DD'),
                dates[1].format('YYYY-MM-DD'),
              ]);
            } else {
              setComputeDates(null);
            }
          }}
        />
        <Button type="primary" onClick={handleCompute} loading={computing}>
          开始计算
        </Button>
      </div>

      {/* Factor table */}
      <ProTable<FactorItem>
        headerTitle="Factors"
        actionRef={actionRef}
        rowKey="factor_id"
        search={false}
        columns={columns}
        request={async () => {
          const data = await api.get('/api/admin/factors');
          return { data, success: true, total: data.length };
        }}
        pagination={{ pageSize: 20 }}
      />

      {/* Detail Drawer */}
      <Drawer
        title={selectedFactor?.factor_id}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={600}
      >
        {selectedFactor && (
          <>
            <Descriptions column={2} size="small" bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="Name">{selectedFactor.name}</Descriptions.Item>
              <Descriptions.Item label="Category">{selectedFactor.category}</Descriptions.Item>
              <Descriptions.Item label="Latest IC">
                {selectedFactor.latest_ic != null
                  ? selectedFactor.latest_ic.toFixed(4)
                  : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="Status">
                <Tag color={selectedFactor.status === 'active' ? 'green' : 'default'}>
                  {selectedFactor.status}
                </Tag>
              </Descriptions.Item>
            </Descriptions>

            <Button
              type="primary"
              loading={evaluating}
              onClick={() => handleEvaluate(selectedFactor.factor_id)}
              style={{ marginBottom: 16 }}
            >
              运行评估
            </Button>

            <h4 style={{ marginBottom: 8 }}>Market Coverage</h4>
            <Table
              size="small"
              rowKey="market"
              dataSource={selectedFactor.coverage || []}
              columns={coverageColumns}
              pagination={false}
            />
          </>
        )}
      </Drawer>
    </>
  );
};

export default Factors;
