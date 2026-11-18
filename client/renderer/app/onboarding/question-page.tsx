import { type KeyboardEvent, type RefObject, useId } from 'react'

import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import { BTN_GHOST, Chip, CreationActionBar, CreationWorkspace, INPUT_CLASS } from '@/ui'

import { submitOnEnter } from './keyboard'
import { ONBOARDING_PRIMARY_BUTTON } from './onboarding-components'
import type { Question } from './onboarding-questions'
import styles from './onboarding.module.css'

interface QuestionPageProps {
  question: Question
  qIndex: number
  questionCount: number
  input: string
  hint: string | null
  inputRef: RefObject<HTMLInputElement | null>
  textareaRef: RefObject<HTMLTextAreaElement | null>
  setInput: (value: string) => void
  canGoBack: boolean
  onBack: () => void
  onSend: () => void
  onSkip: () => void
}

export function QuestionPage({
  question,
  qIndex,
  questionCount,
  input,
  hint,
  inputRef,
  textareaRef,
  setInput,
  canGoBack,
  onBack,
  onSend,
  onSkip
}: QuestionPageProps): React.JSX.Element {
  const t = useStrings().onboarding
  const id = useId()
  const presetValues = question.presets ?? []

  const nextLabel = question.key === 'voice' ? t.nextVoice : qIndex === questionCount - 1 ? t.nextConfirm : t.next

  const field = t.fields[question.key]
  const required = question.key !== 'voice'
  const species = question.key === 'biological_type'
  const descriptionIds = [hint && `${id}-error`, species && `${id}-locked`].filter(Boolean).join(' ') || undefined

  const focusInput = (): void => {
    ;(question.multiline ? textareaRef.current : inputRef.current)?.focus()
  }

  const selectPreset = (value: string): void => {
    setInput(value)
    focusInput()
  }

  const onAnswerKeyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
    submitOnEnter(event, onSend)
  }

  return (
    <CreationWorkspace
      className={styles.questionWorkspace}
      footer={
        <CreationActionBar>
          <div className={styles.navigation}>
            {canGoBack && (
              <button className={BTN_GHOST} onClick={onBack} type="button">
                {t.previousQuestion}
              </button>
            )}
            <div className={styles.forward}>
              {!required && (
                <button className={BTN_GHOST} onClick={onSkip} type="button">
                  {t.skip}
                </button>
              )}
              <button
                className={ONBOARDING_PRIMARY_BUTTON}
                disabled={Boolean(required && !input.trim())}
                onClick={onSend}
                type="button"
              >
                {nextLabel}
              </button>
            </div>
          </div>
        </CreationActionBar>
      }
    >
      <div className={styles.question}>
        <div>
          <div className={styles.questionMeta}>
            <span>{t.questionProgress(qIndex + 1, questionCount)}</span>
          </div>
          <h2 className={styles.questionTitle} id={`${id}-title`}>
            {field.label}
          </h2>
        </div>
        <div className={styles.answer}>
          {presetValues.length > 0 && (
            <div className={styles.options}>
              {presetValues.map(p => (
                <Chip active={input === p} key={p} label={p} onClick={() => selectPreset(p)} />
              ))}
            </div>
          )}
          {species && (
            <p className="text-xs leading-relaxed text-body" id={`${id}-locked`}>
              {t.lockedHint(field.label)}
            </p>
          )}
          {question.multiline ? (
            <textarea
              aria-describedby={descriptionIds}
              aria-invalid={Boolean(hint)}
              aria-labelledby={`${id}-title`}
              className={cn(INPUT_CLASS, 'text-sm')}
              maxLength={question.max}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onAnswerKeyDown}
              placeholder={field.placeholder}
              ref={textareaRef}
              rows={3}
              value={input}
            />
          ) : (
            <input
              aria-describedby={descriptionIds}
              aria-invalid={Boolean(hint)}
              aria-labelledby={`${id}-title`}
              className={cn(INPUT_CLASS, 'text-sm')}
              maxLength={question.max}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onAnswerKeyDown}
              placeholder={field.placeholder}
              ref={inputRef}
              value={input}
            />
          )}

          {hint && (
            <p className="text-xs text-danger-fg" id={`${id}-error`} role="alert">
              {hint}
            </p>
          )}
        </div>
      </div>
    </CreationWorkspace>
  )
}
