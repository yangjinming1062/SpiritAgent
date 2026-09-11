import { cn } from '@/shared/lib/utils'
import { HINT_TEXT, SECTION_TITLE, SettingCard, SettingRow, Slider, Toggle } from '@/shared/panel'
import type { Dictionary } from '@/shared/strings'

type ContextCompressionCopy = Dictionary['settings']['inference']['contextCompression']

export interface ChatFormState {
  enable_context_compression: boolean
  context_compression_threshold: number
}

const THRESHOLD_MIN = 0.3
const THRESHOLD_MAX = 1.0
const THRESHOLD_STEP = 0.05

export function ContextCompressionSection({
  disabled,
  state,
  t,
  update
}: {
  disabled: boolean
  state: ChatFormState
  t: ContextCompressionCopy
  update: (patch: Partial<ChatFormState>) => void
}): React.JSX.Element {
  const thresholdPct = Math.round(state.context_compression_threshold * 100)

  return (
    <section>
      <p className={cn(SECTION_TITLE, 'mb-1')}>{t.heading}</p>
      {t.intro ? <p className={cn(HINT_TEXT, 'mb-2')}>{t.intro}</p> : null}
      <SettingCard>
        <SettingRow description={t.enableCompressionDesc} label={t.enableCompression}>
          <Toggle
            ariaLabel={t.enableCompression}
            checked={state.enable_context_compression}
            disabled={disabled}
            onChange={value => update({ enable_context_compression: value })}
          />
        </SettingRow>

        <SettingRow description={t.thresholdDesc} label={t.threshold}>
          <div className="flex w-48 items-center gap-3">
            <Slider
              ariaLabel={t.threshold}
              disabled={disabled}
              max={THRESHOLD_MAX}
              min={THRESHOLD_MIN}
              onChange={value =>
                update({
                  context_compression_threshold: Math.min(
                    THRESHOLD_MAX,
                    Math.max(THRESHOLD_MIN, Math.round(value * 100) / 100)
                  )
                })
              }
              step={THRESHOLD_STEP}
              value={state.context_compression_threshold}
            />
            <span className="w-10 shrink-0 text-right font-mono text-xs text-body">{thresholdPct}%</span>
          </div>
        </SettingRow>
      </SettingCard>
    </section>
  )
}
