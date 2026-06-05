import {
  ExperimentOutlined,
  CloudServerOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  DashboardOutlined,
} from '@ant-design/icons';
import ProLayout from '@ant-design/pro-layout';
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { useState } from 'react';
import './App.css';
import Dashboard from './pages/Dashboard';
import ExperimentDashboard from './pages/Experiments';
import DataMap from './pages/DataMap';
import LogViewer from './pages/LogViewer';
import CronJobs from './pages/CronJobs';
import Models from './pages/Models';

const menuData = [
  { path: '/dashboard', name: 'Dashboard', icon: <DashboardOutlined /> },
  { path: '/experiments', name: '实验管理', icon: <ExperimentOutlined /> },
  { path: '/data', name: '数据采集', icon: <CloudServerOutlined /> },
  { path: '/logs', name: '日志浏览', icon: <FileTextOutlined /> },
  { path: '/cron', name: 'Cron 任务', icon: <ClockCircleOutlined /> },
  { path: '/models', name: '模型 & 策略', icon: <DashboardOutlined /> },
];

function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [pathname, setPathname] = useState(location.pathname || '/dashboard');

  return (
    <ProLayout
      title="Quant Admin"
      logo={null}
      location={{ pathname }}
      menuDataRender={() => menuData}
      siderWidth={180}
      contentStyle={{ padding: '8px 12px', margin: 0, maxWidth: '100%' }}
      layout="mix"
      menuItemRender={(item, dom) => (
        <a
          onClick={() => {
            setPathname(item.path || '/dashboard');
            navigate(item.path || '/dashboard');
          }}
        >
          {dom}
        </a>
      )}
    >
      <Routes>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/experiments" element={<ExperimentDashboard />} />
        <Route path="/data" element={<DataMap />} />
        <Route path="/logs" element={<LogViewer />} />
        <Route path="/cron" element={<CronJobs />} />
        <Route path="/models" element={<Models />} />
        <Route path="/" element={<Dashboard />} />
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
