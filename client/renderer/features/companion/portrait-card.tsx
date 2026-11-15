import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { $persona } from '@/features/character'
import { $avatarSeeds, hydrateAvatarSeeds } from '@/features/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { Pencil } from '@/shared/lib/icons'
import { failureText, ipcErrorStatus } from '@/shared/lib/ipc-error'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import { BTN_ICON, BTN_SUBTLE, CreationFeedback, INPUT_CLASS } from '@/ui'
import { ImageLightboxButton } from '@/ui/image-lightbox-button'
import type { AvatarAppearance } from '@protocol'

import { $companionEditors, updateCompanionEditors } from './companion-editor-state'
import { personaBase, personaValues } from './persona-groups'

// 名片承载"它是谁"的第一眼：形象、名字与关系。形象只查看与重试，不提供外形编辑。
export function PortraitCard({
  disabled,
  editingName,
  nameError,
  onDraftNeeded,
  onEditingNameChange
}: {
  disabled: boolean
  editingName: boolean
  nameError?: string
  onDraftNeeded: () => void
  onEditingNameChange: (next: boolean) => void
}): React.JSX.Element {
  const strings = useStrings()
  const t = strings.settings.persona
  const persona = useStore($persona)
  const draft = useStore($companionEditors).persona
  const images = useStore($avatarSeeds)
  const [avatar, setAvatar] = useState<AvatarAppearance | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const begin = useAsyncGuard()
  const working = avatar?.preparation_status === 'queued' || avatar?.preparation_status === 'running'

  const retryable = Boolean(
    avatar?.preparation_status && ['failed', 'paused', 'unknown'].includes(avatar.preparation_status)
  )

  const values = draft?.values ?? personaValues(persona)
  const name = values.name.trim() || t.defaultName
  const fullbodyUrl = avatar?.is_fullbody_confirmed ? images.fullbodySeedUrl : null

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
          // 尚未取得形象时按"没有"处理，不把 404 呈现成读取失败。
          setError(ipcErrorStatus(failure) === 404 ? null : failureText(failure, strings.common.operationFailed))
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

  const changeName = (value: string): void => {
    const next = draft ?? { values: personaValues(persona), base: personaBase(persona) }
    updateCompanionEditors({ persona: { ...next, values: { ...next.values, name: value } } })
  }

  const retryPreparation = (): void => {
    if (!avatar) {
      return
    }

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
  }

  return (
    <section className="liquid-glass-card rounded-2xl p-4">
      <div className="flex items-start gap-4">
        <div className="flex shrink-0 gap-3">
          {images.avatarUrl ? (
            <ImageLightboxButton
              className="w-[88px] shrink-0 rounded-xl border border-line-hairline p-1 transition hover:border-line-strong"
              name={t.portraitLabel}
              url={images.avatarUrl}
            >
              <img
                alt={t.portraitLabel}
                className="aspect-square w-full rounded-lg object-cover"
                src={images.avatarUrl}
              />
            </ImageLightboxButton>
          ) : (
            <div className="grid aspect-square w-[88px] shrink-0 place-items-center rounded-xl bg-accent-soft text-xl font-semibold text-accent">
              {name.slice(0, 1)}
            </div>
          )}
          {fullbodyUrl ? (
            <ImageLightboxButton
              className="w-[88px] shrink-0 rounded-xl border border-line-hairline p-1 transition hover:border-line-strong"
              name={t.fullbodyLabel}
              url={fullbodyUrl}
            >
              <img alt={t.fullbodyLabel} className="aspect-[4/5] w-full rounded-lg object-contain" src={fullbodyUrl} />
            </ImageLightboxButton>
          ) : (
            <div className="grid aspect-[4/5] w-[88px] shrink-0 place-items-center rounded-xl border border-dashed border-line-standard px-1 text-center text-[10px] leading-4 text-muted">
              {avatar ? t.appearanceEmpty : ''}
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {editingName ? (
              <label className="min-w-0 flex-1">
                <span className="sr-only">{t.renameAction}</span>
                <input
                  aria-describedby={nameError ? 'persona-field-name-error' : undefined}
                  aria-invalid={Boolean(nameError)}
                  autoFocus
                  className={cn(INPUT_CLASS, 'max-w-56 py-1 text-[15px] font-medium')}
                  disabled={disabled}
                  id="persona-field-name"
                  maxLength={500}
                  onChange={event => changeName(event.target.value)}
                  onKeyDown={event => {
                    if (event.key === 'Enter' || event.key === 'Escape') {
                      event.preventDefault()
                      onEditingNameChange(false)
                    }
                  }}
                  value={values.name}
                />
              </label>
            ) : (
              <h2 className="min-w-0 truncate text-[17px] font-medium tracking-tight text-strong">{name}</h2>
            )}
            <button
              aria-expanded={editingName}
              aria-label={t.renameAction}
              className={BTN_ICON}
              disabled={disabled}
              onClick={() => {
                onDraftNeeded()
                onEditingNameChange(!editingName)
              }}
              title={t.renameAction}
              type="button"
            >
              <Pencil aria-hidden="true" />
            </button>
          </div>
          {nameError ? (
            <p className="mt-1 text-[11px] text-danger-fg" id="persona-field-name-error">
              {nameError}
            </p>
          ) : null}
          {working ? (
            <div className="mt-2">
              <CreationFeedback state="working" title={t.preparationWorking} />
            </div>
          ) : null}
          {error || avatar?.preparation_error ? (
            <div className="mt-2">
              <CreationFeedback state="error" title={error || avatar?.preparation_error || ''} />
            </div>
          ) : null}
          {retryable ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button className={BTN_SUBTLE} disabled={busy || disabled} onClick={retryPreparation} type="button">
                {t.preparationRetry}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  )
}
