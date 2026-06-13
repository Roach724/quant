import { Tabs } from 'antd';
import { useState } from 'react';
import StrategyPanel from './StrategyPanel';

export default function TradingStrategies({ onJumpToDashboard }: { env: string; onJumpToDashboard: (id: number) => void }) {
  const [sub, setSub] = useState('sim');
  return (
    <Tabs activeKey={sub} onChange={setSub} items={[
      { key: 'sim', label: '模拟策略', children: <StrategyPanel env="sim" onJumpToDashboard={onJumpToDashboard} /> },
      { key: 'real', label: '实盘策略', children: <StrategyPanel env="real" onJumpToDashboard={onJumpToDashboard} /> },
    ]} />
  );
}
