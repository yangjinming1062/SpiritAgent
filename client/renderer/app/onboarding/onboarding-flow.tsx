import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { clearDraftRefImage, type OnboardingAnswers } from '@/modules/character'
import { stopSpeaking, warmAudioContext } from '@/modules/speech'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { log } from '@/shared/lib/log'
import { SURFACE_OVERLAY } from '@/shared/panel'
import { $gatewayState } from '@/shared/store/gateway'

import { computeBackTransition, type Phase, type VoiceStage } from './back-transition'
import { playOnboardingAudio } from './onboarding-audio'
import { DraggableDialog } from './onboarding-components'
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
import { useOnboardingVoice } from './use-onboarding-voice'
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
  const [resumeState, setResumeState] = useState<'failed' | 'ok' | 'retrying'>('ok')
  const [resumeAttempt, setResumeAttempt] = useState(0)
  const resumedRef = useRef(false)
  const savingCharacterRef = useRef(false)
  const carriedHintRef = useRef<string | null>(null)
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

  const enterVoiceDescribe = (): void => {
    setImageSealed(true)
    setPhase('voice')
    setVoiceStage('describe')
    setQIndex(0)
  }

  const portrait = useOnboardingPortrait({ begin, imageSealed, setHint, setPhase, enterVoiceDescribe })
  const portraitRef = useLatestRef(portrait)
  const voice = useOnboardingVoice({ phase, voiceStage, answers, begin })

  useEffect(() => {
    if (phase !== 'q-character' && phase !== 'q-user' && !(phase === 'voice' && voiceStage === 'describe')) {
      return
    }

    const current = currentList[qIndex]

    if (!current) {
      return
    }

    const carriedHint = carriedHintRef.current
    carriedHintRef.current = null
    setInput(answersRef.current[current.key] ?? '')
    setAnswerKind(null)
    setHint(carriedHint)

    if (!carriedHint) {
      void playOnboardingAudio(current.audioTag)
    }

    return () => stopSpeaking()
  }, [phase, voiceStage, qIndex, currentList, answersRef])

  useEffect(() => {
    if ((phase === 'q-character' || phase === 'q-user' || phase === 'voice') && question) {
      ;(question.multiline ? textareaRef.current : inputRef.current)?.focus()
    }
  }, [phase, question])

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

      setPhase('portrait-choose')
    } finally {
      savingCharacterRef.current = false
    }
  }

  const finish = async (currentAnswers: OnboardingAnswers): Promise<void> => {
    const isLive = begin()
    const failure = await draft.saveComplete(currentAnswers, voice.voice ? voice.voice.label || voice.voice.id : null)

    if (!isLive()) {
      return
    }

    if (failure) {
      carriedHintRef.current = failure
      setPhase('q-user')
      setQIndex(USER_QUESTIONS.length - 1)

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
    } else if (phase === 'q-user') {
      setPhase('finishing')
      void finish(currentAnswers)
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

  const confirmVoice = (): void => {
    if (voice.voice) {
      const selected = voice.voice.label || voice.voice.id
      setAnswers(previous => ({ ...previous, voice: selected }))
      void draft.submit('voice', selected)
    }

    setPhase('q-user')
    setQIndex(0)
  }

  const isQuestionPhase =
    phase === 'q-character' || phase === 'q-user' || (phase === 'voice' && voiceStage === 'describe')

  return (
    <div className="fixed inset-0 z-50 pointer-events-none" style={{ pointerEvents: 'none' }}>
      <DraggableDialog>
        <div className={`w-full rounded-2xl p-5 text-strong ${SURFACE_OVERLAY}`} style={{ pointerEvents: 'auto' }}>
          {voice.voicePreparing && <p className="mb-2 text-center text-[10px] text-muted">正在准备声音…</p>}
          {resumeState !== 'ok' && (
            <div className="py-2 text-center">
              <p className="text-sm text-body">没能读取之前保存的进度，请检查网络后重试。</p>
              <button
                className="mt-3 inline-flex h-8 items-center justify-center rounded-lg bg-accent px-4 text-xs font-medium text-on-accent transition hover:bg-accent/85 disabled:pointer-events-none disabled:opacity-40"
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
            <VoicePage confirmVoice={confirmVoice} onDescribe={() => setVoiceStage('describe')} voice={voice} />
          )}
          {phase === 'finishing' && <p className="py-6 text-center text-sm text-strong">正在保存资料…</p>}
        </div>
      </DraggableDialog>
    </div>
  )
}
