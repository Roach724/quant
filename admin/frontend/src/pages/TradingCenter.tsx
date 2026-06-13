import { Tabs } from 'antd';
import { useState } from 'react';
import TradingDashboard from './TradingDashboard';
import TradingStrategies from './TradingStrategies';
import TradingConfig from './TradingConfig';
import TradingAccount from './TradingAccount';

export default function TradingCenter() {
  const [tab, setTab] = useState('dashboard');
  const [selectedStrategyId, setSelectedStrategyId] = useState<number | undefined>();

  return (
    <Tabs activeKey={tab} onChange={setTab} items={[
      { key: 'dashboard', label: '量化看板', children: <TradingDashboard env="sim" preSelectedId={selectedStrategyId} /> },
      { key: 'strategies', label: '量化策略', children: <TradingStrategies env="sim" onJumpToDashboard={(id) => { setSelectedStrategyId(id); setTab('dashboard'); }} /> },
      { key: 'config', label: '量化配置', children: <TradingConfig /> },
      { key: 'account', label: '交易账户', children: <TradingAccount /> },
    ]} />
  );
}
