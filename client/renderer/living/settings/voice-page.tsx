import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useState } from 'react'

import {
  $companionVoiceId,
  $persona,
  designVoice,
  fetchVoiceCatalogRaw,
  GENDER_OPTIONS,
  playDataUrl,
  sampleLine,
  setCompanionVoiceId,
  speakScripted,
  type VoiceCatalog,
  type VoiceDesignPreview,
  VoiceProviderBadge,
  voiceSelectionId
} from '@/companion'
import { useGatewayRequest } from '@/shared'
import { Check } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  CapsuleTabs,
  HINT_TEXT,
  INPUT_CLASS,
  SECTION_TITLE,
  SettingCard,
  SETTINGS_ROW_DESC,
  SETTINGS_ROW_TITLE,
  SettingsSectionIntro
} from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'

// 音色页：目录筛选 / 试听 / 切换 + 专属音色设计。长页（living-settings）内嵌段。
export function VoicePage(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.voice

  const { requestGateway } = useGatewayRequest()
  const persona = useStore($persona)
  const currentVoice = useStore($companionVoiceId)
  const locale = useStore($locale)

  const [catalog, setCatalog] = useState<VoiceCatalog>({
    providers: [],
    voices: [],
    supportsVoiceDesign: false,
    voiceDesignGuide: ''
  })

  const [genderFilter, setGenderFilter] = useState('')

  const [designPrompt, setDesignPrompt] = useState('')
  const [designPreview, setDesignPreview] = useState<VoiceDesignPreview | null>(null)
  const [designing, setDesigning] = useState(false)
  const [designHint, setDesignHint] = useState<string | null>(null)

  const filteredVoices = useMemo(
    () => catalog.voices.filter(v => !genderFilter || v.gender === genderFilter),
    [catalog.voices, genderFilter]
  )

  useEffect(() => {
    void fetchVoiceCatalogRaw(requestGateway, locale).then(r => {
      if (r.ok) {
        setCatalog(r.catalog)
      }
    })
  }, [locale, requestGateway])

  const runDesign = async (): Promise<void> => {
    const prompt = designPrompt.trim()

    if (!prompt) {
      return
    }

    setDesigning(true)
    setDesignHint(null)

    try {
      const result = await designVoice(requestGateway, prompt, sampleLine(persona?.name ?? ''))
      setDesignPreview(result)
      void playDataUrl(result.trialAudioDataUrl)
    } catch {
      setDesignHint(t.designFailed)
    } finally {
      setDesigning(false)
    }
  }

  if (catalog.voices.length === 0) {
    return (
      <div className="space-y-4">
        <SettingsSectionIntro hint={t.intro} title={t.title} />
        <p className="text-[13px] text-muted">
          {catalog.providers.length > 0 ? t.noVoicesForLanguage : t.noTtsConfigured}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <SettingsSectionIntro hint={t.intro} title={t.title} />
      <div className="flex flex-wrap items-center gap-2">
        <CapsuleTabs
          ariaLabel={t.genderFilterAria}
          onChange={setGenderFilter}
          options={GENDER_OPTIONS.map(g => ({ label: g.label, value: g.id }))}
          size="sm"
          value={genderFilter}
        />
      </div>

      <SettingCard>
        {filteredVoices.map(v => {
          const selectionId = voiceSelectionId(v)
          const inUse = currentVoice === selectionId

          return (
            <div className="flex items-center justify-between gap-3 px-4 py-3" key={selectionId}>
              <div className="min-w-0">
                <p className={cn(SETTINGS_ROW_TITLE, 'flex items-center gap-1.5')}>
                  {v.label}
                  <VoiceProviderBadge provider={v.provider} />
                  {inUse ? <Check className="size-3.5 text-accent" /> : null}
                </p>
                <p className={SETTINGS_ROW_DESC}>{v.tags.join(' · ')}</p>
              </div>
              <div className="flex shrink-0 gap-1.5">
                <button
                  className="rounded-lg px-2.5 py-1 text-xs text-body transition hover:bg-fill-hover hover:text-strong"
                  onClick={() =>
                    void speakScripted(sampleLine(persona?.name ?? ''), selectionId || undefined, 'voice.preview')
                  }
                  type="button"
                >
                  {t.preview}
                </button>
                <button
                  className={cn(BTN_SUBTLE, 'h-7 px-3', inUse && 'border-accent-line text-accent font-medium')}
                  disabled={inUse}
                  onClick={() => setCompanionVoiceId(selectionId)}
                  type="button"
                >
                  {inUse ? t.inUse : t.use}
                </button>
              </div>
            </div>
          )
        })}
        {filteredVoices.length === 0 && <p className="px-4 py-6 text-center text-[13px] text-muted">{t.noMatch}</p>}
      </SettingCard>

      {catalog.supportsVoiceDesign && (
        <section>
          <p className={cn(SECTION_TITLE, 'mb-2')}>{t.designHeading}</p>
          <SettingCard className="p-4" divided={false}>
            {catalog.voiceDesignGuide && (
              <p className={cn(HINT_TEXT, 'whitespace-pre-line')}>{catalog.voiceDesignGuide}</p>
            )}
            <textarea
              className={cn(INPUT_CLASS, 'mt-2 resize-none')}
              onChange={e => setDesignPrompt(e.target.value)}
              placeholder={t.designPlaceholder}
              rows={3}
              value={designPrompt}
            />
            <div className="mt-2.5 flex items-center gap-2">
              <button
                className={BTN_PRIMARY}
                disabled={designing || !designPrompt.trim()}
                onClick={() => void runDesign()}
                type="button"
              >
                {designing ? t.designGenerating : t.designGenerate}
              </button>
              {designPreview && (
                <>
                  <button
                    className="rounded-lg px-2.5 py-1 text-xs text-body transition hover:bg-fill-hover hover:text-strong"
                    onClick={() => void playDataUrl(designPreview.trialAudioDataUrl)}
                    type="button"
                  >
                    {t.preview}
                  </button>
                  <button
                    className={cn(
                      BTN_SUBTLE,
                      'h-7 px-3',
                      currentVoice === designPreview.voiceId && 'border-emerald-400/30 text-emerald-300'
                    )}
                    disabled={currentVoice === designPreview.voiceId}
                    onClick={() => setCompanionVoiceId(designPreview.voiceId)}
                    type="button"
                  >
                    {currentVoice === designPreview.voiceId ? t.inUse : t.use}
                  </button>
                </>
              )}
            </div>
            {designHint && <p className="mt-2 text-xs text-amber-300/90">{designHint}</p>}
          </SettingCard>
        </section>
      )}
    </div>
  )
}
