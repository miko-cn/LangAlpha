/**
 * Read the configured market-data provider chain from the backend
 * status endpoint. Read-only and rarely changes (only on deploy +
 * config edit), so cache aggressively.
 */
import { useQuery } from '@tanstack/react-query';
import {
  getDataSourcesLogs,
  getDataSourcesStatus,
  type CollectLogResponse,
  type DataSourcesStatusResponse,
} from '@/api/dataSources';
import { queryKeys } from '@/lib/queryKeys';

const STALE_TIME = 5 * 60 * 1000; // 5 minutes

export function useDataSourcesStatus() {
  return useQuery<DataSourcesStatusResponse>({
    queryKey: queryKeys.dataSources.status(),
    queryFn: getDataSourcesStatus,
    staleTime: STALE_TIME,
  });
}

/** Recent collection attempts — polled so the log panel stays live. */
export function useDataSourcesLogs(limit = 200) {
  return useQuery<CollectLogResponse>({
    queryKey: queryKeys.dataSources.logs(limit),
    queryFn: () => getDataSourcesLogs(limit),
    refetchInterval: 5_000,
  });
}