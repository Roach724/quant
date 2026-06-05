import { Card, Row, Col, Tag, Spin, Statistic } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { useEffect, useState } from 'react';
import { api } from '../api';

export default function DashboardPipeline() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const d = await api.get('/api/admin/dashboard/pipeline');
        setData(d);
      } catch (e) { console.error('pipeline load failed', e); }
      finally { setLoading(false); }
    })();
  }, []);

  const usLatest = data?.us ? new Date(data.us) : null;
  const hkLatest = data?.hk ? new Date(data.hk) : null;
  const checkedAt = data?.ts ? new Date(data.ts) : new Date();

  const isFresh = (d: Date | null) => {
    if (!d) return false;
    return (checkedAt.getTime() - d.getTime()) < 24 * 3600 * 1000;
  };

  return (
    <div>
      <div style={{ marginBottom: 16, fontWeight: 600, fontSize: 16 }}>Data Pipeline Health</div>
      <Spin spinning={loading}>
        <Row gutter={[16, 16]}>
          {/* US */}
          <Col xs={24} md={12}>
            <Card
              size="small"
              title="🇺🇸 US Market"
              extra={data ? (
                <Tag icon={data.us_open ? <CheckCircleOutlined /> : <CloseCircleOutlined />} color={data.us_open ? 'green' : 'default'}>
                  {data.us_open ? 'Open' : 'Closed'}
                </Tag>
              ) : null}
            >
              <Statistic
                title="Latest 5m Bar"
                value={usLatest ? usLatest.toLocaleString() : '—'}
                valueStyle={{ fontSize: 16 }}
              />
              <div style={{ marginTop: 8 }}>
                <Tag icon={isFresh(usLatest) ? <CheckCircleOutlined /> : <ClockCircleOutlined />} color={isFresh(usLatest) ? 'green' : 'orange'}>
                  {isFresh(usLatest) ? 'Fresh (<24h)' : 'Stale'}
                </Tag>
              </div>
            </Card>
          </Col>

          {/* HK */}
          <Col xs={24} md={12}>
            <Card
              size="small"
              title="🇭🇰 HK Market"
              extra={data ? (
                <Tag icon={data.hk_open ? <CheckCircleOutlined /> : <CloseCircleOutlined />} color={data.hk_open ? 'green' : 'default'}>
                  {data.hk_open ? 'Open' : 'Closed'}
                </Tag>
              ) : null}
            >
              <Statistic
                title="Latest 5m Bar"
                value={hkLatest ? hkLatest.toLocaleString() : '—'}
                valueStyle={{ fontSize: 16 }}
              />
              <div style={{ marginTop: 8 }}>
                <Tag icon={isFresh(hkLatest) ? <CheckCircleOutlined /> : <ClockCircleOutlined />} color={isFresh(hkLatest) ? 'green' : 'orange'}>
                  {isFresh(hkLatest) ? 'Fresh (<24h)' : 'Stale'}
                </Tag>
              </div>
            </Card>
          </Col>
        </Row>

        <div style={{ marginTop: 16, color: '#999', fontSize: 12, textAlign: 'center' }}>
          Last checked: {checkedAt.toLocaleString()}
        </div>
      </Spin>
    </div>
  );
}
