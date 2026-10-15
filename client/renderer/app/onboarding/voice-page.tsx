import { nextVoice, voiceSelectionId } from '@/modules/speech'

import { SpinnerWithText } from './onboarding-components'
import type { OnboardingVoice } from './use-onboarding-voice'

interface VoicePageProps {
  voice: OnboardingVoice
  onDescribe: () => void
  confirmVoice: () => void
  onContinueText: () => void
  confirming: boolean
  error: string | null
}

export function VoicePage({
  voice: state,
  onDescribe,
  confirmVoice,
  onContinueText,
  confirming,
  error
}: VoicePageProps): React.JSX.Element {
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
          <p className="mb-3 text-[13px] text-body">选一个喜欢的声音，点击就能试听。之后可以随时更换。</p>
          <div className="rounded-xl border border-line-hairline bg-surface-card p-3">
            <p className="mb-1 text-[10px] tracking-wide text-muted">为你推荐</p>
            <div className="flex items-start justify-between gap-3 text-xs text-strong">
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 truncate font-medium">{voice.label}</p>
                {voice.tags.length > 0 && (
                  <p className="mt-0.5 truncate text-[10px] text-muted">{voice.tags.join(' · ')}</p>
                )}
              </div>
              <div className="flex shrink-0 gap-3">
                <button
                  className="transition hover:text-strong disabled:opacity-40"
                  disabled={voicePreparing || confirming}
                  onClick={() => previewVoice(voice, 'onboarding.voice.preview.try')}
                  type="button"
                >
                  试听
                </button>
                <button
                  className="transition hover:text-strong disabled:opacity-40"
                  disabled={voicePreparing || confirming}
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

          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-body">更多声音</summary>
            <div className="max-h-48 overflow-y-auto rounded-xl border border-line-hairline bg-surface-card">
              {otherVoices.length === 0 ? (
                <p className="px-3 py-4 text-center text-[10px] text-faint">没有更多音色可选</p>
              ) : (
                otherVoices.map(v => (
                  <button
                    className="flex w-full items-center justify-between gap-3 border-b border-line-hairline px-3 py-2 text-left text-xs text-body transition last:border-b-0 hover:bg-fill-hover disabled:opacity-40"
                    disabled={voicePreparing || confirming}
                    key={voiceSelectionId(v)}
                    onClick={() => selectVoice(v, 'onboarding.voice.preview.try')}
                    type="button"
                  >
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5 truncate">{v.label}</span>
                      {v.tags.length > 0 && (
                        <span className="block truncate text-[10px] text-faint">{v.tags.join(' · ')}</span>
                      )}
                    </span>
                    <span className="shrink-0 text-[10px] text-faint">试听并选择</span>
                  </button>
                ))
              )}
            </div>
          </details>
          <p className="mt-1 text-[10px] text-muted">
            {voiceCatalog.length} 个音色 · 先挑个差不多的就行，以后随时能在设置里调。
          </p>
          <div className="mt-3 flex items-center justify-between gap-3 text-xs">
            <button
              className="text-body transition hover:text-strong disabled:opacity-40"
              disabled={confirming}
              onClick={onDescribe}
              type="button"
            >
              描述想要的声音
            </button>
            <button
              className="h-9 flex-1 rounded-lg bg-accent text-sm font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
              disabled={confirming || voicePreparing}
              onClick={confirmVoice}
              type="button"
            >
              {confirming ? '正在保存…' : '使用这个声音'}
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
                : '声音暂时不可用，可以先用文字聊天，之后再到设置中选择声音。'}
            </p>
          )}
          <div className="mt-3 flex items-center gap-4 text-xs">
            <button
              className="text-body transition hover:text-strong disabled:opacity-40"
              disabled={confirming}
              onClick={onDescribe}
              type="button"
            >
              描述想要的声音
            </button>
            {voiceLoad === 'failed' && (
              <button
                className="text-strong transition hover:text-accent disabled:opacity-40"
                disabled={confirming}
                onClick={retry}
                type="button"
              >
                重试
              </button>
            )}
            {voiceLoad !== 'loading' && (
              <button
                className="ml-auto rounded-lg bg-accent px-4 py-2 text-on-accent disabled:opacity-40"
                disabled={confirming || voicePreparing}
                onClick={onContinueText}
                type="button"
              >
                {confirming ? '正在保存…' : '先用文字聊天'}
              </button>
            )}
          </div>
        </div>
      )}
      {error && (
        <p className="mt-3 text-xs text-danger-fg" role="alert">
          {error}
        </p>
      )}
    </>
  )
}
