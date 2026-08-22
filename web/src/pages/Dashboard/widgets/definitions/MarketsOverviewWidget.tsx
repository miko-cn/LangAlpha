import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { LineChart } from 'lucide-react';
import { useQuotes } from '@/lib/quotes';
import type { IndexData, SparklinePoint } from '@/types/market';
import IndexMovementCard from '../../components/IndexMovementCard';
import { buildIndexData, getIndex, normalizeIndexSymbol } from '../../utils/api';
import { registerWidget } from '../framework/WidgetRegistry';
import { useWidgetContextExport } from '../framework/contextSnapshot';
import {
  serializeRowsToMarkdown,
  wrapWidgetContext,
} from '../framework/snapshotSerializers';
import { MarketsOverviewConfigSchema } from '../framework/configSchemas';
import { EnumField } from '../framework/settings/EnumField';
import { SettingsDoneButton } from '../framework/settings/SettingsDoneButton';
import type { WidgetRenderProps, WidgetSettingsProps } from '../types';
import {
  overviewTitleKey,
  resolveOverviewSymbols,
  type MarketsOverviewConfig,
} from './marketsOverviewBaskets';

// Below this cell width the 5-tile desktop grid wraps to ≥3 rows and looks
// worse than the mobile swipe stack, so switch to the compact variant.
const COMPACT_WIDTH_PX = 640;

function useOverviewIndices(symbols: string[]): { indices: IndexData[]; loading: boolean } {
  const { quotes, isLoading } = useQuotes(symbols, {
    isIndex: true,
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const { data: sparklineMap = {} } = useQuery<
    Record<string, { sparklineData: SparklinePoint[]; asOfDate?: string }>
  >({
    queryKey: ['dashboard', 'indexSparklines', symbols],
    queryFn: async () => {
      const entries = await Promise.all(symbols.map(async (s) => {
        const norm = normalizeIndexSymbol(s);
        try {
          const r = await getIndex(norm);
          return [norm, { sparklineData: r.sparklineData, asOfDate: r.asOfDate }] as const;
        } catch {
          return [norm, { sparklineData: [] as SparklinePoint[], asOfDate: undefined }] as const;
        }
      }));
      return Object.fromEntries(entries);
    },
    staleTime: 10_000,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });

  const indices = useMemo<IndexData[]>(
    () => symbols.map((s) => {
      const norm = normalizeIndexSymbol(s);
      const sp = sparklineMap[norm];
      return buildIndexData(norm, quotes[norm], sp?.sparklineData ?? [], sp?.asOfDate);
    }),
    [symbols, quotes, sparklineMap],
  );

  return { indices, loading: isLoading };
}

function MarketsOverviewWidget({ instance }: WidgetRenderProps<MarketsOverviewConfig>) {
  const { t } = useTranslation();
  const symbols = useMemo(() => resolveOverviewSymbols(instance.config), [instance.config]);
  const { indices, loading } = useOverviewIndices(symbols);

  useWidgetContextExport(instance.id, {
    full: () => {
      const rows = indices.map((idx) => ({
        symbol: idx.symbol,
        name: idx.name,
        price: idx.price,
        change: idx.change,
        changePercent: idx.changePercent,
      }));
      const fmtNum = (v: unknown) => (typeof v === 'number' ? v.toFixed(2) : '');
      const fmtPct = (v: unknown) => (typeof v === 'number' ? `${v.toFixed(2)}%` : '');
      const body = rows.length
        ? serializeRowsToMarkdown(rows, [
            { key: 'symbol', label: 'symbol' },
            { key: 'name', label: 'name' },
            { key: 'price', label: 'price', format: fmtNum },
            { key: 'change', label: 'change', format: fmtNum },
            { key: 'changePercent', label: 'change%', format: fmtPct },
          ])
        : '_no indices_';
      const text = wrapWidgetContext('markets.overview', { count: rows.length }, body);
      return {
        widget_type: 'markets.overview',
        widget_id: instance.id,
        label: `${t(overviewTitleKey(instance.config))} · ${rows.length}`,
        description: rows.length ? `${rows.length} ${rows.length === 1 ? 'index' : 'indices'}` : 'empty',
        captured_at: new Date().toISOString(),
        text,
        data: { indices },
      };
    },
  });
  const containerRef = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const update = () => setCompact(el.clientWidth > 0 && el.clientWidth < COMPACT_WIDTH_PX);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div ref={containerRef} className="h-full">
      <IndexMovementCard
        indices={indices}
        loading={loading}
        forceMobile={compact}
      />
    </div>
  );
}

function MarketsOverviewSettings({ config, onChange, onClose }: WidgetSettingsProps<MarketsOverviewConfig>) {
  const { t } = useTranslation();
  return (
    <div className="space-y-4">
      <EnumField
        label={t('dashboard.widgets.marketsOverview.basket')}
        value={config.basket ?? 'us'}
        onChange={(v) => onChange({ basket: v as MarketsOverviewConfig['basket'], indices: [] })}
        options={[
          { value: 'us', label: t('dashboard.widgets.marketsOverview.basket_us') },
          { value: 'cn', label: t('dashboard.widgets.marketsOverview.basket_cn') },
          { value: 'hk', label: t('dashboard.widgets.marketsOverview.basket_hk') },
        ]}
        helper={t('dashboard.widgets.marketsOverview.basketHelper')}
      />
      <SettingsDoneButton onClick={onClose} />
    </div>
  );
}

registerWidget<MarketsOverviewConfig>({
  type: 'markets.overview',
  titleKey: 'dashboard.widgets.marketsOverview.title',
  descriptionKey: 'dashboard.widgets.marketsOverview.description',
  resolveTitleKey: overviewTitleKey,
  category: 'markets',
  icon: LineChart,
  component: MarketsOverviewWidget,
  settingsComponent: MarketsOverviewSettings,
  defaultConfig: {},
  configSchema: MarketsOverviewConfigSchema,
  defaultSize: { w: 12, h: 11 },
  minSize: { w: 3, h: 11 },
  maxSize: { w: 12, h: 11 },
});

export default MarketsOverviewWidget;
