import { Button, Tooltip } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { api } from '../api';

interface Props {
  /** Cache module name, e.g. "dashboard:experiments" */
  module: string;
  /** Optional params passed to cache/refresh */
  params?: Record<string, any>;
  /** Optional label for the button tooltip */
  label?: string;
  /** Called after refresh completes, receives fresh data */
  onRefresh?: (data: any) => void;
  /** Optional: do a full refresh (invalidate + warm) instead of just invalidate */
  warmup?: boolean;
}

/**
 * Reusable refresh button that invalidates a specific cache module
 * and optionally warms it up.  Embed in any data panel header.
 */
export default function CacheRefresh({ module: moduleName, params, label, onRefresh, warmup = true }: Props) {
  const [loading, setLoading] = useState(false);

  const handleClick = async () => {
    setLoading(true);
    try {
      if (warmup) {
        const res = await api.post('/api/admin/cache/refresh', {
          module: moduleName,
          params: params || {},
        });
        if (onRefresh && res?.data) {
          onRefresh(res.data);
        }
      } else {
        await api.post('/api/admin/cache/invalidate', { module: moduleName });
        if (onRefresh) onRefresh(null);
      }
    } catch {
      // Silent fail — cache miss will re-fetch on next data load
    }
    setLoading(false);
  };

  return (
    <Tooltip title={label || `刷新缓存: ${moduleName}`}>
      <Button
        size="small"
        type="text"
        icon={<ReloadOutlined spin={loading} />}
        loading={loading}
        onClick={handleClick}
      />
    </Tooltip>
  );
}
