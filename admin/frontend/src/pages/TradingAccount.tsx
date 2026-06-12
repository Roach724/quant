import { Tabs } from 'antd';
import { useState } from 'react';
import AccountPanel from './AccountPanel';

export default function TradingAccount() {
  const [sub, setSub] = useState('sim');
  return (
    <Tabs activeKey={sub} onChange={setSub} items={[
      { key: 'sim', label: '模拟账户', children: <AccountPanel env="sim" /> },
      { key: 'real', label: '真实账户', children: <AccountPanel env="real" /> },
    ]} />
  );
}
