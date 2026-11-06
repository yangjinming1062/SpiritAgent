import { useEffect, useRef, useState } from 'react'

import { pickAvatarImage } from '@/features/character'
import { adoptSceneImage, createScene, discardSceneCreationIssue } from '@/features/creation/environment'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { authedResource } from '@/shared/lib/resource-request'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, CreationActionBar, CreationWorkspace, INPUT_CLASS } from '@/ui'

import { ONBOARDING_PRIMARY_BUTTON } from './onboarding-components'
import styles from './onboarding.module.css'

interface EnvironmentPageProps {
  busy: boolean
  error: string | null
  onBack: () => void
  onCompleted: () => void
  onError: (message: string) => void
  onBusyChange: (busy: boolean) => void
}

type ExistingState = 'checking' | 'exists' | 'absent'

// 环境步骤复用环境库的创建与上传链路；受理即推进引导，制作在后台完成，维护入口在环境库。
export function EnvironmentPage({
  busy,
  error,
  onBack,
  onCompleted,
  onError,
  onBusyChange
}: EnvironmentPageProps): React.JSX.Element {
  const t = useStrings().onboarding
  const [existing, setExisting] = useState<ExistingState>('checking')
  const [notes, setNotes] = useState('')
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    const scope = mountedRef

    void (async () => {
      try {
        const result = await authedResource('companion_scenes.read_scene_state')
        const hasScene = result.ok && result.value !== null && result.value.materials.length > 0

        if (scope.current) {
          // 已有场景（如重启恢复兜底已建默认场景）时不再重复创建，直接以只读态继续引导。
          setExisting(hasScene ? 'exists' : 'absent')
        }
      } catch (err) {
        // 状态读取失败按无场景处理，创建时的服务端约束仍会兜底（409 时提示继续）。
        if (scope.current) {
          log.warn('onboarding', 'scene state read failed', err)
          setExisting('absent')
        }
      }
    })()
  }, [])

  const finish = (): void => {
    if (mountedRef.current) {
      onCompleted()
    }
  }

  const submitDescribe = async (): Promise<void> => {
    const trimmed = notes.trim()

    if (!trimmed) {
      onError(t.environmentNotesRequired)

      return
    }

    onBusyChange(true)
    onError('')

    try {
      const result = await createScene({ notes: trimmed })

      // created=已受理；unavailable=互斥/已有任务，环境已存在或已在途，直接继续；failed=就地报错保留输入。
      // 引导期用户看不到环境库的失败提醒，rejected 的提醒就地消费后丢弃，避免残留到之后首次打开环境库。
      if (result.kind === 'failed') {
        if (result.outcome === 'rejected') {
          discardSceneCreationIssue()
        }

        onError(result.message)
      } else {
        finish()
      }
    } finally {
      if (mountedRef.current) {
        onBusyChange(false)
      }
    }
  }

  const submitUpload = async (): Promise<void> => {
    const picked = await pickAvatarImage(t.environmentPickTitle)

    if (!picked) {
      return
    }

    if ('error' in picked) {
      onError(picked.error)

      return
    }

    onBusyChange(true)
    onError('')

    try {
      const sceneId = await adoptSceneImage({ base64: picked.image.base64 })

      if (sceneId) {
        finish()
      } else {
        onError(t.environmentCreateFailed)
      }
    } catch (err) {
      onError(backendDetailMessage(err, t.environmentCreateFailed))
      log.warn('onboarding', 'environment upload failed', err)
    } finally {
      if (mountedRef.current) {
        onBusyChange(false)
      }
    }
  }

  const submitSkip = async (): Promise<void> => {
    onBusyChange(true)
    onError('')

    try {
      const result = await authedResource('onboarding.patch_onboarding', {
        body: { field: 'environment_skipped', value: 'true' }
      })

      if (result.ok) {
        finish()
      } else {
        onError(t.environmentSkipFailed)
      }
    } catch (err) {
      if (mountedRef.current) {
        onError(backendDetailMessage(err, t.environmentSkipFailed))
        log.warn('onboarding', 'environment skip failed', err)
      }
    } finally {
      if (mountedRef.current) {
        onBusyChange(false)
      }
    }
  }

  return (
    <CreationWorkspace
      footer={
        <CreationActionBar>
          <div className={styles.navigation}>
            <button className={BTN_SUBTLE} disabled={busy} onClick={onBack} type="button">
              {t.stepVoice}
            </button>
            <span className="ml-auto" />
          </div>
        </CreationActionBar>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-col gap-2">
          <h2 className={styles.questionTitle}>{t.environmentTitle}</h2>
          <p className="text-xs leading-relaxed text-body">{t.environmentHint}</p>
        </div>
        {existing === 'checking' && <p className="text-xs text-muted">{t.environmentChecking}</p>}
        {existing === 'exists' && (
          <div className="space-y-3">
            <p className="text-xs leading-relaxed text-body">{t.environmentExists}</p>
            <button className={`ml-auto ${ONBOARDING_PRIMARY_BUTTON}`} disabled={busy} onClick={finish} type="button">
              {t.environmentContinue}
            </button>
          </div>
        )}
        {existing === 'absent' && (
          <div className="space-y-4">
            <label className="block space-y-1.5 text-xs text-body" htmlFor="onboarding-environment-notes">
              <span>{t.environmentNotesLabel}</span>
              <textarea
                className={INPUT_CLASS}
                id="onboarding-environment-notes"
                maxLength={500}
                onChange={event => {
                  setNotes(event.target.value)

                  if (error) {
                    onError('')
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
                disabled={busy}
                onClick={() => void submitDescribe()}
                type="button"
              >
                {t.environmentGenerate}
              </button>
            </div>
            <div className={styles.navigation}>
              <button className={BTN_SUBTLE} disabled={busy} onClick={() => void submitUpload()} type="button">
                {t.environmentUpload}
              </button>
              <button className={BTN_SUBTLE} disabled={busy} onClick={() => void submitSkip()} type="button">
                {t.environmentSkip}
              </button>
            </div>
          </div>
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
