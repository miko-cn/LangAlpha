import { Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import i18n from '@/i18n';
import AIDailyBriefCard, { getCachedInsights } from '../../components/AIDailyBriefCard';
import { useDashboardContext } from '../framework/DashboardDataContext';
import { registerWidget } from '../framework/WidgetRegistry';
import {
  useWidgetContextExport,
  type WidgetContextSnapshot,
} from '../framework/contextSnapshot';
import {
  serializeInsightDayToMarkdown,
  wrapWidgetContext,
} from '../framework/snapshotSerializers';
import { buildInsightSnapshot, normalizeInsight } from '../../utils/insightFetch';
import { InsightBriefConfigSchema } from '../framework/configSchemas';
import { EnumField } from '../framework/settings/EnumField';
import { SettingsDoneButton } from '../framework/settings/SettingsDoneButton';
import type { WidgetRenderProps, WidgetSettingsProps } from '../types';
import './InsightBriefWidget.css';

type InsightBriefConfig = { variant?: 'latest' | 'personalized'; focus?: 'us' | 'cn' };

function briefTitleKey(config: InsightBriefConfig): string {
  return config.focus === 'cn'
    ? 'dashboard.widgets.insightBrief.title_cn'
    : 'dashboard.widgets.insightBrief.title';
}

interface CachedInsight {
  market_insight_id: string;
  type: string;
  headline: string;
  summary: string;
  completed_at?: string;
  topics?: Array<{ text: string; trend: 'up' | 'down' | 'neutral' }>;
  [key: string]: unknown;
}

function InsightBriefWidget({ instance }: WidgetRenderProps<InsightBriefConfig>) {
  const { t } = useTranslation();
  const titleKey = briefTitleKey(instance.config);
  useWidgetContextExport(instance.id, {
    full: (): WidgetContextSnapshot => {
      const cached = (getCachedInsights() as CachedInsight[] | null) ?? [];
      const titleResolved = t(titleKey);

      if (!cached.length) {
        // Brief hasn't loaded yet. Emit a config-only directive so the agent
        // at least knows what the user is looking at.
        return {
          widget_type: 'insight.brief',
          widget_id: instance.id,
          label: titleResolved,
          captured_at: new Date().toISOString(),
          text: wrapWidgetContext('insight.brief', {}, `Widget: ${titleResolved}\n_(Brief data not yet loaded.)_`),
          data: { config: instance.config },
        };
      }

      const items = cached.map((it) => normalizeInsight(it));
      const personalizedCount = items.filter((it) => it.type === 'personalized').length;
      const body = `Widget: ${titleResolved}\n\n${serializeInsightDayToMarkdown(items)}`;
      const latest = items[0];
      const descParts = [`${items.length} brief${items.length === 1 ? '' : 's'}`];
      if (personalizedCount) descParts.push(`${personalizedCount} personalized`);

      return {
        widget_type: 'insight.brief',
        widget_id: instance.id,
        label: latest.headline,
        description: descParts.join(' · '),
        captured_at: new Date().toISOString(),
        text: wrapWidgetContext(
          'insight.brief',
          {
            count: items.length,
            personalized: personalizedCount || undefined,
            latestType: latest.type,
            completedAt: latest.completed_at,
          },
          body,
        ),
        data: { items, count: items.length, personalizedCount },
      };
    },
    rows: async (rowId: string) => {
      const cached = (getCachedInsights() as CachedInsight[] | null) ?? [];
      const item = cached.find((i) => i.market_insight_id === rowId);
      if (!item) return null;
      return buildInsightSnapshot({
        instanceId: instance.id,
        rowId,
        insightId: rowId,
        fallback: normalizeInsight(item),
      });
    },
  });

  const { modals } = useDashboardContext();
  return (
    <div className="insight-brief-widget">
      <AIDailyBriefCard
        onReadFull={modals.openInsight}
        instanceId={instance.id}
        focus={instance.config.focus}
      />
    </div>
  );
}

function InsightBriefSettings({ config, onChange, onClose }: WidgetSettingsProps<InsightBriefConfig>) {
  const { t, i18n } = useTranslation();
  const inferredFocus = i18n.language.toLowerCase().startsWith('zh') ? 'cn' : 'us';
  return (
    <div className="space-y-4">
      <EnumField
        label={t('dashboard.widgets.insightBrief.focus')}
        value={config.focus ?? inferredFocus}
        onChange={(v) => onChange({ focus: v as InsightBriefConfig['focus'] })}
        options={[
          { value: 'us', label: t('dashboard.widgets.insightBrief.focus_us') },
          { value: 'cn', label: t('dashboard.widgets.insightBrief.focus_cn') },
        ]}
        helper={t('dashboard.widgets.insightBrief.focusHelper')}
      />
      <SettingsDoneButton onClick={onClose} />
    </div>
  );
}

registerWidget<InsightBriefConfig>({
  type: 'insight.brief',
  titleKey: 'dashboard.widgets.insightBrief.title',
  descriptionKey: 'dashboard.widgets.insightBrief.description',
  resolveTitleKey: briefTitleKey,
  category: 'intel',
  icon: Sparkles,
  component: InsightBriefWidget,
  settingsComponent: InsightBriefSettings,
  defaultConfig: { variant: 'latest' },
  initConfig: () => ({
    variant: 'latest' as const,
    focus: (i18n.language.toLowerCase().startsWith('zh') ? 'cn' : 'us') as InsightBriefConfig['focus'],
  }),
  configSchema: InsightBriefConfigSchema,
  defaultSize: { w: 8, h: 18 },
  minSize: { w: 4, h: 15 },
  maxSize: { w: 12, h: 44 },
  singleton: true,
  fitToContent: true,
});

export default InsightBriefWidget;
