import { cn } from '@/shared/lib/utils'
import { CapsuleTabs, HINT_TEXT, SECTION_TITLE, SettingCard, SettingRow, Toggle } from '@/shared/panel'
import type { Dictionary } from '@/shared/strings'

type AgentDefaultsCopy = Dictionary['settings']['inference']['agentDefaults']

export interface AgentFormState {
  reasoning_effort: string
  enable_background_review: boolean
}

const REASONING_OPTIONS = ['none', 'low', 'medium', 'high'] as const

export function AgentDefaultsSection({
  disabled,
  state,
  t,
  update
}: {
  disabled: boolean
  state: AgentFormState
  t: AgentDefaultsCopy
  update: (patch: Partial<AgentFormState>) => void
}): React.JSX.Element {
  return (
    <section>
      <p className={cn(SECTION_TITLE, 'mb-1')}>{t.heading}</p>
      {t.intro ? <p className={cn(HINT_TEXT, 'mb-2')}>{t.intro}</p> : null}
      <SettingCard>
        <SettingRow description={t.reasoningEffortDesc} label={t.reasoningEffort}>
          <CapsuleTabs
            ariaLabel={t.reasoningEffort}
            disabled={disabled}
            onChange={value => update({ reasoning_effort: value })}
            options={REASONING_OPTIONS.map(opt => ({ value: opt, label: t.reasoningOptions[opt] }))}
            size="sm"
            value={state.reasoning_effort}
          />
        </SettingRow>

        <SettingRow description={t.backgroundReviewDesc} label={t.backgroundReview}>
          <Toggle
            ariaLabel={t.backgroundReview}
            checked={state.enable_background_review}
            disabled={disabled}
            onChange={value => update({ enable_background_review: value })}
          />
        </SettingRow>
      </SettingCard>
    </section>
  )
}
