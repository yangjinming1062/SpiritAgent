import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { pickAvatarImage } from '@/features/character'
import {
  $sceneCreationIssue,
  $sceneLibrary,
  createScene,
  discardSceneCreationIssue,
  inspectSceneCreationIssue,
  type SceneCreateInput,
  SceneCreationCheckActions
} from '@/features/creation/environment'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { requestResource } from '@/shared/lib/resource-request'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, CreationActionBar, CreationFeedback, CreationWorkspace, INPUT_CLASS } from '@/ui'

import { ONBOARDING_PRIMARY_BUTTON } from './onboarding-components'
import styles from './onboarding.module.css'
import { useOnboardingAction } from './use-onboarding-action'

interface EnvironmentPageProps {
  onBack: () => void
  onContinue: (complete: boolean) => void
}

type ExistingState = 'checking' | 'exists' | 'absent' | 'failed'

async function hasScene(): Promise<boolean> {
  const page = await requestResource('companion_scenes.scene_list_route', { query: { limit: 1 } })

  return page.scenes.length > 0
}

// 环境步骤复用环境库的创建与上传链路；受理即推进引导，制作在后台完成，维护入口在环境库。
export function EnvironmentPage({ onBack, onContinue }: EnvironmentPageProps): React.JSX.Element {
  const dict = useStrings()
  const t = dict.onboarding
  const issue = useStore($sceneCreationIssue)
  const waiting = issue?.outcome === 'unknown'
  const { busy, error, setError, run } = useOnboardingAction()
  const [existing, setExisting] = useState<ExistingState>('checking')
  const [notes, setNotes] = useState('')
  const begin = useAsyncGuard()
  const [readAttempt, setReadAttempt] = useState(0)

  useEffect(() => {
    setExisting('checking')

    if (waiting) {
      return
    }

    const isLive = begin()
    let cancelled = false
    void hasScene().then(
      found => {
        if (!cancelled && isLive()) {
          setExisting(found ? 'exists' : 'absent')
        }
      },
      () => {
        if (!cancelled && isLive()) {
          setExisting('failed')
        }
      }
    )

    return () => {
      cancelled = true
    }
  }, [begin, readAttempt, waiting])

  const continueAfterChoice = async (useDefault: boolean, isLive: () => boolean): Promise<void> => {
    if (!isLive()) {
      return
    }

    // 素材已受理后，即使进度回执丢失，重试也只需确认这一步。
    if (!useDefault) {
      setExisting('exists')
    }

    const state = await requestResource('onboarding.patch_onboarding', {
      body: { field: useDefault ? 'environment_skipped' : 'environment_complete', value: 'true' }
    })

    if (!isLive()) {
      return
    }

    if (state.complete || state.next_field === 'user_profile') {
      onContinue(state.complete)
    } else {
      setError(t.saveFailed)
    }
  }

  const submitScene = async (body: SceneCreateInput, isLive: () => boolean): Promise<void> => {
    const result = await createScene(body)

    if (!isLive()) {
      return
    }

    if (result.kind === 'failed') {
      if (result.outcome === 'rejected') {
        discardSceneCreationIssue()
        setError(result.message)
      }

      return
    }

    if (result.kind === 'unavailable' && !(await hasScene())) {
      if (isLive()) {
        setError(t.environmentCreateFailed)
      }

      return
    }

    await continueAfterChoice(false, isLive)
  }

  const submitUpload = async (isLive: () => boolean): Promise<void> => {
    const picked = await pickAvatarImage(t.environmentPickTitle)

    if (!picked || !isLive()) {
      return
    }

    if ('error' in picked) {
      setError(picked.error)

      return
    }

    await submitScene({ source: 'analysis', image: picked.image.base64 }, isLive)
  }

  return (
    <CreationWorkspace
      footer={
        <CreationActionBar>
          <div className={styles.navigation}>
            <button className={BTN_SUBTLE} disabled={busy} onClick={onBack} type="button">
              {t.stepVoice}
            </button>
          </div>
        </CreationActionBar>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-col gap-2">
          <h2 className={styles.questionTitle}>{t.environmentTitle}</h2>
          <p className="text-xs leading-relaxed text-body">{t.environmentHint}</p>
        </div>
        {waiting ? (
          <CreationFeedback
            actions={
              <SceneCreationCheckActions
                checking={busy}
                issue={issue}
                onCheck={() =>
                  void run(async live => {
                    if (!(await inspectSceneCreationIssue()) && live()) {
                      setError(dict.living.scene.loadFailed)
                    }
                  })
                }
              />
            }
            description={
              issue.inspected
                ? `${$sceneLibrary.get().length ? t.environmentExists : t.environmentNotFound} ${dict.creation.inspectionHint}`
                : dict.living.scene.submissionUnknown
            }
            state="unknown"
            title={dict.creation.unknown}
          />
        ) : (
          existing === 'checking' && <p className="text-xs text-muted">{t.environmentChecking}</p>
        )}
        {!waiting && existing === 'failed' && (
          <div className="space-y-2">
            <p className="text-sm text-danger-fg" role="alert">
              {t.environmentReadFailed}
            </p>
            <button className={BTN_SUBTLE} disabled={busy} onClick={() => setReadAttempt(n => n + 1)} type="button">
              {t.reload}
            </button>
          </div>
        )}
        {!waiting && existing === 'exists' && (
          <div className="space-y-3">
            <p className="text-xs leading-relaxed text-body">{t.environmentExists}</p>
            <button
              className={`ml-auto ${ONBOARDING_PRIMARY_BUTTON}`}
              disabled={busy}
              onClick={() => void run(live => continueAfterChoice(false, live))}
              type="button"
            >
              {t.environmentContinue}
            </button>
          </div>
        )}
        {!waiting && existing === 'absent' && (
          <div className="space-y-4">
            <label className="block space-y-1.5 text-xs text-body" htmlFor="onboarding-environment-notes">
              <span>{t.environmentNotesLabel}</span>
              <textarea
                className={INPUT_CLASS}
                disabled={busy}
                id="onboarding-environment-notes"
                maxLength={500}
                onChange={event => {
                  setNotes(event.target.value)

                  if (error) {
                    setError('')
                  }
                }}
                placeholder={t.environmentNotesPlaceholder}
                rows={3}
                value={notes}
              />
            </label>
            <div className={styles.navigation}>
              <button
                className={`ml-auto ${ONBOARDING_PRIMARY_BUTTON}`}
                disabled={busy || !notes.trim()}
                onClick={() => void run(live => submitScene({ source: 'generate', notes: notes.trim() }, live))}
                type="button"
              >
                {t.environmentGenerate}
              </button>
            </div>
            <div className={styles.navigation}>
              <button className={BTN_SUBTLE} disabled={busy} onClick={() => void run(submitUpload)} type="button">
                {t.environmentUpload}
              </button>
            </div>
          </div>
        )}
        {!waiting && (existing === 'absent' || existing === 'failed') && (
          <button
            className={BTN_SUBTLE}
            disabled={busy}
            onClick={() => void run(live => continueAfterChoice(true, live))}
            type="button"
          >
            {t.environmentSkip}
          </button>
        )}
        {error && (
          <p className="text-xs text-danger-fg" role="alert">
            {error}
          </p>
        )}
      </div>
    </CreationWorkspace>
  )
}
