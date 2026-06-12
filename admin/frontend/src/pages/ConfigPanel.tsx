import { Card, Empty } from 'antd';

export default function ConfigPanel({ env }: { env: string }) {
  const label = env === 'sim' ? '模拟配置' : '实盘配置';
  return (
    <Card title={label}>
      <Empty description={`${label} — 待实现 YAML 模板管理`} />
    </Card>
  );
}
