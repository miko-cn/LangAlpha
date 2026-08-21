/**
 * Settings → Data Sources tab.
 *
 * Lists every market-data provider configured in `config.yaml`'s
 * `market_data.providers` chain, with availability, market coverage,
 * and rate-limit settings. Read-only — the chain lives on the server,
 * the UI just surfaces what's deployed.
 */
import { useTranslation } from 'react-i18next';
import { CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { useDataSourcesStatus } from '@/hooks/useDataSourcesStatus';
import type { DataSourceStatus } from '@/api/dataSources';

/** "cn" / "hk" / "us" / "all" / "non-us" — short labels for the chain order. */
const MARKET_LABELS: Record<string, string> = {
  cn: 'A 股',
  hk: '港股',
  us: '美股',
  all: '全部',
  'non-us': '非美',
};

function formatMarkets(markets: string[]): string {
  return markets.map((m) => MARKET_LABELS[m] ?? m).join(' / ');
}

/** Build a compact "capabilities" summary like "intraday • daily" from
 * the per-capability market lists; nothing means that capability is empty. */
function capabilityMarkets(
  intraday: string[] | null,
  daily: string[] | null,
  snapshot: string[] | null,
): { label: string; markets: string[] }[] {
  const rows: { label: string; markets: string[] }[] = [];
  if (intraday) rows.push({ label: 'intraday', markets: intraday });
  if (daily) rows.push({ label: 'daily', markets: daily });
  if (snapshot) rows.push({ label: 'snapshot', markets: snapshot });
  return rows;
}

function ProviderCard({ source }: { source: DataSourceStatus }) {
  const { t } = useTranslation();
  const caps = capabilityMarkets(
    source.intraday_markets,
    source.daily_markets,
    source.snapshot_markets,
  );

  return (
    <div
      className="p-4 rounded-lg"
      style={{
        backgroundColor: 'var(--color-bg-card)',
        border: '1px solid var(--color-border-muted)',
      }}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1 min-w-0">
          <div className="flex items-center gap-2">
            <span
              className="text-sm font-medium"
              style={{ color: 'var(--color-text-primary)' }}
            >
              {source.name}
            </span>
            {source.available ? (
              <span
                className="inline-flex items-center gap-1 text-xs"
                style={{ color: 'var(--color-profit)' }}
              >
                <CheckCircle2 className="h-3.5 w-3.5" />
                {t('settings.dataSources.available')}
              </span>
            ) : (
              <span
                className="inline-flex items-center gap-1 text-xs"
                style={{ color: 'var(--color-loss)' }}
              >
                <XCircle className="h-3.5 w-3.5" />
                {t('settings.dataSources.unavailable')}
              </span>
            )}
          </div>
          <p
            className="text-sm leading-relaxed"
            style={{ color: 'var(--color-text-secondary)' }}
          >
            {t('settings.dataSources.markets')}:{' '}
            <span style={{ color: 'var(--color-text-primary)' }}>
              {formatMarkets(source.markets)}
            </span>
          </p>
        </div>
        {source.rate_limit && (
          <span
            className="shrink-0 text-xs tabular-nums"
            style={{ color: 'var(--color-text-tertiary)' }}
          >
            {source.rate_limit.rate_per_sec.toFixed(1)} / s ·{' '}
            burst {source.rate_limit.burst}
          </span>
        )}
      </div>

      {caps.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {caps.map((c) => (
            <span
              key={c.label}
              style={{ color: 'var(--color-text-tertiary)' }}
            >
              {c.label}:{' '}
              <span style={{ color: 'var(--color-text-secondary)' }}>
                {formatMarkets(c.markets) || '—'}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function DataSourcesTab() {
  const { t } = useTranslation();
  const { data, isLoading, isError, error, refetch, isFetching } =
    useDataSourcesStatus();

  if (isLoading) {
    return (
      <div className="space-y-5 max-w-2xl">
        <p
          className="text-sm leading-relaxed"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          {t('common.loading')}
        </p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-5 max-w-2xl">
        <div
          className="p-3 rounded-md"
          style={{
            backgroundColor: 'var(--color-loss-soft)',
            border: '1px solid var(--color-border-loss)',
          }}
        >
          <p className="text-sm" style={{ color: 'var(--color-loss)' }}>
            {t('settings.dataSources.loadFailed', {
              detail: (error as Error)?.message ?? '',
            })}
          </p>
        </div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="space-y-5 max-w-2xl">
      <p
        className="text-sm leading-relaxed"
        style={{ color: 'var(--color-text-tertiary)' }}
      >
        {t('settings.dataSources.subtitle', {
          available: data.available,
          total: data.total,
        })}
      </p>

      <div className="space-y-3">
        {data.sources.map((source) => (
          <ProviderCard key={source.name} source={source} />
        ))}
      </div>

      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-xs font-medium transition-opacity hover:opacity-80 disabled:opacity-50"
          style={{ color: 'var(--color-accent-primary)' }}
        >
          {isFetching ? (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t('settings.dataSources.refreshing')}
            </span>
          ) : (
            t('settings.dataSources.refresh')
          )}
        </button>
        <p
          className="text-xs"
          style={{ color: 'var(--color-text-tertiary)' }}
        >
          {t('settings.dataSources.chainHint')}
        </p>
      </div>
    </div>
  );
}