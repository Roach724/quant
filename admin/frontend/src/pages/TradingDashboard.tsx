import { Tabs } from 'antd';
import { useState } from 'react';
import TradingDashboardPanel from './TradingDashboardPanel';

export default function TradingDashboard() {
  const [sub, setSub] = useState('sim');
  return (
    <Tabs activeKey={sub} onChange={setSub} items={[
      { key: 'sim', label: '模拟看板', children: <TradingDashboardPanel env="sim" /> },
      { key: 'real', label: '实盘看板', children: <TradingDashboardPanel env="real" /> },
    ]} />
  );
}
