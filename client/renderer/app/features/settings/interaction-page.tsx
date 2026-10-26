import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useState } from 'react'

import {
  $postsEnabled,
  $responsePreference,
  $userPreferredTier,
  type DisturbanceTier,
  postsEnabledPref,
  type ResponsePreference,
  selectDisturbanceTier,
  setResponsePreference
} from '@/modules/character'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Check } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import {
  CapsuleTabs,
  HINT_TEXT,
  PanelSelect,
  SECTION_TITLE,
  SettingCard,
  SettingRow,
  SettingsSectionIntro,
  Toggle
} from '@/shared/panel'
import { getSpiritAgentConfig, saveSpiritAgentConfig } from '@/shared/spiritagent'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { SocialAccountsSection } from './social-accounts-section'

const DISTURBANCE_TIERS: readonly DisturbanceTier[] = ['still', 'normal', 'autonomous']

const RECORDING_OPTIONS = [15, 30, 60, 120, 300] as const
const DEFAULT_RECORDING_SECONDS = 60

export function InteractionPage({ part = 'companionship' }: { part?: 'companionship' | 'voice' }): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.interaction

  const tier = useStore($userPreferredTier)
  const responsePreference = useStore($responsePreference)
  const postsEnabled = useStore($postsEnabled)

  const [maxRecordingSeconds, setMaxRecordingSeconds] = useState<number>(DEFAULT_RECORDING_SECONDS)
  const [isSavingRecordTime, setIsSavingRecordTime] = useState(false)
  // 读取失败时选择框显示的是默认值而非实际设置，禁用以免误改。
  const [recordingLoadFailed, setRecordingLoadFailed] = useState(false)

  useEffect(() => {
    if (part !== 'voice') {
      return
    }

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
  }, [part])

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

  return (
    <div className="space-y-6">
      {part === 'companionship' && <SettingsSectionIntro hint={t.intro} title={t.title} />}

      {part === 'voice' ? (
        <section>
          <p className={cn(SECTION_TITLE, 'mb-2')}>{t.voiceHeading}</p>
          <SettingCard>
            <SettingRow description={t.responsePreferenceDesc} label={t.responsePreference} stacked>
              <div className="max-w-xs">
                <CapsuleTabs<ResponsePreference>
                  onChange={setResponsePreference}
                  options={[
                    { value: 'text', label: t.responsePreferenceText },
                    { value: 'voice', label: t.responsePreferenceVoice }
                  ]}
                  value={responsePreference}
                  variant="segmented"
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
          </SettingCard>
        </section>
      ) : (
        <>
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
                    onClick={() => selectDisturbanceTier(id)}
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
              <SettingRow description={t.postsEnabledDesc} label={t.postsEnabled}>
                <Toggle ariaLabel={t.postsEnabled} checked={postsEnabled} onChange={postsEnabledPref.set} />
              </SettingRow>
            </SettingCard>
          </section>
          <SocialAccountsSection />
        </>
      )}
    </div>
  )
}
