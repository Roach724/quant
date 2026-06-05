import {
  ReloadOutlined, DeleteOutlined, EyeOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-layout';
import { Select, Input, Button, Switch, Tag, Space, Typography, DatePicker, Tabs, Table, Popconfirm, Modal, Tooltip, message } from 'antd';
import { useEffect, useRef, useState, useCallback } from 'react';
import { api, WS_BASE } from '../api';

const { Search } = Input;
const { RangePicker } = DatePicker;
const { Text } = Typography;

const MAX_LINES = 500;

interface LogLine { ts: string; level: string; msg: string; }

interface ModuleInfo { name: string; file_count: number; }

interface LogFileInfo { name: string; path: string; size: number; mtime: string; }

const levelColors: Record<string, string> = { ERROR: 'red', WARNING: 'orange', WARN: 'orange', INFO: 'blue', DEBUG: 'default' };
const levelOptions = ['', 'ERROR', 'WARNING', 'INFO', 'DEBUG'].map(v => ({ value: v, label: v || 'ALL' }));
const extractTime = (ts: string): string => { if (!ts) return ''; const m = ts.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/); return m ? `${m[1]} ${m[2]}` : ts; };

// ═══════════════════════════════════════════════════════════════════════════════
// LogBrowser — existing log viewer
// ═══════════════════════════════════════════════════════════════════════════════

const LogBrowser: React.FC = () => {
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const [module, setModule] = useState<string>('collector');
  const [level, setLevel] = useState<string>('');
  const [search, setSearch] = useState<string>('');
  const [lines, setLines] = useState<LogLine[]>([]);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileList, setFileList] = useState<string[]>([]);
  const [selectedFile, setSelectedFile] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [live, setLive] = useState(false);
  const [timeRange, setTimeRange] = useState<[string, string] | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const linesRef = useRef<LogLine[]>([]);
  linesRef.current = lines;
  const scrollToBottom = useCallback(() => { if (containerRef.current) containerRef.current.scrollTop = containerRef.current.scrollHeight; }, []);

  const fetchLogs = useCallback(async (mod: string, lvl: string, s: string, tr: [string, string] | null, f: string = '') => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ module: mod, lines: '100' });
      if (lvl) params.set('level', lvl); if (s) params.set('search', s);
      if (tr && tr[0]) params.set('start', tr[0]); if (tr && tr[1]) params.set('end', tr[1]);
      if (f) params.set('file', f);
      const data = await api.get(`/api/admin/logs?${params.toString()}`);
      if (!data.error) { setLines(data.lines || []); setFileName(data.file || null); if (data.files) setFileList(data.files); }
    } catch { } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    api.get('/api/admin/logs/modules').then((data) => {
      if (Array.isArray(data)) { setModules(data); if (data.length > 0) { setModule(data[0].name); fetchLogs(data[0].name, '', '', null); } }
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { if (!live) scrollToBottom(); }, [lines, live, scrollToBottom]);

  useEffect(() => {
    if (!live) { if (wsRef.current) { wsRef.current.close(); wsRef.current = null; } return; }
    const ws = new WebSocket(`${WS_BASE}/ws/logs?module=${module}`);
    wsRef.current = ws;
    ws.onmessage = (event) => {
      try {
        const entry: LogLine = JSON.parse(event.data);
        const current = linesRef.current;
        setLines(current.length >= MAX_LINES ? [...current.slice(current.length - MAX_LINES + 1), entry] : [...current, entry]);
        setTimeout(scrollToBottom, 50);
      } catch { }
    };
    return () => { ws.close(); wsRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, module]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
        <Select value={module} onChange={(val) => { setModule(val); setSelectedFile(''); fetchLogs(val, level, search, timeRange); }}
          style={{ width: 140 }} options={modules.map(m => ({ value: m.name, label: `${m.name} (${m.file_count})` }))} />
        {fileList.length > 1 && (
          <Select value={selectedFile} onChange={(val) => { setSelectedFile(val); fetchLogs(module, level, search, timeRange, val); }}
            allowClear placeholder="选择文件" style={{ width: 200 }} options={fileList.map(f => ({ value: f, label: f }))} />
        )}
        <Select value={level} onChange={(val) => { setLevel(val); fetchLogs(module, val, search, timeRange); }} allowClear placeholder="Level" style={{ width: 100 }} options={levelOptions} />
        <Search placeholder="搜索..." allowClear onSearch={(value) => { setSearch(value); fetchLogs(module, level, value, timeRange); }} style={{ width: 200 }} />
        <RangePicker showTime onChange={(dates) => {
          if (dates && dates[0] && dates[1]) { const tr: [string, string] = [dates[0].toISOString(), dates[1].toISOString()]; setTimeRange(tr); fetchLogs(module, level, search, tr); }
          else { setTimeRange(null); fetchLogs(module, level, search, null); }
        }} style={{ width: 360 }} placeholder={['开始时间', '结束时间']} />
        <Button icon={<ReloadOutlined />} onClick={() => fetchLogs(module, level, search, timeRange)} loading={loading}>刷新</Button>
        <Space><Switch checked={live} onChange={setLive} /><Text style={{ fontSize: 12 }}>Live</Text></Space>
        {fileName && <Text type="secondary" style={{ fontSize: 11 }}>{fileName}</Text>}
      </div>
      <div ref={containerRef} style={{ height: 'calc(100vh - 250px)', overflow: 'auto', background: '#1e1e1e', borderRadius: 6, padding: 12, fontFamily: 'monospace', fontSize: 12, lineHeight: 1.6, textAlign: 'left' }}>
        {lines.map((line, i) => (
          <div key={i} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            <span style={{ color: '#888' }}>{extractTime(line.ts)} </span>
            <Tag color={levelColors[line.level] || 'default'} style={{ fontSize: 10, lineHeight: '16px', marginRight: 4 }}>{line.level}</Tag>
            <span style={{ color: '#e0e0e0' }}>{line.msg}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// LogManager — list + delete log files
// ═══════════════════════════════════════════════════════════════════════════════

const LogManager: React.FC = () => {
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const [module, setModule] = useState('collector');
  const [files, setFiles] = useState<LogFileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewName, setPreviewName] = useState('');
  const [previewLines, setPreviewLines] = useState<LogLine[]>([]);

  const loadModules = async () => {
    const data = await api.get('/api/admin/logs/modules');
    if (Array.isArray(data)) setModules(data);
  };

  const loadFiles = async (mod: string) => {
    setLoading(true);
    try { const data = await api.get(`/api/admin/logs/files?module=${mod}`); setFiles(data || []); } catch { }
    finally { setLoading(false); }
  };

  useEffect(() => { loadModules(); }, []);
  useEffect(() => { loadFiles(module); }, [module]);

  const handleDelete = async (fileName: string) => {
    try {
      await api.del(`/api/admin/logs/files?module=${module}&file=${encodeURIComponent(fileName)}`);
      message.success(`Deleted ${fileName}`);
      loadFiles(module);
    } catch (e: any) { message.error(`Delete failed: ${e.message}`); }
  };

  const handlePreview = async (fileName: string) => {
    setPreviewName(fileName); setPreviewOpen(true); setPreviewLines([]);
    try {
      const data = await api.get(`/api/admin/logs?module=${module}&lines=50&file=${encodeURIComponent(fileName)}`);
      setPreviewLines(data.lines || []);
    } catch { }
  };

  const formatSize = (b: number) => b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)} MB` : b > 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b} B`;

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Text strong>模块:</Text>
        <Select value={module} onChange={setModule} style={{ width: 140 }}
          options={modules.map(m => ({ value: m.name, label: `${m.name} (${m.file_count})` }))} />
        <Button icon={<ReloadOutlined />} onClick={() => loadFiles(module)}>刷新</Button>
      </Space>
      <Table<LogFileInfo> dataSource={files} rowKey="name" loading={loading} size="small"
        pagination={false}
        columns={[
          { title: '文件名', dataIndex: 'name', key: 'name', width: 280, ellipsis: true },
          { title: '大小', dataIndex: 'size', key: 'size', width: 100, render: (_, r) => formatSize(r.size) },
          { title: '修改时间', dataIndex: 'mtime', key: 'mtime', width: 180, render: (_, r) => r.mtime?.slice(0, 19) || '-' },
          {
            title: '操作', key: 'actions', width: 120,
            render: (_, r) => (
              <Space>
                <Tooltip title="查看"><Button size="small" icon={<EyeOutlined />} onClick={() => handlePreview(r.name)} /></Tooltip>
                <Popconfirm title={`删除 ${r.name}？`}
                  description="运行中的服务不会崩溃，但日志将丢失" onConfirm={() => handleDelete(r.name)}
                  okText="确认删除" okButtonProps={{ danger: true }}>
                  <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />} /></Tooltip>
                </Popconfirm>
              </Space>
            ),
          },
        ]} />
      <Modal title={`预览: ${previewName}`} open={previewOpen} onCancel={() => setPreviewOpen(false)} footer={null} width={900}>
        <div style={{ maxHeight: 500, overflow: 'auto', background: '#1e1e1e', borderRadius: 6, padding: 12, fontFamily: 'monospace', fontSize: 12, lineHeight: 1.6, textAlign: 'left' }}>
          {previewLines.map((line, i) => (
            <div key={i} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              <span style={{ color: '#888' }}>{extractTime(line.ts)} </span>
              <Tag color={levelColors[line.level] || 'default'} style={{ fontSize: 10, lineHeight: '16px', marginRight: 4 }}>{line.level}</Tag>
              <span style={{ color: '#e0e0e0' }}>{line.msg}</span>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
// LogViewer — parent with two tabs
// ═══════════════════════════════════════════════════════════════════════════════

const LogViewer: React.FC = () => {
  const [tab, setTab] = useState('browse');
  return (
    <PageContainer header={{ title: '日志' }}>
      <Tabs activeKey={tab} onChange={setTab} items={[
        { key: 'browse', label: '日志浏览', children: <LogBrowser /> },
        { key: 'manage', label: '日志管理', children: <LogManager /> },
      ]} />
    </PageContainer>
  );
};

export default LogViewer;
