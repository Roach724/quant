import { Tabs } from 'antd';
import { useState } from 'react';
import ConfigPanel from './ConfigPanel';

export default function TradingConfig() {
  const [sub, setSub] = useState('sim');
  return (
    <Tabs activeKey={sub} onChange={setSub} items={[
      { key: 'sim', label: '模拟配置', children: <ConfigPanel env="sim" /> },
      { key: 'real', label: '实盘配置', children: <ConfigPanel env="real" /> },
    ]} />
  );
}
