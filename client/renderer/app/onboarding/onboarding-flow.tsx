import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  applyServerCompanionVoiceId,
  clearDraftRefImage,
  type OnboardingAnswers,
  setCompanionVoiceId,
  setResponsePreference
} from '@/modules/character'
import { stopSpeaking, warmAudioContext } from '@/modules/speech'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { log } from '@/shared/lib/log'
import { BTN_PRIMARY, BTN_SUBTLE, INPUT_CLASS, SURFACE_OVERLAY } from '@/shared/panel'
import { $gatewayState } from '@/shared/store/gateway'

import { computeBackTransition, type Phase, type VoiceStage } from './back-transition'
import { playOnboardingAudio } from './onboarding-audio'
import { DraggableDialog, ONBOARDING_PRIMARY_BUTTON } from './onboarding-components'
import { OnboardingDraft } from './onboarding-draft'
import {
  type AnswerKind,
  CHARACTER_QUESTIONS,
  LOCKED_FIELD_LABELS,
  mergeResumeAnswers,
  PHASE_QUESTIONS,
  resolveResumeTarget,
  USER_QUESTIONS
} from './onboarding-questions'
import { PortraitPages } from './portrait-pages'
import { QuestionPage } from './question-page'
import { useOnboardingPortrait } from './use-onboarding-portrait'
import { VoicePage } from './voice-page'

interface OnboardingFlowProps {
  onCompleted: () => void
}

// 导航与未提交表单归引导视图；持久化和资产操作由专属所有者执行。
export function OnboardingFlow({ onCompleted }: OnboardingFlowProps): React.JSX.Element | null {
  const gatewayState = useStore($gatewayState)
  const [draft] = useState(() => new OnboardingDraft())
  const begin = useCallback(() => draft.captureOperation(), [draft])
  const [phase, setPhase] = useState<Phase>('q-character')
  const [imageSealed, setImageSealed] = useState(false)
  const [qIndex, setQIndex] = useState(0)
  const [answers, setAnswers] = useState<OnboardingAnswers>({})
  const [input, setInput] = useState('')
  const [answerKind, setAnswerKind] = useState<AnswerKind | null>(null)
  const [hint, setHint] = useState<string | null>(null)
  const [voiceStage, setVoiceStage] = useState<VoiceStage>('describe')
  const [voiceSaving, setVoiceSaving] = useState(false)
  const [resumeState, setResumeState] = useState<'failed' | 'ok' | 'retrying'>('ok')
  const [resumeAttempt, setResumeAttempt] = useState(0)
  const resumedRef = useRef(false)
  const savingCharacterRef = useRef(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const answersRef = useLatestRef(answers)
  const onCompletedRef = useLatestRef(onCompleted)
  const currentList = PHASE_QUESTIONS[phase]
  const question = currentList[qIndex]

  useEffect(() => {
    draft.start()
    warmAudioContext()

    return () => {
      draft.dispose()
      stopSpeaking()
    }
  }, [draft])

  const enterVoiceSelection = (): void => {
    setImageSealed(true)
    setPhase('voice')
    setVoiceStage('catalog')
    setQIndex(0)
  }

  const portrait = useOnboardingPortrait({ begin, imageSealed, setHint, setPhase, enterVoiceSelection })
  const portraitRef = useLatestRef(portrait)

  useEffect(() => {
    if (phase !== 'q-character' && !(phase === 'voice' && voiceStage === 'describe')) {
      return
    }

    const current = currentList[qIndex]

    if (!current) {
      return
    }

    setInput(answersRef.current[current.key] ?? '')
    setAnswerKind(null)
    setHint(null)
    void playOnboardingAudio(current.audioTag)

    return () => stopSpeaking()
  }, [phase, voiceStage, qIndex, currentList, answersRef])

  useEffect(() => {
    if ((phase === 'q-character' || (phase === 'voice' && voiceStage === 'describe')) && question) {
      ;(question.multiline ? textareaRef.current : inputRef.current)?.focus()
    }
  }, [phase, question, voiceStage])

  // 草稿读取失败暂停作答；连通后只恢复一次，取消的读取可在重连时继续。
  useEffect(() => {
    if (resumedRef.current || gatewayState !== 'open') {
      return
    }

    resumedRef.current = true
    let cancelled = false
    let pending = true
    const currentOperation = begin()
    const isLive = (): boolean => !cancelled && currentOperation()

    void (async () => {
      try {
        await portraitRef.current.restoreReference(isLive)

        if (!isLive()) {
          return
        }

        const state = await draft.readResumeState(isLive)

        if (!isLive()) {
          return
        }

        if (!state) {
          resumedRef.current = false
          setResumeState('failed')

          return
        }

        setResumeState('ok')

        if (state.complete) {
          void clearDraftRefImage()
          onCompletedRef.current()

          return
        }

        if (state.answers) {
          const savedAnswers = state.answers
          setAnswers(local => mergeResumeAnswers(local, savedAnswers))
        }

        const target = resolveResumeTarget(state.next_field)

        if (!target) {
          return
        }

        let restoredPhase = target.phase

        if (state.next_field === 'portrait' || state.next_field === 'fullbody-reference') {
          restoredPhase = await portraitRef.current.restorePortrait(state.next_field, isLive)
        }

        if (!isLive()) {
          return
        }

        setPhase(restoredPhase)

        if (target.qIndex !== undefined) {
          setQIndex(target.qIndex)
        }

        if (target.imageSealed) {
          setImageSealed(true)
        }

        if (target.voiceStage) {
          setVoiceStage(target.voiceStage)
        }
      } catch (error) {
        if (isLive()) {
          log.warn('onboarding', 'resume failed', error)
          resumedRef.current = false
          setResumeState('failed')
        }
      } finally {
        pending = false
      }
    })()

    return () => {
      cancelled = true

      if (pending) {
        resumedRef.current = false
      }
    }
  }, [begin, draft, gatewayState, onCompletedRef, portraitRef, resumeAttempt])

  const commit = (value: string | undefined): OnboardingAnswers => {
    if (!question) {
      return answers
    }

    const trimmed = value?.trim() || undefined
    const cleaned = trimmed && question.max ? trimmed.slice(0, question.max) : trimmed
    const nextAnswers = { ...answers, [question.key]: cleaned }
    setAnswers(nextAnswers)

    if (gatewayState === 'open') {
      void draft.submit(question.key, cleaned ?? null)
    }

    return nextAnswers
  }

  const enterPortraitStage = async (currentAnswers: OnboardingAnswers): Promise<void> => {
    if (imageSealed || savingCharacterRef.current) {
      return
    }

    const isLive = begin()
    savingCharacterRef.current = true
    setHint(null)

    try {
      const failure = await draft.saveCharacter(currentAnswers)

      if (!isLive()) {
        return
      }

      if (failure) {
        setHint(failure)

        return
      }

      setPhase('portrait-generate')
    } finally {
      savingCharacterRef.current = false
    }
  }

  const finish = async (currentAnswers: OnboardingAnswers): Promise<void> => {
    const isLive = begin()

    const selectedVoice = currentAnswers.voice_setup_skipped === 'true' ? null : (currentAnswers.voice ?? null)

    const failure = await draft.saveComplete(currentAnswers, selectedVoice)

    if (!isLive()) {
      return
    }

    if (failure) {
      setHint(failure)
      setPhase('q-user')

      return
    }

    portrait.updateRefImage(null)
    onCompletedRef.current()
  }

  const advance = (currentAnswers: OnboardingAnswers): void => {
    if (phase === 'voice' && voiceStage === 'describe') {
      setVoiceStage('catalog')

      return
    }

    if (qIndex < currentList.length - 1) {
      setQIndex(qIndex + 1)
    } else if (phase === 'q-character') {
      void enterPortraitStage(currentAnswers)
    }
  }

  const onSend = (): void => {
    if (question?.requiredHint && !input.trim()) {
      setHint(question.requiredHint)

      return
    }

    advance(commit(input))
  }

  const onSkip = (): void => {
    if (!question?.requiredHint) {
      advance(commit(undefined))
    }
  }

  const backIntent = computeBackTransition(
    { phase, qIndex, voiceStage, imageSealed, portraitDirectAdopt: portrait.portraitDirectAdopt },
    CHARACTER_QUESTIONS.length
  )

  const onBack = (): void => {
    if (!backIntent) {
      return
    }

    setPhase(backIntent.phase)

    if (backIntent.qIndex !== undefined) {
      setQIndex(backIntent.qIndex)
    }

    if (backIntent.voiceStage !== undefined) {
      setVoiceStage(backIntent.voiceStage)
    }
  }

  const confirmVoice = async (skip = false, voiceId = '', label = '', serverConfirmed = false): Promise<void> => {
    if (voiceSaving || (!skip && !voiceId)) {
      return
    }

    const isLive = begin()
    setVoiceSaving(true)
    setHint(null)
    const selected = skip ? null : label || voiceId
    const saved = await draft.submit(skip ? 'voice_setup_skipped' : 'voice', skip ? 'true' : selected)

    if (!isLive()) {
      return
    }

    setVoiceSaving(false)

    if (!saved) {
      setHint('选择未能保存，请检查网络后重试。')

      return
    }

    setAnswers(previous => ({
      ...previous,
      voice: selected ?? undefined,
      voice_setup_skipped: skip ? 'true' : undefined
    }))

    if (skip) {
      setCompanionVoiceId('')
      setResponsePreference('text')
    } else if (serverConfirmed) {
      await applyServerCompanionVoiceId(voiceId)
    } else {
      setCompanionVoiceId(voiceId)
    }

    if (!isLive()) {
      return
    }

    setPhase('q-user')
    setQIndex(0)
  }

  const isQuestionPhase = phase === 'q-character' || (phase === 'voice' && voiceStage === 'describe')

  const progressStep =
    phase === 'q-character' ? 0 : phase === 'voice' ? 2 : phase === 'q-user' || phase === 'finishing' ? 3 : 1

  return (
    <div className="fixed inset-0 z-50 pointer-events-none" style={{ pointerEvents: 'none' }}>
      <DraggableDialog>
        <div
          className={`max-h-[85vh] w-full overflow-y-auto rounded-2xl p-5 text-strong ${SURFACE_OVERLAY}`}
          style={{ pointerEvents: 'auto' }}
        >
          <ol aria-label="认识伙伴的进度" className="mb-5 flex items-center gap-2 border-b border-line-hairline pb-4">
            {['认识伙伴', '确认形象', '选择声音'].map((label, index) => (
              <li
                aria-current={progressStep === index ? 'step' : undefined}
                className="flex min-w-0 flex-1 items-center gap-1.5 text-[11px]"
                key={label}
              >
                <span
                  className={`grid size-5 shrink-0 place-items-center rounded-full ${progressStep >= index ? 'bg-accent text-on-accent' : 'bg-fill-trough text-muted'}`}
                >
                  {index + 1}
                </span>
                <span className={progressStep === index ? 'text-strong' : 'text-muted'}>{label}</span>
              </li>
            ))}
          </ol>
          {resumeState !== 'ok' && (
            <div className="py-2 text-center">
              <p className="text-sm text-body">没能读取之前保存的进度，请检查网络后重试。</p>
              <button
                className={`mt-3 ${ONBOARDING_PRIMARY_BUTTON}`}
                disabled={resumeState === 'retrying'}
                onClick={() => {
                  setResumeState('retrying')
                  setResumeAttempt(attempt => attempt + 1)
                }}
                type="button"
              >
                {resumeState === 'retrying' ? '正在重试…' : '重试'}
              </button>
            </div>
          )}
          {resumeState === 'ok' && isQuestionPhase && question && (
            <QuestionPage
              answerKind={answerKind}
              canGoBack={backIntent !== null}
              hint={hint}
              input={input}
              inputRef={inputRef}
              lockedLabel={phase === 'q-character' ? LOCKED_FIELD_LABELS[question.key] : undefined}
              onBack={onBack}
              onSend={onSend}
              onSkip={onSkip}
              qIndex={qIndex}
              question={question}
              questionCount={currentList.length}
              setAnswerKind={setAnswerKind}
              setInput={setInput}
              textareaRef={textareaRef}
            />
          )}
          <PortraitPages
            answers={answers}
            hint={hint}
            onBack={onBack}
            phase={phase}
            portrait={portrait}
            setHint={setHint}
            setPhase={setPhase}
          />
          {phase === 'voice' && voiceStage === 'catalog' && (
            <VoicePage
              confirming={voiceSaving}
              error={hint}
              name={answers.name ?? ''}
              onContinueText={() => void confirmVoice(true)}
              onDescribe={() => setVoiceStage('describe')}
              onVoiceSelected={(voiceId, label, serverConfirmed) =>
                confirmVoice(false, voiceId, label, serverConfirmed)
              }
              preference={answers.voice}
            />
          )}
          {phase === 'q-user' && (
            <div className="space-y-4">
              <div>
                <h2 className="text-base font-semibold text-strong">伙伴准备好了</h2>
                <p className="mt-1 text-xs leading-relaxed text-body">
                  可以开始聊天了。也可以先告诉伙伴一些关于您的事，之后随时都能补充。
                </p>
              </div>
              <details className="rounded-xl border border-line-hairline bg-surface-card p-3">
                <summary className="cursor-pointer text-sm text-body">让伙伴更了解我（可选）</summary>
                <div className="mt-4 space-y-3">
                  {USER_QUESTIONS.map(item => (
                    <label className="block space-y-1.5 text-xs text-body" key={item.key}>
                      <span>{item.text}</span>
                      {item.multiline ? (
                        <textarea
                          className={INPUT_CLASS}
                          maxLength={item.max}
                          onBlur={() => void draft.submit(item.key, answers[item.key]?.trim() || null)}
                          onChange={event => setAnswers(previous => ({ ...previous, [item.key]: event.target.value }))}
                          placeholder={item.placeholder}
                          rows={2}
                          value={answers[item.key] ?? ''}
                        />
                      ) : (
                        <input
                          className={INPUT_CLASS}
                          maxLength={item.max}
                          onBlur={() => void draft.submit(item.key, answers[item.key]?.trim() || null)}
                          onChange={event => setAnswers(previous => ({ ...previous, [item.key]: event.target.value }))}
                          placeholder={item.placeholder}
                          type={item.date ? 'date' : 'text'}
                          value={answers[item.key] ?? ''}
                        />
                      )}
                    </label>
                  ))}
                </div>
              </details>
              {hint && (
                <p className="text-xs text-danger-fg" role="alert">
                  {hint}
                </p>
              )}
              <div className="flex items-center justify-between gap-3">
                <button className={BTN_SUBTLE} onClick={onBack} type="button">
                  选择声音
                </button>
                <button
                  className={BTN_PRIMARY}
                  onClick={() => {
                    setHint(null)
                    setPhase('finishing')
                    void finish(answers)
                  }}
                  type="button"
                >
                  开始相伴
                </button>
              </div>
            </div>
          )}
          {phase === 'finishing' && <p className="py-6 text-center text-sm text-strong">正在保存资料…</p>}
        </div>
      </DraggableDialog>
    </div>
  )
}
