/**
 * Data-source status endpoint — returns the configured market-data provider
 * chain with availability, market coverage, and rate-limit settings. Used by
 * the Settings → Data Sources tab.
 *
 * Read-only and unauthenticated (the status endpoint does not require a
 * session), but goes through the shared axios instance to inherit any base
 * URL/proxy config.
 */
import { api } from '@/api/client';

export interface DataSourceRateLimit {
  rate_per_sec: number;
  burst: number;
}

export interface DataSourceStatus {
  name: string;
  available: boolean;
  markets: string[];
  intraday_markets: string[] | null;
  daily_markets: string[] | null;
  snapshot_markets: string[] | null;
  rate_limit: DataSourceRateLimit | null;
}

export interface DataSourcesStatusResponse {
  sources: DataSourceStatus[];
  total: number;
  available: number;
}

export async function getDataSourcesStatus(): Promise<DataSourcesStatusResponse> {
  const { data } = await api.get<DataSourcesStatusResponse>(
    '/api/v1/data-sources/status'
  );
  return data;
}

export interface CollectLogRow {
  ts_ms: number;
  capability: string;
  symbol: string;
  source: string;
  status: string; // 'ok' | 'empty' | 'error'
  took_ms: number;
  detail?: string;
}

export interface CollectLogResponse {
  rows: CollectLogRow[];
  total: number;
}

export async function getDataSourcesLogs(
  limit = 200
): Promise<CollectLogResponse> {
  const { data } = await api.get<CollectLogResponse>(
    '/api/v1/data-sources/logs',
    { params: { limit } }
  );
  return data;
}