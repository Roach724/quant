import {
  ExperimentOutlined,
  CloudServerOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  DashboardOutlined,
  LineChartOutlined,
  DollarOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import ProLayout from '@ant-design/pro-layout';
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { ConfigProvider, Tag, Tooltip } from 'antd';
import './App.css';
import MarketCenter from './pages/MarketCenter';
import TradingCenter from './pages/TradingCenter';
import Dashboard from './pages/Dashboard';
import ExperimentDashboard from './pages/Experiments';
import DataMap from './pages/DataMap';
import LogViewer from './pages/LogViewer';
import CronJobs from './pages/CronJobs';
import Models from './pages/Models';
import CacheManager from './pages/CacheManager';
import { api } from './api';

const menuData = [
  { path: '/market', name: '行情中心', icon: <LineChartOutlined /> },
  { path: '/trade', name: '交易中心', icon: <DollarOutlined /> },
  { path: '/board', name: '实验看板', icon: <DashboardOutlined /> },
  { path: '/lab', name: '实验管理', icon: <ExperimentOutlined /> },
  { path: '/models', name: '模型 & 策略', icon: <DashboardOutlined /> },
  { path: '/data', name: '数据中心', icon: <CloudServerOutlined /> },
  { path: '/logs', name: '日志中心', icon: <FileTextOutlined /> },
  { path: '/cron', name: '调度中心', icon: <ClockCircleOutlined /> },
  { path: '/cache', name: '缓存管理', icon: <DatabaseOutlined /> },
];

function SystemMonitor() {
  const [status, setStatus] = useState({ ws: '?', cpu: '?', mem: '?', mem_total: '?' });
  useEffect(() => {
    const fetch = () => api.get('/api/admin/system/status').then(setStatus).catch(() => {});
    fetch();
    const i = setInterval(fetch, 15000);
    return () => clearInterval(i);
  }, []);
  const wsColor = status.ws === 'running' ? 'green' : status.ws === '?' ? 'default' : 'red';
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'center', fontSize: 12, color: '#888', paddingRight: 8 }}>
      <Tooltip title="ws_collector 状态"><Tag color={wsColor} style={{ margin: 0, fontSize: 11 }}>WS {status.ws === 'running' ? '●' : status.ws === '?' ? '?' : '✕'}</Tag></Tooltip>
      <span>CPU {status.cpu}%</span>
      <span>MEM {status.mem}G/{status.mem_total}G</span>
    </div>
  );
}

const THEME = {
  token: {
    colorPrimary: '#1677ff',
    borderRadius: 8,
    fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    colorBgLayout: '#f5f5f5',
    colorBgContainer: '#ffffff',
    colorBorderSecondary: '#f0f0f0',
  },
};

function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [pathname, setPathname] = useState(location.pathname || '/market');

  return (
    <ConfigProvider theme={THEME}>
    <ProLayout
      title="Quant Admin"
      logo={null}
      location={{ pathname }}
      menuDataRender={() => menuData}
      siderWidth={200}
      contentStyle={{ padding: '8px 12px', margin: 0, maxWidth: '100%' }}
      layout="mix"
      actionsRender={() => [<SystemMonitor key="sys" />]}
      menuItemRender={(item, dom) => (
        <a
          onClick={() => {
            setPathname(item.path || '/market');
            navigate(item.path || '/market');
          }}
        >
          {dom}
        </a>
      )}
    >
      <Routes>
        <Route path="/market" element={<MarketCenter />} />
        <Route path="/trade" element={<TradingCenter />} />
        <Route path="/board" element={<Dashboard />} />
        <Route path="/lab" element={<ExperimentDashboard />} />
        <Route path="/models" element={<Models />} />
        <Route path="/data" element={<DataMap />} />
        <Route path="/logs" element={<LogViewer />} />
        <Route path="/cron" element={<CronJobs />} />
        <Route path="/cache" element={<CacheManager />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/experiments" element={<ExperimentDashboard />} />
        <Route path="/" element={<MarketCenter />} />
      </Routes>
    </ProLayout>
    </ConfigProvider>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AppLayout />
    </BrowserRouter>
  );
}

export default App;
