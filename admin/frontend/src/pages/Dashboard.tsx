import { Tabs } from 'antd';
import { useState } from 'react';
import DashboardLive from './DashboardLive';
import DashboardPaperRun from './DashboardPaperRun';

const TAB_ITEMS = [
  { key: 'live', label: 'Live' },
  { key: 'paper', label: 'Paper Run' },
];

const TAB_CONTENT: Record<string, React.ReactNode> = {
  live: <DashboardLive />,
  paper: <DashboardPaperRun />,
};

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState('live');

  return (
    <Tabs
      activeKey={activeTab}
      onChange={setActiveTab}
      items={TAB_ITEMS.map((tab) => ({
        key: tab.key,
        label: tab.label,
        children: TAB_CONTENT[tab.key],
      }))}
    />
  );
}
