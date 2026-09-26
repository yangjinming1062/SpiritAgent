import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import {
  $avatarSeeds,
  $characterCard,
  BODY_FEATURE_KEYS,
  type CharacterOverrides,
  extractCharacterCard,
  hydrateAvatarSeeds,
  hydrateCharacterCard,
  PORTRAIT_FEATURE_KEYS,
  saveCharacterCard
} from '@/modules/character'
import { PortraitLightbox } from '@/shared'
import { backendDetailMessage, unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { currentClearEpoch } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, FIELD_LABEL, HINT_TEXT, INPUT_CLASS, SECTION_TITLE } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

export function CharacterCardSection({ avatarId }: { avatarId: number }): React.JSX.Element {
  const strings = useStrings()
  const t = strings.settings.persona.characterCard
  const common = strings.common
  const authKind = useStore($auth).kind
  const card = useStore($characterCard)
  const images = useStore($avatarSeeds)
  const [editing, setEditing] = useState(false)
  const [changes, setChanges] = useState<CharacterOverrides>({})
  const [revision, setRevision] = useState(0)
  const [hint, setHint] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [zoom, setZoom] = useState<string | null>(null)
  const mounted = useRef(false)
  const current = card?.avatar_id === avatarId ? card : null
  const analyzing = current?.status === 'pending' || current?.status === 'running'

  useEffect(() => {
    mounted.current = true
    const epoch = currentClearEpoch()

    if (authKind === 'authenticated') {
      void hydrateAvatarSeeds()
      void hydrateCharacterCard().catch(error => {
        if (mounted.current && epoch === currentClearEpoch()) {
          setHint(backendDetailMessage(error, t.loadFailed))
        }
      })
    }

    return () => {
      mounted.current = false
    }
  }, [authKind, t.loadFailed])

  useEffect(() => {
    if (authKind !== 'authenticated') {
      return
    }

    const refresh = (): void => {
      const epoch = currentClearEpoch()
      void hydrateCharacterCard().catch(error => {
        if (mounted.current && epoch === currentClearEpoch()) {
          setHint(backendDetailMessage(error, t.loadFailed))
        }
      })
    }

    window.addEventListener('focus', refresh)
    const timer = analyzing ? window.setInterval(refresh, 5000) : undefined

    return () => {
      window.removeEventListener('focus', refresh)
      window.clearInterval(timer)
    }
  }, [analyzing, authKind, t.loadFailed])

  const run = async (operation: () => Promise<void>): Promise<void> => {
    const epoch = currentClearEpoch()
    setBusy(true)
    setHint(null)

    try {
      await operation()
    } catch (error) {
      if (mounted.current && epoch === currentClearEpoch()) {
        setHint(backendDetailMessage(error, t.operationFailed))

        if (/^409\s/.test(unwrapIpcErrorMessage(error))) {
          await hydrateCharacterCard().catch(refreshError =>
            console.error('Character card refresh failed', refreshError)
          )
        }
      }
    } finally {
      if (mounted.current && epoch === currentClearEpoch()) {
        setBusy(false)
      }
    }
  }

  const save = (): void => {
    const epoch = currentClearEpoch()
    void run(async () => {
      if (await saveCharacterCard(avatarId, revision, changes)) {
        if (mounted.current && epoch === currentClearEpoch()) {
          setEditing(false)
          setChanges({})
          setHint(t.saved)
        }
      }
    })
  }

  return (
    <section className="space-y-3">
      <p className={SECTION_TITLE}>{t.title}</p>
      <div className="liquid-glass-card space-y-4 rounded-2xl p-4">
        <div className="flex min-w-0 items-start gap-3">
          <p className={cn(HINT_TEXT, 'min-w-0 flex-1')}>{t.description}</p>
          {!editing && current && current.revision > 0 && (
            <button
              className={cn(BTN_GHOST, 'shrink-0 whitespace-nowrap')}
              onClick={() => {
                setRevision(current.revision)
                setChanges({})
                setEditing(true)
                setHint(null)
              }}
              type="button"
            >
              {t.edit}
            </button>
          )}
        </div>
        {images.avatarId === avatarId && (
          <div className="flex gap-3">
            {[
              { url: images.avatarUrl, label: t.portrait },
              { url: images.fullbodySeedUrl, label: t.body }
            ].map(
              ({ url, label }) =>
                url && (
                  <button
                    className="flex min-w-0 flex-1 flex-col items-center gap-1 rounded-xl border border-line-hairline p-2"
                    key={label}
                    onClick={() => setZoom(url)}
                    type="button"
                  >
                    <img alt={label} className="h-40 w-full rounded-lg object-contain" src={url} />
                    <span className={HINT_TEXT}>{label}</span>
                  </button>
                )
            )}
          </div>
        )}
        {current && current.revision > 0 && (
          <div className="space-y-4">
            {[
              { keys: PORTRAIT_FEATURE_KEYS, label: t.portraitFeatures },
              { keys: BODY_FEATURE_KEYS, label: t.bodyFeatures }
            ].map((group, groupIndex) => (
              <div
                className={groupIndex === 0 ? 'space-y-3' : 'space-y-3 border-t border-line-hairline pt-4'}
                key={group.label}
              >
                <p className="text-sm font-medium text-strong">{group.label}</p>
                {editing ? (
                  <div className="space-y-3">
                    {group.keys.map(key => (
                      <div key={key}>
                        <div className="flex items-center justify-between gap-2">
                          <label className={FIELD_LABEL} htmlFor={`character-${key}`}>
                            {t.fields[key]}
                          </label>
                          {(Object.hasOwn(changes, key) ? changes[key] != null : current.overrides[key] != null) && (
                            <button
                              className={BTN_GHOST}
                              disabled={busy}
                              onClick={() => setChanges(previous => ({ ...previous, [key]: null }))}
                              type="button"
                            >
                              {t.restore}
                            </button>
                          )}
                        </div>
                        <textarea
                          className={INPUT_CLASS}
                          disabled={busy}
                          id={`character-${key}`}
                          maxLength={1000}
                          onChange={event => setChanges(previous => ({ ...previous, [key]: event.target.value }))}
                          rows={3}
                          value={
                            Object.hasOwn(changes, key)
                              ? (changes[key] ?? current.automatic[key])
                              : current.features[key]
                          }
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <dl className="space-y-2">
                    {group.keys.map(key => {
                      const value = current.features[key] || t.unknown

                      return (
                        <div className="flex min-w-0 items-baseline gap-2" key={key}>
                          <dt className="w-36 shrink-0 truncate text-[12px] text-muted" title={t.fields[key]}>
                            {t.fields[key]}
                            {strings.settings.persona.detailSeparator}
                          </dt>
                          <dd className="flex min-w-0 flex-1 items-baseline gap-2">
                            <span className="min-w-0 flex-1 truncate text-sm text-body" title={value}>
                              {value}
                            </span>
                            {current.overrides[key] != null && (
                              <span className={cn(HINT_TEXT, 'shrink-0')}>{t.edited}</span>
                            )}
                          </dd>
                        </div>
                      )
                    })}
                  </dl>
                )}
              </div>
            ))}
          </div>
        )}
        {editing && current && revision !== current.revision && (
          <p className="text-xs text-amber-300/90">{t.conflict}</p>
        )}
        {analyzing && <p className={HINT_TEXT}>{t.analyzing}</p>}
        {current?.status === 'failed' && <p className="text-xs text-amber-300/90">{t.failed}</p>}
        {hint && (
          <p aria-live="polite" className={HINT_TEXT}>
            {hint}
          </p>
        )}
        {editing ? (
          <div className="flex flex-wrap gap-2">
            <button className={BTN_SUBTLE} disabled={busy} onClick={() => setEditing(false)} type="button">
              {common.cancel}
            </button>
            {current && revision !== current.revision && (
              <button
                className={BTN_SUBTLE}
                disabled={busy}
                onClick={() => setRevision(current.revision)}
                type="button"
              >
                {t.merge}
              </button>
            )}
            <button
              className={BTN_PRIMARY}
              disabled={busy || !Object.keys(changes).length || revision !== current?.revision}
              onClick={save}
              type="button"
            >
              {busy ? common.saving : common.save}
            </button>
          </div>
        ) : (
          <div className="flex gap-2">
            <button
              className={BTN_SUBTLE}
              disabled={busy || analyzing}
              onClick={() =>
                void run(() => (current ? extractCharacterCard(avatarId, current.revision) : hydrateCharacterCard()))
              }
              type="button"
            >
              {current ? (current.status === 'failed' ? t.retry : t.extract) : t.reload}
            </button>
          </div>
        )}
      </div>
      {zoom && <PortraitLightbox name={t.title} onClose={() => setZoom(null)} url={zoom} />}
    </section>
  )
}
