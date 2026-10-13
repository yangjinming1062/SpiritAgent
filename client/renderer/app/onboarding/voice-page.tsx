import { nextVoice, VoiceProviderBadge, voiceSelectionId } from '@/modules/speech'

import { SpinnerWithText } from './onboarding-components'
import type { OnboardingVoice } from './use-onboarding-voice'

interface VoicePageProps {
  voice: OnboardingVoice
  onDescribe: () => void
  confirmVoice: () => void
}

export function VoicePage({ voice: state, onDescribe, confirmVoice }: VoicePageProps): React.JSX.Element {
  const {
    voice,
    voicePreparing,
    voiceLoad,
    voiceCatalog,
    otherVoices,
    voiceCandidates,
    previewVoice,
    selectVoice,
    retry
  } = state

  return (
    <>
      {voiceLoad === 'ready' && voice && (
        <div className="mt-1">
          <p className="mb-3 text-[13px] text-body">挑一个我说话的声音吧，随时可以试听。</p>
          <div className="rounded-xl border border-line-hairline bg-surface-card p-3">
            <p className="mb-1 text-[10px] tracking-wide text-muted">为你推荐</p>
            <div className="flex items-start justify-between gap-3 text-xs text-strong">
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 truncate font-medium">
                  {voice.label}
                  <VoiceProviderBadge provider={voice.provider} />
                </p>
                {voice.tags.length > 0 && (
                  <p className="mt-0.5 truncate text-[10px] text-muted">{voice.tags.join(' · ')}</p>
                )}
              </div>
              <div className="flex shrink-0 gap-3">
                <button
                  className="transition hover:text-strong disabled:opacity-40"
                  disabled={voicePreparing}
                  onClick={() => previewVoice(voice, 'onboarding.voice.preview.try')}
                  type="button"
                >
                  试听
                </button>
                <button
                  className="transition hover:text-strong disabled:opacity-40"
                  disabled={voicePreparing}
                  onClick={() => {
                    const next = nextVoice(voiceSelectionId(voice), voiceCandidates)

                    if (next) {
                      selectVoice(next, 'onboarding.voice.preview.next')
                    }
                  }}
                  type="button"
                >
                  换一个
                </button>
              </div>
            </div>
          </div>

          <p className="mt-3 mb-1 text-[10px] tracking-wide text-muted">浏览目录</p>
          <div className="max-h-48 overflow-y-auto rounded-xl border border-line-hairline bg-surface-card">
            {otherVoices.length === 0 ? (
              <p className="px-3 py-4 text-center text-[10px] text-faint">没有更多音色可选</p>
            ) : (
              otherVoices.map(v => (
                <button
                  className="flex w-full items-center justify-between gap-3 border-b border-line-hairline px-3 py-2 text-left text-xs text-body transition last:border-b-0 hover:bg-fill-hover disabled:opacity-40"
                  disabled={voicePreparing}
                  key={voiceSelectionId(v)}
                  onClick={() => selectVoice(v, 'onboarding.voice.preview.try')}
                  type="button"
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 truncate">
                      {v.label}
                      <VoiceProviderBadge provider={v.provider} />
                    </span>
                    {v.tags.length > 0 && (
                      <span className="block truncate text-[10px] text-faint">{v.tags.join(' · ')}</span>
                    )}
                  </span>
                  <span className="shrink-0 text-[10px] text-faint">试听并选择</span>
                </button>
              ))
            )}
          </div>
          <p className="mt-1 text-[10px] text-muted">
            {voiceCatalog.length} 个音色 · 先挑个差不多的就行，以后随时能在设置里调。
          </p>
          <div className="mt-3 flex items-center justify-between gap-3 text-xs">
            <button className="text-body transition hover:text-strong" onClick={onDescribe} type="button">
              上一步
            </button>
            <button
              className="h-9 flex-1 rounded-lg bg-accent text-sm font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
              onClick={confirmVoice}
              type="button"
            >
              使用这个
            </button>
          </div>
        </div>
      )}

      {(voiceLoad !== 'ready' || !voice) && (
        <div className="mt-1">
          {voiceLoad === 'loading' ? (
            <SpinnerWithText text="正在加载音色…" />
          ) : (
            <p className="text-[13px] text-body">
              {voiceLoad === 'failed'
                ? '音色加载失败，请检查网络后重试。'
                : '当前系统语言没有可用音色，请先配置支持该语言的 TTS 供应商。'}
            </p>
          )}
          <div className="mt-3 flex items-center gap-4 text-xs">
            <button className="text-body transition hover:text-strong" onClick={onDescribe} type="button">
              上一步
            </button>
            {voiceLoad === 'failed' && (
              <button className="text-strong transition hover:text-accent" onClick={retry} type="button">
                重试
              </button>
            )}
          </div>
        </div>
      )}
    </>
  )
}
