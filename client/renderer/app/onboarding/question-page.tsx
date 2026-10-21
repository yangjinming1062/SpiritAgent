import { type KeyboardEvent, type RefObject, useId } from 'react'

import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, Chip, CreationActionBar, CreationWorkspace, DatePicker, INPUT_CLASS } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { ONBOARDING_PRIMARY_BUTTON } from './onboarding-components'
import type { AnswerKind, Question } from './onboarding-questions'
import styles from './onboarding.module.css'

interface QuestionPageProps {
  question: Question
  qIndex: number
  questionCount: number
  lockedLabel?: string
  input: string
  answerKind: AnswerKind | null
  hint: string | null
  inputRef: RefObject<HTMLInputElement | null>
  textareaRef: RefObject<HTMLTextAreaElement | null>
  setInput: (value: string) => void
  setAnswerKind: (kind: AnswerKind | null) => void
  canGoBack: boolean
  onBack: () => void
  onSend: () => void
  onSkip: () => void
}

export function QuestionPage({
  question,
  qIndex,
  questionCount,
  lockedLabel,
  input,
  answerKind,
  hint,
  inputRef,
  textareaRef,
  setInput,
  setAnswerKind,
  canGoBack,
  onBack,
  onSend,
  onSkip
}: QuestionPageProps): React.JSX.Element {
  const t = useStrings().onboarding
  const id = useId()
  const presetValues = question.presets ?? []

  const nextLabel = question.key === 'voice' ? t.nextVoice : qIndex === questionCount - 1 ? t.nextConfirm : t.next

  const descriptionIds = [hint && `${id}-error`, lockedLabel && `${id}-locked`].filter(Boolean).join(' ') || undefined

  const focusInput = (): void => {
    ;(question.multiline ? textareaRef.current : inputRef.current)?.focus()
  }

  const selectPreset = (value: string): void => {
    setInput(value)
    focusInput()
  }

  const onAnswerKeyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
    if (
      event.key !== 'Enter' ||
      event.nativeEvent.isComposing ||
      event.nativeEvent.keyCode === 229 ||
      event.shiftKey ||
      event.ctrlKey ||
      event.altKey ||
      event.metaKey
    ) {
      return
    }

    event.preventDefault()

    if (!event.repeat) {
      onSend()
    }
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
              {!question.requiredHint && (
                <button className={BTN_GHOST} onClick={onSkip} type="button">
                  {t.skip}
                </button>
              )}
              <button
                className={ONBOARDING_PRIMARY_BUTTON}
                disabled={Boolean(question.requiredHint && !input.trim())}
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
            {question.text}
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
          {question.kinds && (
            <div className={styles.options}>
              {question.kinds.map(k => (
                <Chip
                  active={answerKind?.chip === k.chip}
                  key={k.chip}
                  label={k.chip}
                  onClick={() => {
                    setAnswerKind(k)
                    setInput('')
                    focusInput()
                  }}
                />
              ))}
            </div>
          )}
          {answerKind && (
            <>
              <p className="text-xs text-body">{answerKind.label}</p>
              {answerKind.values && (
                <div className={styles.options}>
                  {answerKind.values.map(v => (
                    <Chip active={input === v} key={v} label={v} onClick={() => selectPreset(v)} />
                  ))}
                </div>
              )}
            </>
          )}
          {lockedLabel && (
            <p className="text-xs leading-relaxed text-body" id={`${id}-locked`}>
              {t.lockedHint(lockedLabel)}
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
              placeholder={question.placeholder}
              ref={textareaRef}
              rows={3}
              value={input}
            />
          ) : question.date ? (
            <DatePicker
              clearLabel={t.removeReference}
              onChange={setInput}
              placeholder={question.placeholder}
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
              placeholder={answerKind?.placeholder ?? question.placeholder}
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
