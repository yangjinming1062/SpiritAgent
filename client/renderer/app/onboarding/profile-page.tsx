import { useStore } from '@nanostores/react'
import { useState } from 'react'

import {
  confirmProfileChanges,
  conflictingProfileFields,
  PROFILE_FIELDS,
  profileChanges,
  profileChangesMatch,
  UserProfileForm,
  useUserProfileRows
} from '@/features/companion'
import { backendDetailMessage, ipcErrorStatus, isUncertainMutationIpc } from '@/shared/lib/ipc-error'
import { authedResource } from '@/shared/lib/resource-request'
import { useStrings } from '@/shared/strings'
import { BTN_GHOST, CreationActionBar, CreationFeedback, CreationWorkspace, ErrorBanner, HINT_TEXT } from '@/ui'

import type { ApiSchemas } from '../../../../shared/api.generated'

import { submitOnEnter } from './keyboard'
import { ONBOARDING_PRIMARY_BUTTON } from './onboarding-components'
import styles from './onboarding.module.css'
import { onboardingProfileDraft } from './profile-draft'
import { useOnboardingAction } from './use-onboarding-action'

type Submission = Required<ApiSchemas['OnboardingProfileRequest']>

export function ProfilePage({
  onBack,
  onCompleted
}: {
  onBack: () => void
  onCompleted: () => void
}): React.JSX.Element {
  const dict = useStrings()
  const t = dict.onboarding
  const drafts = useStore(onboardingProfileDraft.$atom)
  const { rows, loading, loadError, load } = useUserProfileRows()
  const { busy, error, setError, run } = useOnboardingAction()
  const [reviewingSaved, setReviewingSaved] = useState(false)
  const [unconfirmed, setUnconfirmed] = useState<Submission | null>(null)
  const conflicts = conflictingProfileFields(rows, drafts)

  const filled = PROFILE_FIELDS.some(key =>
    (drafts[key]?.content ?? rows?.find(row => row.slot_key === key)?.content ?? '').trim()
  )

  const canSave = !!rows && !loading && filled && !conflicts.length

  const checkResult = async (body: Submission, isLive: () => boolean, failure: string): Promise<void> => {
    const state = await authedResource('onboarding.get_onboarding')

    if (!isLive() || !state.ok || !state.value) {
      return
    }

    if (!state.value.complete) {
      setUnconfirmed(null)
      setError(failure)

      if (body.decision === 'save') {
        await load(true)
      }

      return
    }

    if (body.decision === 'skip') {
      onCompleted()

      return
    }

    const latest = await load(true)

    if (!isLive() || !latest) {
      return
    }

    setUnconfirmed(null)

    if (state.value.profile_outcome === 'saved' && profileChangesMatch(latest, body.changes)) {
      onCompleted()
    } else {
      onboardingProfileDraft.reset()
      setReviewingSaved(true)
    }
  }

  const finish = async (decision: Submission['decision']): Promise<void> => {
    if (reviewingSaved || (!unconfirmed && decision === 'save' && !canSave)) {
      return
    }

    await run(async isLive => {
      if (unconfirmed) {
        await checkResult(unconfirmed, isLive, t.saveFailed)

        return
      }

      const body = { decision, changes: decision === 'save' ? profileChanges(drafts) : [] }

      if (!(await confirmProfileChanges(body.changes)) || !isLive()) {
        return
      }

      const result = await authedResource('onboarding.put_onboarding_profile', { body })

      if (!isLive() || (!result.ok && result.reason === 'unauth')) {
        return
      }

      if (result.ok && result.value?.complete) {
        onCompleted()

        return
      }

      const cause = result.ok ? undefined : result.error
      const failure = backendDetailMessage(cause, t.saveFailed)

      if (ipcErrorStatus(cause) === 409 || isUncertainMutationIpc(cause)) {
        setUnconfirmed(body)
        await checkResult(body, isLive, failure)
      } else {
        setError(failure)
      }
    })
  }

  return (
    <CreationWorkspace
      className={styles.questionWorkspace}
      footer={
        <CreationActionBar>
          <div className={styles.navigation}>
            {!reviewingSaved && (
              <button className={BTN_GHOST} disabled={busy} onClick={onBack} type="button">
                {t.portraitBack}
              </button>
            )}
            <div className={styles.forward}>
              {reviewingSaved ? (
                <button className={ONBOARDING_PRIMARY_BUTTON} onClick={onCompleted} type="button">
                  {t.enterApp}
                </button>
              ) : unconfirmed ? (
                <button
                  className={ONBOARDING_PRIMARY_BUTTON}
                  disabled={busy}
                  onClick={() => void finish(unconfirmed.decision)}
                  type="button"
                >
                  {dict.creation.checkResult}
                </button>
              ) : (
                <>
                  <button className={BTN_GHOST} disabled={busy} onClick={() => void finish('skip')} type="button">
                    {t.profileSkip}
                  </button>
                  <button
                    className={ONBOARDING_PRIMARY_BUTTON}
                    disabled={busy || !canSave}
                    onClick={() => void finish('save')}
                    type="button"
                  >
                    {busy ? dict.common.saving : t.profileSave}
                  </button>
                </>
              )}
            </div>
          </div>
        </CreationActionBar>
      }
    >
      <div
        className="space-y-4"
        onKeyDown={event => {
          if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) {
            submitOnEnter(event, () => void finish('save'))
          }
        }}
      >
        <div>
          <h2 className={styles.questionTitle}>{dict.settings.memory.profile.title}</h2>
          <p className={HINT_TEXT}>{t.profileHint}</p>
        </div>
        {loadError && <ErrorBanner message={loadError} onRetry={() => void load()} retryLabel={dict.common.retry} />}
        {loading && (
          <p className={HINT_TEXT} role="status">
            {dict.settings.memory.loading}
          </p>
        )}
        <UserProfileForm
          busy={busy || !rows || loading || reviewingSaved || !!unconfirmed}
          drafts={reviewingSaved ? {} : drafts}
          onEdit={(field, draft) => {
            const next = { ...onboardingProfileDraft.get() }

            if (draft) {
              next[field] = draft
            } else {
              delete next[field]
            }

            onboardingProfileDraft.replace(next)
            setError('')
          }}
          rows={rows ?? []}
        />
        {reviewingSaved ? (
          <CreationFeedback state="review" title={t.profileAlreadyComplete} />
        ) : unconfirmed ? (
          <CreationFeedback state="unknown" title={t.profileCheckPending} />
        ) : (
          error && <CreationFeedback state="error" title={error} />
        )}
      </div>
    </CreationWorkspace>
  )
}
