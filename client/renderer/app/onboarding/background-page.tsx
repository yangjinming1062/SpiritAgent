import { useEffect, useRef } from 'react'

import type { OnboardingAnswers } from '@/features/character'
import { useStrings } from '@/shared/strings'
import {
  BTN_GHOST,
  CreationActionBar,
  CreationFeedback,
  CreationWorkspace,
  FIELD_LABEL,
  HINT_TEXT,
  INPUT_CLASS
} from '@/ui'

import { submitOnEnter } from './keyboard'
import { ONBOARDING_PRIMARY_BUTTON } from './onboarding-components'
import { ONBOARDING_PERSONA_FIELDS, type OnboardingPersonaConflict } from './onboarding-draft'
import styles from './onboarding.module.css'

type BackgroundField = 'age' | 'occupation' | 'background'

export function OnboardingConflictNotice({
  conflict,
  onReview,
  busy = false
}: {
  conflict: OnboardingPersonaConflict | null
  onReview: () => void
  busy?: boolean
}): React.JSX.Element | null {
  const strings = useStrings()
  const t = strings.settings.persona

  if (!conflict) {
    return null
  }

  const labels = {
    name: t.nameLabel,
    biological_type: t.speciesLabel,
    gender: t.genderLabel,
    relationship: t.relationshipLabel,
    personality: t.personalityLabel,
    speaking_style: t.speakingStyleLabel,
    age: t.ageLabel,
    occupation: t.occupationLabel,
    background: t.backgroundLabel
  }

  return (
    <div className="rounded-xl border border-line-hairline p-3 text-sm text-body" role="alert">
      <p>{t.profileConflictHint}</p>
      <dl className="my-3 space-y-2">
        {ONBOARDING_PERSONA_FIELDS.filter(key => key in conflict).map(key => (
          <div key={key}>
            <dt className={FIELD_LABEL}>{labels[key]}</dt>
            <dd className="whitespace-pre-wrap break-words">{conflict[key] || '—'}</dd>
          </div>
        ))}
      </dl>
      <button className={BTN_GHOST} disabled={busy} onClick={onReview} type="button">
        {strings.common.keepMyEdits}
      </button>
    </div>
  )
}

export function BackgroundPage({
  answers,
  busy,
  hint,
  conflict,
  onReviewConflict,
  onChange,
  onBlur,
  onBack,
  onContinue
}: {
  answers: OnboardingAnswers
  busy: boolean
  hint: string | null
  conflict: OnboardingPersonaConflict | null
  onReviewConflict: () => void
  onChange: (key: BackgroundField, value: string) => void
  onBlur: (key: BackgroundField) => void
  onBack: () => void
  onContinue: (skip: boolean) => void
}): React.JSX.Element {
  const strings = useStrings()
  const t = strings.settings.persona
  const first = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    first.current?.focus()
  }, [])

  const fields: { key: BackgroundField; label: string; placeholder: string }[] = [
    { key: 'age', label: t.ageLabel, placeholder: t.agePlaceholder },
    { key: 'occupation', label: t.occupationLabel, placeholder: t.occupationPlaceholder },
    { key: 'background', label: t.backgroundLabel, placeholder: t.backgroundPlaceholder }
  ]

  return (
    <CreationWorkspace
      className={styles.questionWorkspace}
      footer={
        <CreationActionBar>
          <div className={styles.navigation}>
            <button className={BTN_GHOST} disabled={busy} onClick={onBack} type="button">
              {strings.onboarding.previousQuestion}
            </button>
            <div className={styles.forward}>
              <button
                className={BTN_GHOST}
                disabled={busy || !!conflict}
                onClick={() => onContinue(true)}
                type="button"
              >
                {t.skipBackground}
              </button>
              <button
                className={ONBOARDING_PRIMARY_BUTTON}
                disabled={busy || !!conflict}
                onClick={() => onContinue(false)}
                type="button"
              >
                {busy ? strings.common.saving : strings.onboarding.next}
              </button>
            </div>
          </div>
        </CreationActionBar>
      }
    >
      <div className={styles.question}>
        <h2 className={styles.questionTitle}>{t.backgroundTitle}</h2>
        <p className={HINT_TEXT}>{t.backgroundHint}</p>
        {fields.map(({ key, label, placeholder }, index) => (
          <label className="block space-y-1" key={key}>
            <span className={FIELD_LABEL}>{label}</span>
            <textarea
              className={INPUT_CLASS}
              disabled={busy}
              maxLength={key === 'background' ? 2000 : 500}
              onBlur={() => onBlur(key)}
              onChange={event => onChange(key, event.target.value)}
              onKeyDown={event => submitOnEnter(event, () => onContinue(false))}
              placeholder={placeholder}
              ref={index === 0 ? first : undefined}
              rows={key === 'background' ? 4 : 1}
              value={answers[key] ?? ''}
            />
          </label>
        ))}
        {hint && <CreationFeedback state="error" title={hint} />}
        <OnboardingConflictNotice busy={busy} conflict={conflict} onReview={onReviewConflict} />
      </div>
    </CreationWorkspace>
  )
}
