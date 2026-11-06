import type { Dispatch, SetStateAction } from 'react'
import type React from 'react'

import type { OnboardingAnswers } from '@/features/character'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, CreationActionBar, CreationWorkspace, Disclosure, INPUT_CLASS } from '@/ui'

import { OnboardingConflictNotice } from './background-page'
import { ONBOARDING_PRIMARY_BUTTON } from './onboarding-components'
import type { OnboardingDraft, OnboardingPersonaConflict } from './onboarding-draft'
import { USER_QUESTIONS } from './onboarding-questions'
import styles from './onboarding.module.css'

/** 收尾页：确认称呼与资料，进入保存；保存中与失败的展示由外层按阶段切换。 */
export function ReadyPage({
  answers,
  draft,
  hint,
  personaConflict,
  onBack,
  onFinish,
  onReviewConflict,
  setAnswers
}: {
  answers: OnboardingAnswers
  draft: OnboardingDraft
  hint: string | null
  personaConflict: OnboardingPersonaConflict | null
  onBack: () => void
  onFinish: () => void
  onReviewConflict: () => void
  setAnswers: Dispatch<SetStateAction<OnboardingAnswers>>
}): React.JSX.Element {
  const t = useStrings().onboarding

  return (
    <CreationWorkspace
      footer={
        <CreationActionBar>
          <div className={styles.navigation}>
            <button className={BTN_SUBTLE} onClick={onBack} type="button">
              {t.stepVoice}
            </button>
            <button
              className={`ml-auto ${ONBOARDING_PRIMARY_BUTTON}`}
              disabled={personaConflict !== null}
              onClick={onFinish}
              type="button"
            >
              {t.stepCompanion}
            </button>
          </div>
        </CreationActionBar>
      }
    >
      <div className="space-y-4">
        <div>
          <h2 className={styles.questionTitle}>{t.readyTitle}</h2>
          <p className="mt-1 text-xs leading-relaxed text-body">{t.readyHint}</p>
        </div>
        <Disclosure
          bodyClassName="mt-4 space-y-3"
          className="rounded-xl border border-line-hairline bg-surface-card p-3"
          summaryClassName="text-sm text-body"
          title={t.knowMore}
          variant="bordered"
        >
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
        </Disclosure>
        {hint && (
          <p className="text-xs text-danger-fg" role="alert">
            {hint}
          </p>
        )}
        <OnboardingConflictNotice conflict={personaConflict} onReview={onReviewConflict} />
      </div>
    </CreationWorkspace>
  )
}
