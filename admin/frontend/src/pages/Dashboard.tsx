import { Tabs } from 'antd';
import { useState } from 'react';
import DashboardOverview from './DashboardOverview';
import DashboardLive from './DashboardLive';
import DashboardPaperRun from './DashboardPaperRun';
import DashboardProd from './DashboardProd';
import DashboardDebug from './DashboardDebug';
import DashboardPipeline from './DashboardPipeline';

const TAB_ITEMS = [
  { key: 'overview', label: 'Overview' },
  { key: 'live', label: 'Live' },
  { key: 'paper', label: 'Paper Run' },
  { key: 'prod', label: 'Prod' },
  { key: 'debug', label: 'Debug' },
  { key: 'pipeline', label: 'Pipeline' },
  { key: 'alerts', label: 'Alerts' },
];

const TAB_CONTENT: Record<string, React.ReactNode> = {
  overview: <DashboardOverview />,
  live: <DashboardLive />,
  paper: <DashboardPaperRun />,
  prod: <DashboardProd />,
  debug: <DashboardDebug />,
  pipeline: <DashboardPipeline />,
  alerts: <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>Alerts — coming soon</div>,
};

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState('overview');

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
