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
import { DraggableDialog, ONBOARDING_PRIMARY_BUTTON, OnboardingProgress } from './onboarding-components'
import { OnboardingDraft, type OnboardingPersonaConflict } from './onboarding-draft'
import { CHARACTER_QUESTIONS, mergeResumeAnswers, resolveResumeTarget, VOICE_QUESTION } from './onboarding-questions'
import { PortraitPages } from './portrait-pages'
import { onboardingProfileDraft } from './profile-draft'
import { ProfilePage } from './profile-page'
import { QuestionPage } from './question-page'
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
  const [hint, setHint] = useState<string | null>(null)
  const [personaConflict, setPersonaConflict] = useState<OnboardingPersonaConflict | null>(null)

  const reviewPersonaConflict = (): void => {
    draft.reviewPersonaConflict()
    setPersonaConflict(null)
    setHint(null)
  }

  const [voiceSaving, setVoiceSaving] = useState(false)
  const [characterSaving, setCharacterSaving] = useState(false)
  const [resumeState, setResumeState] = useState<'reading' | 'failed' | 'ok' | 'retrying'>('reading')
  const [resumeAttempt, setResumeAttempt] = useState(0)
  const resumedRef = useRef(false)
  const savingCharacterRef = useRef(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const answersRef = useLatestRef(answers)
  const onCompletedRef = useLatestRef(onCompleted)
  const isQuestionPhase = phase === 'q-character' || (phase === 'voice' && voiceStage === 'describe')
  const question = phase === 'voice' ? VOICE_QUESTION : CHARACTER_QUESTIONS[qIndex]

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
    if (resumeState !== 'ok' || !isQuestionPhase || !question) {
      return
    }

    setInput(answersRef.current[question.key] ?? '')
    setHint(null)
    ;(question.multiline ? textareaRef.current : inputRef.current)?.focus()
  }, [isQuestionPhase, question, answersRef, resumeState])

  // 草稿读取失败暂停作答；连通后只恢复一次，取消的读取可在重连时继续。
  useEffect(() => {
    if (resumedRef.current) {
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

        if (state.complete) {
          onboardingProfileDraft.reset()
          void saveCreationDraftImage('avatar', null)
          onCompletedRef.current()

          return
        }

        setResumeState('ok')
        setAnswers(local => mergeResumeAnswers(local, state.answers))

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

  const commit = (value: string | undefined): void => {
    if (!question) {
      return
    }

    const trimmed = value?.trim() || undefined
    const cleaned = trimmed?.slice(0, question.max)
    setAnswers(current => ({ ...current, [question.key]: cleaned }))

    if (gatewayState === 'open' && question.key !== 'voice') {
      void draft.submit(question.key, cleaned ?? null)
    }
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

  const advance = (value: string | undefined): void => {
    commit(value)
    setStage(current => advanceStage(current, CHARACTER_QUESTIONS.length))
  }

  const onSend = (): void => {
    if (question && question.key !== 'voice' && !input.trim()) {
      setHint(t.requiredField(t.fields[question.key].label))
      ;(question.multiline ? textareaRef.current : inputRef.current)?.focus()

      return
    }

    advance(input)
  }

  const backIntent = computeBackTransition(
    { phase, qIndex, voiceStage, imageSealed, portraitDirectAdopt: portrait.portraitDirectAdopt },
    CHARACTER_QUESTIONS.length
  )

  const onBack = (): void => {
    if (!backIntent) {
      return
    }

    setHint(null)
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

    if (saved.complete) {
      finish()
    } else {
      setStage(current => ({
        ...current,
        phase: saved.next_field === 'user_profile' ? 'user_profile' : 'environment',
        qIndex: 0
      }))
    }

    return true
  }

  const finish = (): void => {
    onboardingProfileDraft.reset()
    portrait.updateRefImage(null)
    onCompletedRef.current()
  }

  const progressStep =
    phase === 'q-character' || phase === 'background'
      ? 0
      : phase === 'voice'
        ? 2
        : phase === 'environment'
          ? 3
          : phase === 'user_profile'
            ? 4
            : 1

  return (
    <div className="fixed inset-0 z-50 pointer-events-none" style={{ pointerEvents: 'none' }}>
      <DraggableDialog>
        <OnboardingProgress step={progressStep} />
        {resumeState === 'reading' && (
          <p className="py-2 text-center text-sm text-body" role="status">
            {strings.common.loading}
          </p>
        )}
        {(resumeState === 'failed' || resumeState === 'retrying') && (
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
            canGoBack={backIntent !== null}
            hint={hint}
            input={input}
            inputRef={inputRef}
            onBack={onBack}
            onSend={onSend}
            onSkip={() => advance(undefined)}
            qIndex={qIndex}
            question={question}
            questionCount={phase === 'voice' ? 1 : CHARACTER_QUESTIONS.length}
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
              const next = skip
                ? { ...answers, gender: '', personality: '', relationship: '', age: '', occupation: '', background: '' }
                : answers

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
            onBack={onBack}
            onContinue={complete => {
              if (complete) {
                finish()

                return
              }

              setHint(null)
              setStage(current => ({ ...current, phase: 'user_profile' }))
            }}
          />
        )}
        {phase === 'user_profile' && <ProfilePage onBack={onBack} onCompleted={finish} />}
      </DraggableDialog>
    </div>
  )
}
