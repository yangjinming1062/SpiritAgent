import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useState } from 'react'

import {
  $autonomousMedia,
  $autonomousVoice,
  $autoplayVoice,
  $llmAffect,
  $llmAutonomy,
  $responsePreference,
  $userPreferredTier,
  autonomousMediaPref,
  autonomousVoicePref,
  autoplayVoicePref,
  type DisturbanceTier,
  endQuiet,
  llmAffectPref,
  llmAutonomyPref,
  type ResponsePreference,
  setDisturbanceTier,
  setResponsePreference
} from '@/modules/character'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Check } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
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

const DISTURBANCE_TIERS: readonly DisturbanceTier[] = ['still', 'normal', 'autonomous']

const RECORDING_OPTIONS = [15, 30, 60, 120, 300] as const
const DEFAULT_RECORDING_SECONDS = 60

// 交互页：伙伴怎么回应（回应方式 / 打扰档位 / 智能反应与自主行为）。长页（living-settings）内嵌段。
export function InteractionPage(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.interaction

  const tier = useStore($userPreferredTier)
  const responsePreference = useStore($responsePreference)
  const llmAffect = useStore($llmAffect)
  const llmAutonomy = useStore($llmAutonomy)
  const autonomousMedia = useStore($autonomousMedia)
  const autonomousVoice = useStore($autonomousVoice)
  const autoplayVoice = useStore($autoplayVoice)

  const [maxRecordingSeconds, setMaxRecordingSeconds] = useState<number>(DEFAULT_RECORDING_SECONDS)
  const [isSavingRecordTime, setIsSavingRecordTime] = useState(false)
  // 读取失败时选择框显示的是默认值而非实际设置，禁用以免误改。
  const [recordingLoadFailed, setRecordingLoadFailed] = useState(false)

  useEffect(() => {
    let mounted = true
    void getSpiritAgentConfig()
      .then(cfg => {
        if (mounted && typeof cfg.voice?.max_recording_seconds === 'number') {
          setMaxRecordingSeconds(cfg.voice.max_recording_seconds)
        }
      })
      .catch((err: unknown) => {
        log.warn('interaction', 'load recording limit failed', err)

        if (mounted) {
          setRecordingLoadFailed(true)
        }
      })

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

  // 明确选择档位即结束临时安静；先写偏好，精灵窗同步时不会短暂按旧偏好生效。生效档位由精灵窗经 storage 同步后立即重算并推送（含活动覆盖），本窗不推送。
  const selectTier = (id: DisturbanceTier): void => {
    setDisturbanceTier(id)
    endQuiet()
  }

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={t.intro} title={t.title} />

      <section>
        <p className={cn(SECTION_TITLE, 'mb-2')}>{t.voiceHeading}</p>
        <SettingCard>
          <SettingRow description={t.responsePreferenceDesc} label={t.responsePreference} stacked>
            <div className="max-w-xs">
              <Segmented<ResponsePreference>
                onChange={setResponsePreference}
                options={[
                  { value: 'text', label: t.responsePreferenceText },
                  { value: 'voice', label: t.responsePreferenceVoice }
                ]}
                value={responsePreference}
              />
            </div>
          </SettingRow>
          <SettingRow
            description={
              recordingLoadFailed ? <span className="text-danger-fg">{t.recordingLoadFailed}</span> : t.recordingDesc
            }
            label={t.recording}
          >
            <PanelSelect
              disabled={isSavingRecordTime || recordingLoadFailed}
              onChange={v => void handleRecordingSecondsChange(v)}
              options={recordingOptions}
              value={String(maxRecordingSeconds)}
              widthClass="w-28"
            />
          </SettingRow>
          <SettingRow label={t.autoplayVoice}>
            <Toggle ariaLabel={t.autoplayVoice} checked={autoplayVoice} onChange={autoplayVoicePref.set} />
          </SettingRow>
        </SettingCard>
      </section>

      <section>
        <p className={cn(SECTION_TITLE, 'mb-1')}>{t.tierHeading}</p>
        <p className={cn(HINT_TEXT, 'mb-2')}>{t.tierHint}</p>
        <SettingCard ariaLabel={t.tierAriaLabel} role="radiogroup">
          {DISTURBANCE_TIERS.map(id => {
            const isSelected = tier === id

            return (
              <button
                aria-checked={isSelected}
                className={cn(
                  'flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-fill-hover',
                  isSelected && 'bg-accent-soft/35'
                )}
                key={id}
                onClick={() => selectTier(id)}
                role="radio"
                type="button"
              >
                <div className="min-w-0 flex-1">
                  <div className={cn('text-[13px] font-medium', isSelected ? 'text-strong' : 'text-body')}>
                    {t.tiers[id].label}
                  </div>
                  <div className="mt-0.5 text-[11px] leading-relaxed text-muted">{t.tiers[id].hint}</div>
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
