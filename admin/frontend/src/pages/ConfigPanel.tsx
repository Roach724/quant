import { useRef } from 'react';
import ProTable from '@ant-design/pro-table';
import { Button, Space, message, Modal, Input, Popconfirm, Tooltip, Drawer } from 'antd';
import { PlusOutlined, EyeOutlined, SettingOutlined, DeleteOutlined } from '@ant-design/icons';
import type { ProColumns, ActionType } from '@ant-design/pro-table';
import { useState } from 'react';
import { api } from '../api';

interface ConfigItem { name: string; path: string; size: number; created_at?: string; updated_at?: string; }

const stripYaml = (name: string) => name.replace(/\.yaml$/, '');

export default function ConfigPanel({ env }: { env: string }) {
  const actionRef = useRef<ActionType>(undefined);
  const prefix = env === 'sim' ? 'trading_sim_' : 'trading_real_';
  const [viewOpen, setViewOpen] = useState(false);
  const [viewContent, setViewContent] = useState('');
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editContent, setEditContent] = useState('');
  const [createOpen, setCreateOpen] = useState(false);

  const openViewer = async (name: string) => {
    try { const d = await api.get(`/api/admin/experiments/configs/${name}`); setViewContent(d.content || ''); setViewOpen(true); }
    catch { message.error('Failed'); }
  };
  const openEditor = async (name: string) => {
    try { const d = await api.get(`/api/admin/experiments/configs/${name}`); setEditName(name); setEditContent(d.content || ''); setEditOpen(true); }
    catch { message.error('Failed'); }
  };
  const saveEditor = async () => {
    try { await api.put(`/api/admin/experiments/configs/${editName}`, { content: editContent }); message.success('Saved'); setEditOpen(false); actionRef.current?.reload(); }
    catch (e: any) { message.error(`Save failed: ${e.message}`); }
  };
  const deleteConfig = async (name: string) => {
    try { await api.del(`/api/admin/experiments/configs/${name}`); message.success(`Deleted`); actionRef.current?.reload(); }
    catch (e: any) { message.error(`Delete failed: ${e.message}`); }
  };

  const columns: ProColumns<ConfigItem>[] = [
    { title: '文件', dataIndex: 'name', key: 'name', width: 240, render: (_, r) => stripYaml(r.name) },
    { title: '大小', dataIndex: 'size', key: 'size', width: 100, render: (_, r) => `${(r.size / 1024).toFixed(1)} KB` },
    { title: '更新时间', dataIndex: 'updated_at', width: 160, render: (_, r) => r.updated_at?.slice(0, 19) || '-' },
    {
      title: '操作', key: 'actions', width: 160,
      render: (_, r) => (
        <Space>
          <Tooltip title="查看"><Button size="small" icon={<EyeOutlined />} onClick={() => openViewer(r.name)} /></Tooltip>
          <Tooltip title="编辑"><Button size="small" icon={<SettingOutlined />} onClick={() => openEditor(r.name)} /></Tooltip>
          <Popconfirm title="删除？" onConfirm={() => deleteConfig(r.name)} okButtonProps={{ danger: true }}>
            <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />} /></Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const title = env === 'sim' ? '模拟配置' : '实盘配置';

  return (
    <>
      <ProTable<ConfigItem>
        headerTitle={title}
        actionRef={actionRef}
        rowKey="name"
        search={false}
        columns={columns}
        pagination={{ pageSize: 20 }}
        request={async () => {
          const data = await api.get('/api/admin/experiments/configs');
          const filtered = (data || []).filter((c: any) => c.name.startsWith(prefix));
          return { data: filtered, success: true, total: filtered.length };
        }}
        toolBarRender={() => [
          <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建配置</Button>,
        ]}
      />
      <Modal title="新建配置" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={async () => {
          const nm = (document.getElementById('cfg-name') as HTMLInputElement)?.value || '';
          const el = document.getElementById('cfg-content') as HTMLTextAreaElement;
          if (!nm || !el) return;
          try { await api.put(`/api/admin/experiments/configs/${prefix}${nm}.yaml`, { content: el.value }); message.success('Created'); setCreateOpen(false); actionRef.current?.reload(); }
          catch (e: any) { message.error(`Failed: ${e.message}`); }
        }} width={700}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input id="cfg-name" placeholder="name" addonAfter=".yaml" />
          <Input.TextArea id="cfg-content" rows={20} placeholder="YAML content..." style={{ fontFamily: 'monospace', fontSize: 12 }} />
        </Space>
      </Modal>
      <Drawer title="编辑" open={editOpen} onClose={() => setEditOpen(false)} width={700}
        extra={<Popconfirm title="保存？" onConfirm={saveEditor}><Button type="primary">保存</Button></Popconfirm>}>
        <Input.TextArea value={editContent} onChange={e => setEditContent(e.target.value)} rows={30} style={{ fontFamily: 'monospace', fontSize: 12 }} />
      </Drawer>
      <Drawer title="查看" open={viewOpen} onClose={() => setViewOpen(false)} width={700}>
        <pre style={{ fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap', background: '#fafafa', padding: 16, borderRadius: 6 }}>
          {viewContent}
        </pre>
      </Drawer>
    </>
  );
}
