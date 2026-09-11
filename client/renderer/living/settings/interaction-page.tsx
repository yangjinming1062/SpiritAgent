import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useState } from 'react'

import {
  $autonomousMedia,
  $autonomousVoice,
  $effectiveTier,
  $llmAffect,
  $llmAutonomy,
  $llmReactions,
  $responseMode,
  $userPreferredTier,
  autonomousMediaPref,
  autonomousVoicePref,
  DISTURBANCE_TIERS,
  type DisturbanceTier,
  llmAffectPref,
  llmAutonomyPref,
  llmReactionsPref,
  pushEffectiveDisturbanceTier,
  type ResponseMode,
  setDisturbanceTier,
  setResponseMode
} from '@/companion'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Check } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import {
  HINT_TEXT,
  PanelSelect,
  SECTION_TITLE,
  Segmented,
  SettingCard,
  SettingRow,
  SettingsSectionIntro,
  Toggle
} from '@/shared/panel'
import { getSpiritAgentConfig, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

const RECORDING_OPTIONS = [15, 30, 60, 120, 300] as const
const DEFAULT_RECORDING_SECONDS = 60

// 交互页：伙伴怎么回应（回应方式 / 打扰档位 / 智能反应与自主行为）。
// 长页（living-settings）内嵌段，不使用 SettingsPage 外壳。
export function InteractionPage(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.interaction

  const tier = useStore($userPreferredTier)
  const responseMode = useStore($responseMode)
  const llmReactions = useStore($llmReactions)
  const llmAffect = useStore($llmAffect)
  const llmAutonomy = useStore($llmAutonomy)
  const autonomousMedia = useStore($autonomousMedia)
  const autonomousVoice = useStore($autonomousVoice)

  const [maxRecordingSeconds, setMaxRecordingSeconds] = useState<number>(DEFAULT_RECORDING_SECONDS)
  const [isSavingRecordTime, setIsSavingRecordTime] = useState(false)

  useEffect(() => {
    let mounted = true
    void getSpiritAgentConfig()
      .then(cfg => {
        if (mounted && typeof cfg.voice?.max_recording_seconds === 'number') {
          setMaxRecordingSeconds(cfg.voice.max_recording_seconds)
        }
      })
      .catch(() => {})

    return () => {
      mounted = false
    }
  }, [])

  const recordingOptions = useMemo(
    () =>
      Array.from(new Set([...RECORDING_OPTIONS, maxRecordingSeconds]))
        .sort((a, b) => a - b)
        .map(sec => ({ value: String(sec), label: `${sec} ${t.recordingSecondsSuffix}` })),
    [maxRecordingSeconds, t.recordingSecondsSuffix]
  )

  const handleRecordingSecondsChange = async (val: string): Promise<void> => {
    const nextSec = Number(val)
    const prevSec = maxRecordingSeconds
    setMaxRecordingSeconds(nextSec)
    setIsSavingRecordTime(true)

    try {
      await saveSpiritAgentConfig({
        voice: { max_recording_seconds: nextSec }
      })
      triggerHaptic('success')
    } catch (err) {
      setMaxRecordingSeconds(prevSec)
      notifyError(err, t.recordingSaveFailed)
    } finally {
      setIsSavingRecordTime(false)
    }
  }

  const selectTier = (id: DisturbanceTier): void => {
    setDisturbanceTier(id)
    // 推送 EFFECTIVE 档位（含活动覆盖）以保证后端闸门与渲染层一致。
    // 否则在手动点击后，沉浸式焦点上下文会让后端在整个轮询周期内都保持 un-mute。
    pushEffectiveDisturbanceTier($effectiveTier.get())
  }

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={t.intro} title={t.title} />

      <section>
        <p className={cn(SECTION_TITLE, 'mb-2')}>{t.voiceHeading}</p>
        <SettingCard>
          <SettingRow description={t.responseModeDesc} label={t.responseMode} stacked>
            <div className="max-w-xs">
              <Segmented<ResponseMode>
                onChange={setResponseMode}
                options={[
                  { value: 'text', label: t.responseModeText },
                  { value: 'voice', label: t.responseModeVoice }
                ]}
                value={responseMode}
              />
            </div>
          </SettingRow>
          <SettingRow description={t.recordingDesc} label={t.recording}>
            <PanelSelect
              disabled={isSavingRecordTime}
              onChange={v => void handleRecordingSecondsChange(v)}
              options={recordingOptions}
              value={String(maxRecordingSeconds)}
              widthClass="w-28"
            />
          </SettingRow>
        </SettingCard>
      </section>

      <section>
        <p className={cn(SECTION_TITLE, 'mb-1')}>{t.tierHeading}</p>
        <p className={cn(HINT_TEXT, 'mb-2')}>{t.tierHint}</p>
        <SettingCard ariaLabel={t.tierAriaLabel} role="radiogroup">
          {DISTURBANCE_TIERS.map(item => {
            const isSelected = tier === item.id

            return (
              <button
                aria-checked={isSelected}
                className={cn(
                  'flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-fill-hover',
                  isSelected && 'bg-accent-soft/35'
                )}
                key={item.id}
                onClick={() => selectTier(item.id)}
                role="radio"
                type="button"
              >
                <div className="min-w-0 flex-1">
                  <div className={cn('text-[13px] font-medium', isSelected ? 'text-strong' : 'text-body')}>
                    {item.label}
                  </div>
                  <div className="mt-0.5 text-[11px] leading-relaxed text-muted">{item.hint}</div>
                </div>
                {isSelected ? <Check className="size-4 shrink-0 text-accent" /> : null}
              </button>
            )
          })}
        </SettingCard>
      </section>

      <section>
        <p className={cn(SECTION_TITLE, 'mb-1')}>{t.smartHeading}</p>
        <p className={cn(HINT_TEXT, 'mb-2')}>{t.smartHint}</p>
        <SettingCard>
          <SettingRow description={t.pokeThinkingDesc} label={t.pokeThinking}>
            <Toggle ariaLabel={t.pokeThinkingAria} checked={llmReactions} onChange={llmReactionsPref.set} />
          </SettingRow>
          <SettingRow description={t.idleAffectDesc} label={t.idleAffect}>
            <Toggle ariaLabel={t.idleAffectAria} checked={llmAffect} onChange={llmAffectPref.set} />
          </SettingRow>
          <SettingRow description={t.autonomyDesc} label={t.autonomy}>
            <Toggle ariaLabel={t.autonomyAria} checked={llmAutonomy} onChange={llmAutonomyPref.set} />
          </SettingRow>
          <SettingRow description={t.autonomousMediaDesc} label={t.autonomousMedia}>
            <Toggle ariaLabel={t.autonomousMediaAria} checked={autonomousMedia} onChange={autonomousMediaPref.set} />
          </SettingRow>
          <SettingRow description={t.autonomousVoiceDesc} label={t.autonomousVoice}>
            <Toggle ariaLabel={t.autonomousVoiceAria} checked={autonomousVoice} onChange={autonomousVoicePref.set} />
          </SettingRow>
        </SettingCard>
      </section>
    </div>
  )
}
