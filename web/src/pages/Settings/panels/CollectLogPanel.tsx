/**
 * Settings → Data Sources → collection log panel.
 *
 * Streams the provider-chain attempt log from `GET /api/v1/data-sources/logs`.
 * Each row is one source attempt for one capability/symbol: ok / empty /
 * error + latency, so a fallback chain (futu fails → tushare fills) is
 * visible at a glance. Polls every 5s while mounted.
 */
import { useTranslation } from 'react-i18next';
import { Activity, RefreshCw, AlertTriangle } from 'lucide-react';
import { useDataSourcesLogs } from '@/hooks/useDataSourcesStatus';
import type { CollectLogRow } from '@/api/dataSources';

const STATUS_STYLE: Record<string, { color: string; label: string }> = {
  ok: { color: 'var(--color-profit)', label: 'ok' },
  empty: { color: 'var(--color-text-tertiary)', label: 'empty' },
  error: { color: 'var(--color-loss)', label: 'error' },
};

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function Row({ row }: { row: CollectLogRow }) {
  const style = STATUS_STYLE[row.status] ?? STATUS_STYLE.empty;
  return (
    <div className="flex items-center gap-3 text-xs py-1 border-b last:border-0"
      style={{ borderColor: 'var(--color-border-muted)' }}>
      <span className="tabular-nums shrink-0"
        style={{ color: 'var(--color-text-tertiary)' }}>
        {formatTime(row.ts_ms)}
      </span>
      <span className="shrink-0 w-14" style={{ color: 'var(--color-text-secondary)' }}>
        {row.capability}
      </span>
      <span className="shrink-0 font-medium" style={{ color: 'var(--color-text-primary)' }}>
        {row.symbol}
      </span>
      <span className="shrink-0" style={{ color: 'var(--color-accent-primary)' }}>
        {row.source}
      </span>
      <span className="inline-flex items-center gap-1 shrink-0 font-medium"
        style={{ color: style.color }}>
        <span className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ backgroundColor: style.color }} />
        {style.label}
      </span>
      <span className="tabular-nums ml-auto shrink-0"
        style={{ color: 'var(--color-text-tertiary)' }}>
        {row.took_ms.toFixed(0)}ms
      </span>
    </div>
  );
}

export function CollectLogPanel() {
  const { t } = useTranslation();
  const { data, isLoading, isError, refetch, isFetching } =
    useDataSourcesLogs(200);

  return (
    <div
      className="rounded-lg"
      style={{
        backgroundColor: 'var(--color-bg-card)',
        border: '1px solid var(--color-border-muted)',
      }}
    >
      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <Activity className="h-3.5 w-3.5" style={{ color: 'var(--color-accent-primary)' }} />
          <span className="text-sm font-medium" style={{ color: 'var(--color-text-primary)' }}>
            {t('settings.dataSources.logTitle')}
          </span>
        </div>
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-xs font-medium transition-opacity hover:opacity-80 disabled:opacity-50"
          style={{ color: 'var(--color-accent-primary)' }}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <p className="px-4 pb-2 text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
        {t('settings.dataSources.logHint')}
      </p>

      <div className="px-4 pb-3">
        {isError && (
          <div className="flex items-center gap-2 text-xs"
            style={{ color: 'var(--color-loss)' }}>
            <AlertTriangle className="h-3.5 w-3.5" />
            {t('settings.dataSources.logLoadFailed')}
          </div>
        )}
        {!isError && isLoading && (
          <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
            {t('common.loading')}
          </p>
        )}
        {!isError && !isLoading && (!data?.rows || data.rows.length === 0) && (
          <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
            {t('settings.dataSources.logEmpty')}
          </p>
        )}
        {!isError && !isLoading && data && data.rows.length > 0 && (
          <div className="max-h-72 overflow-y-auto">
            {data.rows.map((row, i) => <Row key={`${row.ts_ms}-${i}`} row={row} />)}
          </div>
        )}
      </div>
    </div>
  );
}
