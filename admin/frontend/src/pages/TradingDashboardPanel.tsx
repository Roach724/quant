import { Card, Empty } from 'antd';

export default function TradingDashboardPanel({ env }: { env: string }) {
  const label = env === 'sim' ? '模拟看板' : '实盘看板';
  return (
    <Card title={label}>
      <Empty description={`${label} — 待交易运行器接入后展示权益曲线和持仓`} />
    </Card>
  );
}
