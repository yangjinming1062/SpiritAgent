import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { $avatarSeeds, hydrateAvatarSeeds } from '@/features/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { failureText } from '@/shared/lib/ipc-error'
import { requestResource } from '@/shared/lib/resource-request'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE, CreationFeedback, HINT_TEXT, SECTION_TITLE } from '@/ui'
import { ImageLightboxButton } from '@/ui/image-lightbox-button'
import type { AvatarAppearance } from '@protocol'

export function AppearancePreview(): React.JSX.Element | null {
  const strings = useStrings()
  const t = strings.settings.persona
  const images = useStore($avatarSeeds)
  const [avatar, setAvatar] = useState<AvatarAppearance | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const begin = useAsyncGuard()
  const working = avatar?.preparation_status === 'queued' || avatar?.preparation_status === 'running'

  useEffect(() => {
    void hydrateAvatarSeeds()
  }, [])
  useEffect(() => {
    let active = true
    const scope = captureAuthScope()

    const refresh = async (): Promise<void> => {
      try {
        const next = await requestResource('companion.get_avatar')

        if (active && scope?.()) {
          setAvatar(next)
          setError(null)
        }
      } catch (failure) {
        if (active && scope?.()) {
          setError(failureText(failure, strings.common.operationFailed))
        }
      }
    }

    void refresh()

    const timer = working
      ? window.setInterval(() => {
          void refresh()
        }, 5000)
      : undefined

    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [images, working, strings.common.operationFailed])

  if (!images.avatarId) {
    return null
  }

  return (
    <section className="space-y-3">
      <h3 className={SECTION_TITLE}>{t.appearanceTitle}</h3>
      <div className="liquid-glass-card rounded-2xl p-4 space-y-3">
        <div className="flex gap-3">
          {[
            { url: images.avatarUrl, label: t.portraitLabel },
            { url: avatar?.is_fullbody_confirmed ? images.fullbodySeedUrl : null, label: t.fullbodyLabel }
          ].map(
            ({ url, label }, index) =>
              url && (
                <ImageLightboxButton
                  className="min-w-0 flex-1 rounded-xl border border-line-hairline p-2"
                  key={index}
                  name={label}
                  url={url}
                >
                  <img alt={label} className="h-44 w-full object-contain" src={url} />
                </ImageLightboxButton>
              )
          )}
        </div>
        {working && (
          <p className={HINT_TEXT} role="status">
            {t.preparationWorking}
          </p>
        )}
        {(error || avatar?.preparation_error) && (
          <CreationFeedback state="error" title={error || avatar?.preparation_error || ''} />
        )}
        {avatar?.preparation_status && ['failed', 'paused', 'unknown'].includes(avatar.preparation_status) && (
          <button
            className={BTN_SUBTLE}
            disabled={busy}
            onClick={() => {
              const scope = captureAuthScope()
              const live = begin()
              setBusy(true)
              void requestResource('companion.retry_appearance_preparation', {
                path: { avatar_id: avatar.id },
                body: { expected_revision: avatar.identity_revision }
              })
                .then(next => {
                  if (live() && scope?.()) {
                    setAvatar(next)
                    setError(null)
                  }
                })
                .catch(failure => {
                  if (live() && scope?.()) {
                    setError(failureText(failure, strings.common.operationFailed))
                  }
                })
                .finally(() => {
                  if (live() && scope?.()) {
                    setBusy(false)
                  }
                })
            }}
            type="button"
          >
            {t.preparationRetry}
          </button>
        )}
      </div>
    </section>
  )
}
