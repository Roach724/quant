import {
  ExperimentOutlined,
  CloudServerOutlined,
  FileTextOutlined,
  ClockCircleOutlined,
  DashboardOutlined,
  FunctionOutlined,
} from '@ant-design/icons';
import ProLayout, { PageContainer } from '@ant-design/pro-layout';
import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { useState } from 'react';
import './App.css';
import Experiments from './pages/Experiments';

const menuData = [
  { path: '/experiments', name: '实验管理', icon: <ExperimentOutlined /> },
  { path: '/data', name: '数据采集', icon: <CloudServerOutlined /> },
  { path: '/logs', name: '日志浏览', icon: <FileTextOutlined /> },
  { path: '/cron', name: 'Cron 任务', icon: <ClockCircleOutlined /> },
  { path: '/models', name: '模型 & 策略', icon: <DashboardOutlined /> },
  { path: '/factors', name: '因子管理', icon: <FunctionOutlined /> },
];

const PlaceholderPage = ({ title }: { title: string }) => (
  <PageContainer header={{ title }}>
    <div style={{ minHeight: 300 }}>{title}</div>
  </PageContainer>
);

function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [pathname, setPathname] = useState(location.pathname || '/experiments');

  return (
    <ProLayout
      title="Quant Admin"
      logo={null}
      location={{ pathname }}
      menuDataRender={() => menuData}
      menuItemRender={(item, dom) => (
        <a
          onClick={() => {
            setPathname(item.path || '/experiments');
            navigate(item.path || '/experiments');
          }}
        >
          {dom}
        </a>
      )}
    >
      <Routes>
        <Route path="/experiments" element={<Experiments />} />
        <Route path="/data" element={<PlaceholderPage title="数据采集" />} />
        <Route path="/logs" element={<PlaceholderPage title="日志浏览" />} />
        <Route path="/cron" element={<PlaceholderPage title="Cron 任务" />} />
        <Route path="/models" element={<PlaceholderPage title="模型 & 策略" />} />
        <Route path="/factors" element={<PlaceholderPage title="因子管理" />} />
        <Route path="/" element={<Experiments />} />
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
