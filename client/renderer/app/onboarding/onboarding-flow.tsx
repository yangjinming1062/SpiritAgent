import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  applyServerCompanionVoiceId,
  type OnboardingAnswers,
  setCompanionVoiceId,
  setResponsePreference
} from '@/features/character'
import { stopSpeaking } from '@/features/speech'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { saveCreationDraftImage } from '@/shared/lib/creation-draft-image'
import { log } from '@/shared/lib/log'
import { $gatewayState } from '@/shared/store/gateway'
import { useStrings } from '@/shared/strings'

import { computeBackTransition } from './back-transition'
import { BackgroundPage } from './background-page'
import { EnvironmentPage } from './environment-page'
import { playOnboardingAudio } from './onboarding-audio'
import { DraggableDialog, ONBOARDING_PRIMARY_BUTTON, OnboardingProgress } from './onboarding-components'
import { OnboardingDraft, type OnboardingPersonaConflict } from './onboarding-draft'
import {
  type AnswerKind,
  CHARACTER_QUESTIONS,
  LOCKED_FIELD_LABELS,
  mergeResumeAnswers,
  PHASE_QUESTIONS,
  resolveResumeTarget
} from './onboarding-questions'
import { PortraitPages } from './portrait-pages'
import { QuestionPage } from './question-page'
import { ReadyPage } from './ready-page'
import {
  advanceStage,
  backStage,
  enterVoiceSelection,
  initialOnboardingStage,
  type OnboardingStageState,
  resumeStage
} from './stage'
import { useOnboardingPortrait } from './use-onboarding-portrait'
import { VoicePage } from './voice-page'

interface OnboardingFlowProps {
  onCompleted: () => void
}

// 导航与未提交表单归引导视图；持久化和资产操作由专属所有者执行。
export function OnboardingFlow({ onCompleted }: OnboardingFlowProps): React.JSX.Element | null {
  const strings = useStrings()
  const t = strings.onboarding
  const gatewayState = useStore($gatewayState)
  const [draft] = useState(() => new OnboardingDraft())
  const begin = useCallback(() => draft.captureOperation(), [draft])
  const [stage, setStage] = useState<OnboardingStageState>(initialOnboardingStage)
  const { imageSealed, phase, qIndex, voiceStage } = stage
  const [answers, setAnswers] = useState<OnboardingAnswers>({})
  const [input, setInput] = useState('')
  const [answerKind, setAnswerKind] = useState<AnswerKind | null>(null)
  const [hint, setHint] = useState<string | null>(null)
  const [personaConflict, setPersonaConflict] = useState<OnboardingPersonaConflict | null>(null)

  const reviewPersonaConflict = (): void => {
    draft.reviewPersonaConflict()
    setPersonaConflict(null)
    setHint(null)
  }

  const [voiceSaving, setVoiceSaving] = useState(false)
  const [characterSaving, setCharacterSaving] = useState(false)
  const [environmentBusy, setEnvironmentBusy] = useState(false)
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

    return () => {
      draft.dispose()
      stopSpeaking()
    }
  }, [draft])

  const enterVoiceStage = (): void => {
    setStage(current => enterVoiceSelection(current))
  }

  const portrait = useOnboardingPortrait({
    begin,
    imageSealed,
    setHint,
    setPhase: phase => setStage(current => ({ ...current, phase })),
    enterVoiceSelection: enterVoiceStage
  })

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
    if (
      resumeState === 'ok' &&
      (phase === 'q-character' || (phase === 'voice' && voiceStage === 'describe')) &&
      question
    ) {
      ;(question.multiline ? textareaRef.current : inputRef.current)?.focus()
    }
  }, [phase, question, resumeState, voiceStage])

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
          void saveCreationDraftImage('avatar', null)
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

        setStage(current => resumeStage(current, restoredPhase, target))
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
    setCharacterSaving(true)
    setHint(null)

    try {
      const failure = await draft.saveCharacter(currentAnswers)

      if (!isLive()) {
        return
      }

      if (failure) {
        setHint(failure)
        setPersonaConflict(draft.personaConflict())

        return
      }

      setStage(current => ({ ...current, phase: 'portrait-generate' }))
    } finally {
      savingCharacterRef.current = false

      if (isLive()) {
        setCharacterSaving(false)
      }
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
      setPersonaConflict(draft.personaConflict())
      setStage(current => ({ ...current, phase: 'q-user' }))

      return
    }

    portrait.updateRefImage(null)
    onCompletedRef.current()
  }

  const advance = (_currentAnswers: OnboardingAnswers): void => {
    setStage(current => advanceStage(current, currentList.length))
  }

  const onSend = (): void => {
    if (question?.requiredHint && !input.trim()) {
      setHint(question.requiredHint)
      ;(question.multiline ? textareaRef.current : inputRef.current)?.focus()

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

    setStage(current => backStage(current, backIntent))
  }

  const confirmVoice = async (
    skip = false,
    voiceId = '',
    label = '',
    serverConfirmed = false,
    isSelectionCurrent: () => boolean = () => true
  ): Promise<boolean> => {
    if (voiceSaving || (!skip && !voiceId) || !isSelectionCurrent()) {
      return false
    }

    const mounted = begin()
    const isLive = (): boolean => mounted() && isSelectionCurrent()
    setVoiceSaving(true)
    setHint(null)
    const selected = skip ? null : label || voiceId
    const saved = await draft.submit(skip ? 'voice_setup_skipped' : 'voice', skip ? 'true' : selected)

    if (mounted()) {
      setVoiceSaving(false)
    }

    if (!isLive()) {
      return false
    }

    if (!saved) {
      setHint(t.saveFailed)

      return false
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
      return false
    }

    setStage(current => ({ ...current, phase: 'environment', qIndex: 0 }))

    return true
  }

  const isQuestionPhase = phase === 'q-character' || (phase === 'voice' && voiceStage === 'describe')

  const progressStep =
    phase === 'q-character' || phase === 'background'
      ? 0
      : phase === 'voice'
        ? 2
        : phase === 'environment'
          ? 3
          : phase === 'q-user' || phase === 'finishing'
            ? 4
            : 1

  return (
    <div className="fixed inset-0 z-50 pointer-events-none" style={{ pointerEvents: 'none' }}>
      <DraggableDialog>
        <OnboardingProgress step={progressStep} />
        {resumeState !== 'ok' && (
          <div className="py-2 text-center">
            <p className="text-sm text-body">{t.resumeFailed}</p>
            <button
              className={`mt-3 ${ONBOARDING_PRIMARY_BUTTON}`}
              disabled={resumeState === 'retrying'}
              onClick={() => {
                setResumeState('retrying')
                setResumeAttempt(attempt => attempt + 1)
              }}
              type="button"
            >
              {resumeState === 'retrying' ? t.retrying : strings.common.retry}
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
            setInput={value => {
              setInput(value)
              setHint(null)
            }}
            textareaRef={textareaRef}
          />
        )}
        {resumeState === 'ok' && phase === 'background' && (
          <BackgroundPage
            answers={answers}
            busy={characterSaving}
            conflict={personaConflict}
            hint={hint}
            onBack={onBack}
            onBlur={key => {
              void draft.submit(key, answersRef.current[key] || null)
            }}
            onChange={(key, value) => {
              setAnswers(previous => ({ ...previous, [key]: value }))
              setHint(null)
            }}
            onContinue={skip => {
              const next = skip ? { ...answers, age: '', occupation: '', background: '' } : answers
              setAnswers(next)
              void enterPortraitStage(next)
            }}
            onReviewConflict={reviewPersonaConflict}
          />
        )}
        <PortraitPages
          answers={answers}
          hint={hint}
          onBack={onBack}
          phase={phase}
          portrait={portrait}
          setHint={setHint}
          setPhase={phase => setStage(current => ({ ...current, phase }))}
        />
        {phase === 'voice' && voiceStage === 'catalog' && (
          <VoicePage
            confirming={voiceSaving}
            error={hint}
            name={answers.name ?? ''}
            onContinueText={() => void confirmVoice(true)}
            onDescribe={() => setStage(current => ({ ...current, voiceStage: 'describe' }))}
            onVoiceSelected={(voiceId, label, serverConfirmed, isSelectionCurrent) =>
              confirmVoice(false, voiceId, label, serverConfirmed, isSelectionCurrent)
            }
            preference={answers.voice}
          />
        )}
        {phase === 'environment' && (
          <EnvironmentPage
            busy={environmentBusy}
            error={hint}
            onBack={onBack}
            onBusyChange={setEnvironmentBusy}
            onCompleted={() => {
              setHint(null)
              setStage(current => ({ ...current, phase: 'q-user', qIndex: 0 }))
            }}
            onError={message => setHint(message)}
          />
        )}
        {phase === 'q-user' && (
          <ReadyPage
            answers={answers}
            draft={draft}
            hint={hint}
            onBack={onBack}
            onFinish={() => {
              setHint(null)
              setStage(current => ({ ...current, phase: 'finishing' }))
              void finish(answers)
            }}
            onReviewConflict={reviewPersonaConflict}
            personaConflict={personaConflict}
            setAnswers={setAnswers}
          />
        )}
        {phase === 'finishing' && <p className="py-6 text-center text-sm text-strong">{t.savingProfile}</p>}
      </DraggableDialog>
    </div>
  )
}
