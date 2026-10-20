import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useState } from 'react'

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
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage, ipcErrorStatus } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import {
  BTN_GHOST,
  BTN_SUBTLE,
  CreationActionBar,
  CreationFeedback,
  FIELD_LABEL,
  HINT_TEXT,
  INPUT_CLASS,
  SECTION_TITLE,
  WizardModal
} from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'

import { $companionEditors, updateCompanionEditors } from './companion-editor-state'

export function CharacterCardSection({
  avatarId,
  editorOpen,
  onEditRequested,
  onEditorClosed
}: {
  avatarId: number
  editorOpen: boolean
  onEditRequested: () => void
  onEditorClosed: () => void
}): React.JSX.Element {
  const strings = useStrings()
  const t = strings.settings.persona.characterCard
  const common = strings.common
  const authKind = useStore($auth).kind
  const card = useStore($characterCard)
  const images = useStore($avatarSeeds)
  const english = useStore($locale) === 'en'
  const draft = useStore($companionEditors).characterCards[String(avatarId)]
  const editing = !!draft && editorOpen
  const changes = draft?.changes ?? {}
  const revision = draft?.revision ?? 0
  const [hint, setHint] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [zoom, setZoom] = useState<string | null>(null)
  const [imageError, setImageError] = useState(false)
  const [imageRevision, setImageRevision] = useState(0)
  const begin = useAsyncGuard()
  const current = card?.avatar_id === avatarId ? card : null
  const analyzing = current?.status === 'pending' || current?.status === 'running'

  const setChanges = (next: CharacterOverrides): void => {
    if (draft) {
      updateCompanionEditors({
        characterCards: { ...$companionEditors.get().characterCards, [String(avatarId)]: { ...draft, changes: next } }
      })
    }
  }

  const closeEditor = (): void => {
    const cards = { ...$companionEditors.get().characterCards }
    delete cards[String(avatarId)]
    updateCompanionEditors({ characterCards: cards })
    setHint(null)
    onEditorClosed()
  }

  const refresh = useCallback((): void => {
    const isLive = begin()
    void hydrateCharacterCard().catch(error => {
      if (isLive()) {
        setHint(backendDetailMessage(error, t.loadFailed))
      }
    })
  }, [begin, t.loadFailed])

  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateAvatarSeeds()
      refresh()
    }
  }, [authKind, refresh])

  useEffect(() => {
    if (authKind !== 'authenticated') {
      return
    }

    window.addEventListener('focus', refresh)
    const timer = analyzing ? window.setInterval(refresh, 5000) : undefined

    return () => {
      window.removeEventListener('focus', refresh)
      window.clearInterval(timer)
    }
  }, [analyzing, authKind, refresh])

  const run = async (operation: (isLive: () => boolean) => Promise<void>): Promise<void> => {
    const isLive = begin()
    setBusy(true)
    setHint(null)

    try {
      await operation(isLive)
    } catch (error) {
      if (isLive()) {
        setHint(backendDetailMessage(error, t.operationFailed))

        if (ipcErrorStatus(error) === 409) {
          await hydrateCharacterCard().catch(refreshError => log.warn('character-card', 'Refresh failed', refreshError))
        }
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const creationFeedback = (busy || analyzing || hint || imageError || current?.status === 'failed') && (
    <CreationFeedback
      actions={
        imageError ? (
          <button
            className={BTN_SUBTLE}
            onClick={() => {
              setImageError(false)
              setImageRevision(value => value + 1)
              void hydrateAvatarSeeds()
            }}
            type="button"
          >
            {strings.creation.retryLoad}
          </button>
        ) : undefined
      }
      description={
        hint ||
        (current?.status === 'failed' ? t.failed : busy || analyzing ? strings.creation.preserveCurrent : undefined)
      }
      state={busy || analyzing ? 'working' : 'error'}
      title={
        busy || analyzing
          ? busy && editing
            ? undefined
            : t.analyzing
          : imageError
            ? strings.creation.imageLoadFailed
            : t.operationFailed
      }
    />
  )

  const content = (
    <>
      <div className={editing ? 'space-y-4' : 'liquid-glass-card space-y-4 rounded-2xl p-4'}>
        <div className="flex min-w-0 items-start gap-3">
          <p className={cn(HINT_TEXT, 'min-w-0 flex-1')}>{t.description}</p>
          {!editing && current && current.revision > 0 && (
            <button
              className={cn(BTN_GHOST, 'shrink-0 whitespace-nowrap')}
              onClick={() => {
                if (!draft) {
                  updateCompanionEditors({
                    characterCards: {
                      ...$companionEditors.get().characterCards,
                      [String(avatarId)]: {
                        revision: current.revision,
                        features: current.features,
                        automatic: current.automatic,
                        overrides: current.overrides,
                        changes: {}
                      }
                    }
                  })
                }

                onEditRequested()
                setHint(null)
              }}
              type="button"
            >
              {t.edit}
            </button>
          )}
        </div>
        {editing && Object.keys(changes).length > 0 && (
          <p className={HINT_TEXT}>{english ? 'Unsaved changes' : '未保存修改'}</p>
        )}
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
                    <img
                      alt={label}
                      className="h-40 w-full rounded-lg object-contain"
                      key={`${url}:${imageRevision}`}
                      onError={() => setImageError(true)}
                      src={url}
                    />
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
                          {(Object.hasOwn(changes, key) ? changes[key] != null : draft?.overrides[key] != null) && (
                            <button
                              className={BTN_GHOST}
                              disabled={busy}
                              onClick={() => setChanges({ ...changes, [key]: null })}
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
                          onChange={event => setChanges({ ...changes, [key]: event.target.value })}
                          rows={3}
                          value={
                            Object.hasOwn(changes, key)
                              ? (changes[key] ?? draft?.automatic[key] ?? '')
                              : (draft?.features[key] ?? '')
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
                          <dt className="w-28 shrink-0 whitespace-normal text-[12px] text-muted">
                            {t.fields[key]}
                            {strings.settings.persona.detailSeparator}
                          </dt>
                          <dd className="flex min-w-0 flex-1 items-baseline gap-2">
                            <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-sm leading-6 text-body">
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
          <div className="space-y-2 rounded-xl border border-line-hairline p-3 text-xs text-amber-300/90">
            <p>{t.conflict}</p>
            <dl className="space-y-1">
              {[...PORTRAIT_FEATURE_KEYS, ...BODY_FEATURE_KEYS]
                .filter(key => current.features[key] !== draft?.features[key])
                .map(key => (
                  <div key={key}>
                    <dt>{t.fields[key]}</dt>
                    <dd className="whitespace-pre-wrap break-words text-body">{current.features[key] || t.unknown}</dd>
                  </div>
                ))}
            </dl>
          </div>
        )}
      </div>
    </>
  )

  return (
    <section className="space-y-3">
      <p className={SECTION_TITLE}>{t.title}</p>
      {editing ? (
        <WizardModal
          closeDisabled={busy}
          escClose={!busy && !zoom}
          footer={
            <CreationActionBar description={creationFeedback}>
              <button
                aria-atomic="true"
                aria-live="polite"
                className={BTN_SUBTLE}
                disabled={busy || !Object.keys(changes).length || revision !== current?.revision}
                onClick={() =>
                  void run(async isLive => {
                    const isCurrent = captureAuthScope()

                    if (!isCurrent) {
                      return
                    }

                    try {
                      if (!(await saveCharacterCard(avatarId, revision, changes))) {
                        throw new Error(t.operationFailed)
                      }
                    } catch (error) {
                      await hydrateCharacterCard().catch(refreshError =>
                        log.warn('character-card', 'Refresh failed', refreshError)
                      )

                      if (!isCurrent()) {
                        return
                      }

                      const latest = $characterCard.get()

                      const confirmed =
                        latest?.avatar_id === avatarId &&
                        [...PORTRAIT_FEATURE_KEYS, ...BODY_FEATURE_KEYS].every(
                          key =>
                            !Object.hasOwn(changes, key) || (latest.overrides[key] ?? null) === (changes[key] ?? null)
                        )

                      if (!confirmed) {
                        throw error
                      }
                    }

                    if (
                      isCurrent() &&
                      JSON.stringify($companionEditors.get().characterCards[String(avatarId)]) === JSON.stringify(draft)
                    ) {
                      const cards = { ...$companionEditors.get().characterCards }
                      delete cards[String(avatarId)]
                      updateCompanionEditors({ characterCards: cards })

                      if (isLive()) {
                        setHint(null)
                      }
                    }
                  })
                }
                type="button"
              >
                {busy ? common.saving : english ? 'Save changes' : '保存修改'}
              </button>
              <button className={BTN_GHOST} disabled={busy} onClick={closeEditor} type="button">
                {common.cancel}
              </button>
              {current && revision !== current.revision && (
                <button
                  className={BTN_SUBTLE}
                  disabled={busy}
                  onClick={() => {
                    if (draft) {
                      updateCompanionEditors({
                        characterCards: {
                          ...$companionEditors.get().characterCards,
                          [String(avatarId)]: {
                            ...draft,
                            revision: current.revision,
                            features: current.features,
                            automatic: current.automatic,
                            overrides: current.overrides
                          }
                        }
                      })
                    }
                  }}
                  type="button"
                >
                  {english ? 'Keep my edits after review' : '已核对，保留我的修改'}
                </button>
              )}
            </CreationActionBar>
          }
          onClose={onEditorClosed}
          regionId="character-card-editor"
          title={t.title}
        >
          {content}
        </WizardModal>
      ) : (
        <>
          {content}
          <CreationActionBar description={creationFeedback}>
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
          </CreationActionBar>
        </>
      )}
      {zoom && <PortraitLightbox name={t.title} onClose={() => setZoom(null)} url={zoom} />}
    </section>
  )
}
