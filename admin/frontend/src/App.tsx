import {
  ExperimentOutlined,
  CloudServerOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  DashboardOutlined,
  LineChartOutlined,
  DollarOutlined,
} from '@ant-design/icons';
import ProLayout from '@ant-design/pro-layout';
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { useState } from 'react';
import './App.css';
import MarketCenter from './pages/MarketCenter';
import TradingCenter from './pages/TradingCenter';
import Dashboard from './pages/Dashboard';
import ExperimentDashboard from './pages/Experiments';
import DataMap from './pages/DataMap';
import LogViewer from './pages/LogViewer';
import CronJobs from './pages/CronJobs';
import Models from './pages/Models';

const menuData = [
  { path: '/market', name: '行情中心', icon: <LineChartOutlined /> },
  { path: '/trade', name: '交易中心', icon: <DollarOutlined /> },
  { path: '/board', name: '实验看板', icon: <DashboardOutlined /> },
  { path: '/lab', name: '实验管理', icon: <ExperimentOutlined /> },
  { path: '/models', name: '模型 & 策略', icon: <DashboardOutlined /> },
  { path: '/data', name: '数据中心', icon: <CloudServerOutlined /> },
  { path: '/logs', name: '日志中心', icon: <FileTextOutlined /> },
  { path: '/cron', name: '调度中心', icon: <ClockCircleOutlined /> },
];

function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [pathname, setPathname] = useState(location.pathname || '/market');

  return (
    <ProLayout
      title="Quant Admin"
      logo={null}
      location={{ pathname }}
      menuDataRender={() => menuData}
      contentStyle={{ padding: '4px 6px', margin: 0, maxWidth: '100%' }}
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
        {/* Backward compat */}
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/experiments" element={<ExperimentDashboard />} />
        <Route path="/" element={<MarketCenter />} />
      </Routes>
    </ProLayout>
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
