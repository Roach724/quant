import {
  ReloadOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-layout';
import { Select, Input, Button, Switch, Tag, Space, Typography, DatePicker } from 'antd';
import { useEffect, useRef, useState, useCallback } from 'react';
import { api, WS_BASE } from '../api';

const { Search } = Input;
const { RangePicker } = DatePicker;
const { Text } = Typography;

const MAX_LINES = 500;

interface LogLine {
  ts: string;
  level: string;
  msg: string;
}

interface ModuleInfo {
  name: string;
  file_count: number;
}

const levelColors: Record<string, string> = {
  ERROR: 'red',
  WARNING: 'orange',
  WARN: 'orange',
  INFO: 'blue',
  DEBUG: 'default',
};

const levelOptions = [
  { value: '', label: 'ALL' },
  { value: 'ERROR', label: 'ERROR' },
  { value: 'WARNING', label: 'WARNING' },
  { value: 'INFO', label: 'INFO' },
  { value: 'DEBUG', label: 'DEBUG' },
];

const extractTime = (ts: string): string => {
  if (!ts) return '';
  // ts format: "2025-01-15T14:30:22.123456" or "2025-01-15 14:30:22"
  const m = ts.match(/[T ](\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : ts;
};

const LogViewer: React.FC = () => {
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

  // Keep linesRef in sync with state
  linesRef.current = lines;

  const scrollToBottom = useCallback(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, []);

  const fetchLogs = useCallback(async (mod: string, lvl: string, s: string, tr: [string, string] | null, f: string = '') => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ module: mod, lines: '100' });
      if (lvl) params.set('level', lvl);
      if (s) params.set('search', s);
      if (tr && tr[0]) params.set('start', tr[0]);
      if (tr && tr[1]) params.set('end', tr[1]);
      if (f) params.set('file', f);
      const data = await api.get(`/api/admin/logs?${params.toString()}`);
      if (!data.error) {
        setLines(data.lines || []);
        setFileName(data.file || null);
        if (data.files) setFileList(data.files);
      }
    } catch (err: any) {
      console.error('Failed to fetch logs:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load modules on mount
  useEffect(() => {
    api.get('/api/admin/logs/modules').then((data) => {
      if (Array.isArray(data)) {
        setModules(data);
        if (data.length > 0) {
          const defaultMod = data[0].name;
          setModule(defaultMod);
          fetchLogs(defaultMod, '', '', null);
        }
      }
    }).catch(console.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll when lines update (non-live mode)
  useEffect(() => {
    if (!live) scrollToBottom();
  }, [lines, live, scrollToBottom]);

  // Live WebSocket
  useEffect(() => {
    if (!live) {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      return;
    }

    const ws = new WebSocket(`${WS_BASE}/ws/logs?module=${module}`);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('Log WebSocket connected');
    };

    ws.onmessage = (event) => {
      try {
        const entry: LogLine = JSON.parse(event.data);
        const current = linesRef.current;
        if (current.length >= MAX_LINES) {
          setLines([...current.slice(current.length - MAX_LINES + 1), entry]);
        } else {
          setLines([...current, entry]);
        }
        // Auto-scroll in live mode
        setTimeout(scrollToBottom, 50);
      } catch {
        // ignore parse errors
      }
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
    };

    ws.onclose = () => {
      console.log('Log WebSocket closed');
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, module]);

  const handleRefresh = () => {
    fetchLogs(module, level, search, timeRange);
  };

  const handleSearch = (value: string) => {
    setSearch(value);
    fetchLogs(module, level, value, timeRange);
  };

  return (
    <PageContainer header={{ title: '日志浏览' }}>
      {/* Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 12,
          flexWrap: 'wrap',
        }}
      >
        <Select
          value={module}
          onChange={(val) => {
            setModule(val);
            setSelectedFile('');
            fetchLogs(val, level, search, timeRange);
          }}
          style={{ width: 140 }}
          options={modules.map((m) => ({
            value: m.name,
            label: `${m.name} (${m.file_count})`,
          }))}
        />
        {fileList.length > 1 && (
          <Select
            value={selectedFile}
            onChange={(val) => {
              setSelectedFile(val);
              fetchLogs(module, level, search, timeRange, val);
            }}
            allowClear
            placeholder="选择文件"
            style={{ width: 200 }}
            options={fileList.map((f) => ({ value: f, label: f }))}
          />
        )}
        <Select
          value={level}
          onChange={(val) => {
            setLevel(val);
            fetchLogs(module, val, search, timeRange);
          }}
          allowClear
          placeholder="Level"
          style={{ width: 100 }}
          options={levelOptions}
        />
        <Search
          placeholder="搜索..."
          allowClear
          onSearch={handleSearch}
          style={{ width: 200 }}
        />
        <RangePicker
          showTime
          onChange={(dates) => {
            if (dates && dates[0] && dates[1]) {
              const tr: [string, string] = [
                dates[0].toISOString(),
                dates[1].toISOString(),
              ];
              setTimeRange(tr);
              fetchLogs(module, level, search, tr);
            } else {
              setTimeRange(null);
              fetchLogs(module, level, search, null);
            }
          }}
          style={{ width: 360 }}
          placeholder={['开始时间', '结束时间']}
        />
        <Button
          icon={<ReloadOutlined />}
          onClick={handleRefresh}
          loading={loading}
        >
          刷新
        </Button>
        <Space>
          <Switch
            checked={live}
            onChange={(checked) => setLive(checked)}
            size="small"
          />
          <Text style={{ fontSize: 13 }}>实时</Text>
        </Space>
        {fileName && (
          <Text type="secondary" style={{ fontSize: 12, marginLeft: 'auto' }}>
            {fileName}
          </Text>
        )}
      </div>

      {/* Terminal log area */}
      <div
        ref={containerRef}
        style={{
          height: 'calc(100vh - 280px)',
          background: '#1e1e1e',
          color: '#d4d4d4',
          fontFamily: "'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace",
          fontSize: 12,
          padding: 12,
          borderRadius: 4,
          overflowY: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          lineHeight: '1.6',
        }}
      >
        {lines.length === 0 && !loading && (
          <div style={{ color: '#888' }}>暂无日志数据</div>
        )}
        {lines.map((line, i) => (
          <div key={i} style={{ display: 'flex', gap: 8 }}>
            <span style={{ color: '#888', flexShrink: 0, minWidth: 64 }}>
              {extractTime(line.ts)}
            </span>
            {line.level && (
              <Tag
                color={levelColors[line.level.toUpperCase()] || 'default'}
                style={{ margin: 0, lineHeight: '16px', fontSize: 11, flexShrink: 0 }}
              >
                {line.level}
              </Tag>
            )}
            <span style={{ color: '#d4d4d4' }}>{line.msg}</span>
          </div>
        ))}
      </div>
    </PageContainer>
  );
};

export default LogViewer;
