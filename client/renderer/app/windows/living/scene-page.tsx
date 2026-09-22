import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import {
  $avatarSeeds,
  hydrateAvatarSeeds,
  pickAvatarImage,
  type PickedImage,
  SelfSourceImageFlow
} from '@/modules/character'
import {
  $activeScene,
  $pendingScene,
  $sceneLibrary,
  $scenePage,
  $scenePolicy,
  $sceneTaskSlow,
  $sceneTaskStatus,
  $sceneTotal,
  activateScene,
  adoptSceneImage,
  analyzeScene,
  createScene,
  deleteScene,
  discardPendingScene,
  editScene,
  hydrateScene,
  loadSceneLibrary,
  prepareScenePrompt,
  type SceneAsset,
  setScenePolicy
} from '@/modules/scene'
import { PortraitLightbox } from '@/shared'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Eye, FileImage, Loader2, Sparkles, Trash2, ZoomIn } from '@/shared/lib/icons'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import {
  BTN_PRIMARY,
  BTN_SUBTLE,
  ConfirmDialog,
  HINT_TEXT,
  INPUT_CLASS,
  SettingCard,
  SettingRow,
  SettingsContent,
  Toggle
} from '@/shared/panel'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

function GroupHeading({ subtitle, title }: { subtitle?: string; title: string }): React.JSX.Element {
  return (
    <div className="flex items-baseline justify-between pb-1.5 pt-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-faint">{title}</h3>
      {subtitle ? <span className="text-[10.5px] text-faint">{subtitle}</span> : null}
    </div>
  )
}

interface ZoomTarget {
  name: string
  url: string
}

export function ScenePage(): React.JSX.Element {
  const history = useStore($sceneLibrary)
  const pending = useStore($pendingScene)
  const slow = useStore($sceneTaskSlow)
  const total = useStore($sceneTotal)
  const page = useStore($scenePage)
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<SceneAsset | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const policy = useStore($scenePolicy)
  const status = useStore($sceneTaskStatus)
  const activeScene = useStore($activeScene)
  const avatarSeeds = useStore($avatarSeeds)
  const dict = useStrings()
  const t = dict.living.scene
  const tToasts = dict.living.toasts
  const [notes, setNotes] = useState('')
  const [outfitDescription, setOutfitDescription] = useState('')
  const [reference, setReference] = useState<PickedImage | null>(null)
  const [selecting, setSelecting] = useState(false)
  const [referenceError, setReferenceError] = useState(false)
  const [selfSourceOpen, setSelfSourceOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<SceneAsset | null>(null)
  const [zoom, setZoom] = useState<ZoomTarget | null>(null)
  const mounted = useRef(true)
  const generating = status === 'pending'
  const waitingUpload = status === 'waiting_upload'
  const busy = generating || selecting || waitingUpload || uploading

  const openZoom = (url: string | undefined, name: string): void => {
    if (!url) {
      return
    }

    setZoom({ name, url })
  }

  useEffect(() => {
    mounted.current = true

    void hydrateAvatarSeeds()
    void hydrateScene()

    const unregister = registerStorageClearHandler((): void => {
      setNotes('')
      setOutfitDescription('')
      setQuery('')
      setEditTitle('')
      setEditDescription('')
      setSaving(false)
      setUploading(false)
      setSelfSourceOpen(false)
      setReference(null)
      setSelecting(false)
      setReferenceError(false)
      // 换号/清仓后历史与当前场景均已重置，灯箱与删除确认不得继续展示旧图。
      setZoom(null)
      setPendingDelete(null)
      setEditing(null)
    })

    return (): void => {
      mounted.current = false
      unregister()
    }
  }, [])

  const chooseReference = async (): Promise<void> => {
    if (busy) {
      return
    }

    const epoch = currentClearEpoch()
    setSelecting(true)
    setReferenceError(false)
    const result = await pickAvatarImage(t.chooseReference)

    if (!mounted.current || currentClearEpoch() !== epoch) {
      return
    }

    if (result && 'image' in result) {
      setReference(result.image)
    } else if (result && 'error' in result) {
      setReferenceError(true)
    }

    setSelecting(false)
  }

  const handleChange = async (): Promise<void> => {
    if (busy) {
      return
    }

    triggerHaptic('open')
    await createScene({
      notes: notes.trim() || undefined,
      outfit_description: outfitDescription.trim() || undefined,
      ...(reference ? { image: reference.base64, content_type: reference.contentType } : {})
    })
  }

  const fetchSelfSourcePrompt = async (): Promise<string> =>
    prepareScenePrompt({
      notes: notes.trim() || undefined,
      outfit_description: outfitDescription.trim() || undefined
    })

  const adoptSelfSourceImage = async (image: PickedImage): Promise<void> => {
    await adoptSceneImage(image)
  }

  // 等待上传态的直传入口：提示词已在行上，无需再走弹窗。
  const uploadWaitingImage = async (): Promise<void> => {
    if (uploading) {
      return
    }

    const epoch = currentClearEpoch()
    setSelecting(true)
    const result = await pickAvatarImage(t.chooseReference)

    if (!mounted.current || currentClearEpoch() !== epoch) {
      setSelecting(false)

      return
    }

    setSelecting(false)

    if (!result || !('image' in result)) {
      if (result && 'error' in result) {
        notify({ kind: 'warning', message: result.error })
      }

      return
    }

    setUploading(true)

    try {
      await adoptSceneImage(result.image)
    } catch (err) {
      if (!mounted.current || epoch !== currentClearEpoch()) {
        return
      }

      notify({ kind: 'warning', message: err instanceof Error ? err.message : tToasts.sceneRegenerateFailed })
    } finally {
      if (mounted.current && epoch === currentClearEpoch()) {
        setUploading(false)
      }
    }
  }

  const discardWaitingUpload = async (): Promise<void> => {
    const epoch = currentClearEpoch()

    if (uploading) {
      return
    }

    try {
      await discardPendingScene()
    } catch (err) {
      if (!mounted.current || epoch !== currentClearEpoch()) {
        return
      }

      notify({ kind: 'warning', message: err instanceof Error ? err.message : tToasts.sceneRegenerateFailed })
    }
  }

  const handleActivate = async (sceneId: string): Promise<void> => {
    triggerHaptic('tap')
    await activateScene(sceneId)
  }

  const beginEdit = (entry: SceneAsset): void => {
    setEditing(entry)
    setEditTitle(entry.title)
    setEditDescription(entry.description)
  }

  const saveEdit = async (): Promise<void> => {
    if (!editing || saving) {
      return
    }

    const epoch = currentClearEpoch()
    setSaving(true)

    try {
      await editScene(editing.id, editTitle.trim(), editDescription.trim())

      if (mounted.current && epoch === currentClearEpoch()) {
        setEditing(null)
      }
    } catch (error) {
      if (!mounted.current || epoch !== currentClearEpoch()) {
        return
      }

      notify({ kind: 'warning', message: error instanceof Error ? error.message : tToasts.sceneRegenerateFailed })
    } finally {
      if (mounted.current && epoch === currentClearEpoch()) {
        setSaving(false)
      }
    }
  }

  const retryAnalysis = async (sceneId: string): Promise<void> => {
    const epoch = currentClearEpoch()

    try {
      await analyzeScene(sceneId)
    } catch (error) {
      if (!mounted.current || epoch !== currentClearEpoch()) {
        return
      }

      notify({ kind: 'warning', message: error instanceof Error ? error.message : tToasts.sceneRegenerateFailed })
    }
  }

  const handleDeleteScene = async (): Promise<void> => {
    const epoch = currentClearEpoch()
    const target = pendingDelete

    if (!target) {
      return
    }

    triggerHaptic('tap')

    try {
      await deleteScene(target.id)

      if (mounted.current && epoch === currentClearEpoch()) {
        notify({ kind: 'success', message: tToasts.sceneDeleteSuccess })
      }
    } catch (err) {
      if (!mounted.current || epoch !== currentClearEpoch()) {
        return
      }

      notify({ kind: 'warning', message: err instanceof Error ? err.message : tToasts.sceneDeleteFailed })
      throw err
    }
  }

  const handleToggleLock = async (): Promise<void> => {
    const epoch = currentClearEpoch()
    triggerHaptic('selection')
    const next = policy === 'locked' ? 'llm_may_replace' : 'locked'

    try {
      await setScenePolicy(next)

      if (mounted.current && epoch === currentClearEpoch()) {
        notify({ kind: 'info', message: next === 'locked' ? tToasts.sceneLocked : tToasts.sceneUnlocked })
      }
    } catch (error) {
      if (!mounted.current || epoch !== currentClearEpoch()) {
        return
      }

      notify({ kind: 'warning', message: error instanceof Error ? error.message : tToasts.sceneLockFailed })
    }
  }

  return (
    <SettingsContent>
      <p className="mb-5 text-[11px] leading-relaxed text-faint">{t.intro}</p>

      <GroupHeading title={t.currentBadge} />
      <SettingCard>
        {activeScene?.url ? (
          <button
            aria-label={t.viewOriginal}
            className="block aspect-video w-full cursor-zoom-in"
            onClick={() => openZoom(activeScene.url, activeScene.title)}
            type="button"
          >
            <img alt={activeScene.title} className="h-full w-full object-cover" src={activeScene.url} />
          </button>
        ) : (
          <p className="p-5 text-xs text-faint">{t.noScene}</p>
        )}
        {activeScene && (
          <div className="space-y-1 p-3.5">
            <h4 className="text-sm font-medium">{activeScene.title}</h4>
            <p className={HINT_TEXT}>{activeScene.description}</p>
          </div>
        )}
      </SettingCard>

      <GroupHeading title={t.createTitle} />
      <SettingCard>
        {pending && (
          <div className="space-y-2 p-3.5">
            <p className="flex items-center gap-2 text-xs">
              {generating && <Loader2 className="size-4 animate-spin" />}
              {waitingUpload ? t.waitingUploadOverlay : slow ? t.slow : t.pendingOverlay}
            </p>
            <p className={HINT_TEXT}>{waitingUpload ? t.waitingUploadHint : t.pendingOverlayHint}</p>
            <p className={HINT_TEXT}>{pending.requirements}</p>
            {pending.outfit_description && <p className={HINT_TEXT}>{pending.outfit_description}</p>}
            <div className="flex gap-2">
              <button className={BTN_SUBTLE} onClick={() => void hydrateScene()} type="button">
                {t.refresh}
              </button>
              <button
                className={BTN_SUBTLE}
                disabled={uploading}
                onClick={() => void discardWaitingUpload()}
                type="button"
              >
                {t.cancelTask}
              </button>
              {waitingUpload && (
                <button
                  className={BTN_PRIMARY}
                  disabled={uploading}
                  onClick={() => void uploadWaitingImage()}
                  type="button"
                >
                  {t.waitingUploadAction}
                </button>
              )}
            </div>
          </div>
        )}
        <div className="space-y-3 border-t border-line-hairline p-3.5">
          <div className="space-y-2">
            <p className={HINT_TEXT}>{t.referenceHint}</p>
            <div className="flex flex-wrap items-center gap-2">
              {reference && (
                <img
                  alt={t.referenceLabel}
                  className="h-20 w-28 rounded-lg border border-line-hairline object-contain"
                  src={reference.previewUrl}
                />
              )}
              <button className={BTN_SUBTLE} disabled={busy} onClick={() => void chooseReference()} type="button">
                {reference ? t.replaceReference : t.chooseReference}
              </button>
              {reference && (
                <button
                  className={BTN_SUBTLE}
                  disabled={busy}
                  onClick={(): void => {
                    setReference(null)
                    setReferenceError(false)
                  }}
                  type="button"
                >
                  {t.removeReference}
                </button>
              )}
            </div>
            {referenceError && (
              <p className="text-xs text-danger-fg" role="alert">
                {t.referenceError}
              </p>
            )}
          </div>
          <textarea
            aria-label={t.notesLabel}
            className={INPUT_CLASS}
            disabled={busy}
            onChange={(event): void => setNotes(event.target.value)}
            placeholder={t.notesPlaceholder}
            rows={3}
            value={notes}
          />
          <label className="flex flex-col gap-2 text-xs text-muted">
            {t.outfitLabel}
            <textarea
              className={INPUT_CLASS}
              disabled={busy}
              onChange={(event): void => setOutfitDescription(event.target.value)}
              placeholder={t.outfitPlaceholder}
              rows={2}
              value={outfitDescription}
            />
            <span className={HINT_TEXT}>{t.outfitHint}</span>
          </label>
        </div>

        <div className="flex items-center justify-between gap-4 p-3.5">
          <p className={HINT_TEXT}>{t.savedHint}</p>
          <div className="flex shrink-0 items-center gap-2">
            <button
              className={cn(BTN_SUBTLE, 'inline-flex shrink-0 items-center gap-1.5 px-3 text-xs')}
              disabled={busy}
              onClick={() => {
                // 提示词以全身种子图为身份锚点：打开前确保本地参考图缓存已水合。
                const epoch = currentClearEpoch()
                void hydrateAvatarSeeds().finally(() => {
                  if (mounted.current && epoch === currentClearEpoch()) {
                    setSelfSourceOpen(true)
                  }
                })
              }}
              title={dict.selfSource.openTitle}
              type="button"
            >
              <FileImage className="size-3.5" />
              <span>{dict.selfSource.open}</span>
            </button>
            <button
              className={cn(
                BTN_PRIMARY,
                'inline-flex shrink-0 items-center gap-1.5 px-3.5 text-xs font-medium transition active:scale-95'
              )}
              disabled={busy}
              onClick={() => void handleChange()}
              type="button"
            >
              {generating ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  <span>{t.generatingButton}</span>
                </>
              ) : (
                <>
                  <Sparkles className="size-3.5" />
                  <span>{t.generateButton}</span>
                </>
              )}
            </button>
          </div>
        </div>
      </SettingCard>

      <GroupHeading subtitle={String(total)} title={t.historyTitle} />
      <div className="flex gap-2">
        <input
          aria-label={t.search}
          className={INPUT_CLASS}
          onChange={event => setQuery(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter') {
              void loadSceneLibrary(query, 0)
            }
          }}
          placeholder={t.search}
          value={query}
        />
        <button className={BTN_SUBTLE} onClick={() => void loadSceneLibrary(query, 0)} type="button">
          {t.search}
        </button>
      </div>
      {editing && (
        <SettingCard>
          <div className="space-y-3 p-3.5">
            <label className="block text-xs">
              {t.titleLabel}
              <input
                className={INPUT_CLASS}
                maxLength={80}
                onChange={event => setEditTitle(event.target.value)}
                value={editTitle}
              />
            </label>
            <label className="block text-xs">
              {t.descriptionLabel}
              <textarea
                className={INPUT_CLASS}
                maxLength={2000}
                onChange={event => setEditDescription(event.target.value)}
                rows={4}
                value={editDescription}
              />
            </label>
            <div className="flex gap-2">
              <button
                className={BTN_PRIMARY}
                disabled={saving || !editTitle.trim() || !editDescription.trim()}
                onClick={() => void saveEdit()}
                type="button"
              >
                {t.save}
              </button>
              <button className={BTN_SUBTLE} onClick={() => setEditing(null)} type="button">
                {t.cancel}
              </button>
            </div>
          </div>
        </SettingCard>
      )}
      {history.length === 0 ? (
        <p className={HINT_TEXT}>{t.historyEmpty}</p>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {history.map(entry => {
            const isCurrent = entry.id === activeScene?.id

            return (
              <SettingCard key={entry.id}>
                {entry.url && (
                  <button
                    aria-label={t.viewOriginal}
                    className="relative block aspect-video w-full cursor-zoom-in"
                    onClick={() => openZoom(entry.url, entry.title || t.historyAltFallback)}
                    type="button"
                  >
                    <img
                      alt={entry.title || t.historyAltFallback}
                      className="h-full w-full object-cover"
                      src={entry.url}
                    />
                    <ZoomIn className="absolute right-2 bottom-2 size-4 text-white" />
                  </button>
                )}
                <div className="space-y-2 p-3">
                  <h4 className="text-xs font-medium">
                    {entry.title || t.untitled}
                    {isCurrent && <span className="ml-2 text-accent">{t.historyCurrentLabel}</span>}
                  </h4>
                  <p className="whitespace-pre-wrap text-xs text-faint">{entry.description || t.needsDescription}</p>
                  <p className={HINT_TEXT}>{t.statuses[entry.status]}</p>
                  {entry.error && <p className="text-xs text-danger-fg">{entry.error}</p>}
                  <div className="flex flex-wrap gap-2">
                    <button
                      className={BTN_SUBTLE}
                      disabled={isCurrent || entry.status !== 'ready'}
                      onClick={() => void handleActivate(entry.id)}
                      type="button"
                    >
                      {t.historyRollbackLabel}
                    </button>
                    {entry.url && (
                      <button className={BTN_SUBTLE} onClick={() => beginEdit(entry)} type="button">
                        {t.edit}
                      </button>
                    )}
                    {entry.status === 'description_failed' && (
                      <button
                        className={BTN_SUBTLE}
                        disabled={generating}
                        onClick={() => void retryAnalysis(entry.id)}
                        type="button"
                      >
                        {t.retryAnalysis}
                      </button>
                    )}
                    {!isCurrent && entry.status !== 'pending' && (
                      <button
                        aria-label={t.historyDeleteAria(entry.id)}
                        className={BTN_SUBTLE}
                        onClick={() => setPendingDelete(entry)}
                        type="button"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              </SettingCard>
            )
          })}
        </div>
      )}
      <div className="flex justify-between">
        <button
          className={BTN_SUBTLE}
          disabled={page === 0}
          onClick={() => void loadSceneLibrary(undefined, page - 1)}
          type="button"
        >
          {t.previous}
        </button>
        <span className={HINT_TEXT}>
          {page + 1} / {Math.max(1, Math.ceil(total / 24))}
        </span>
        <button
          className={BTN_SUBTLE}
          disabled={(page + 1) * 24 >= total}
          onClick={() => void loadSceneLibrary(undefined, page + 1)}
          type="button"
        >
          {t.next}
        </button>
      </div>

      <GroupHeading title={t.policyTitle} />

      <SettingCard>
        <SettingRow
          description={t.policyDesc}
          label={
            <span className="flex items-center gap-1.5">
              <Eye className="size-3.5 text-accent" />
              <span>{t.policyLabel}</span>
            </span>
          }
        >
          <div className="flex items-center gap-2.5">
            <span className="text-[11px] text-faint">
              {policy === 'locked' ? t.policyStatusLocked : t.policyStatusUnlocked}
            </span>
            <Toggle
              ariaLabel={t.policyToggleAria}
              checked={policy !== 'locked'}
              onChange={() => void handleToggleLock()}
            />
          </div>
        </SettingRow>
      </SettingCard>

      <SelfSourceImageFlow
        adopt={adoptSelfSourceImage}
        fetchPrompt={fetchSelfSourcePrompt}
        onClose={() => setSelfSourceOpen(false)}
        onUseAi={() => {
          const epoch = currentClearEpoch()
          setSelfSourceOpen(false)

          void (async () => {
            try {
              if (waitingUpload) {
                await discardPendingScene()
              }

              if (!mounted.current || epoch !== currentClearEpoch()) {
                return
              }

              await createScene({
                notes: notes.trim() || undefined,
                outfit_description: outfitDescription.trim() || undefined,
                ...(reference ? { image: reference.base64, content_type: reference.contentType } : {})
              })
            } catch (error) {
              if (!mounted.current || epoch !== currentClearEpoch()) {
                return
              }

              notify({
                kind: 'warning',
                message: error instanceof Error ? error.message : tToasts.sceneRegenerateFailed
              })
            }
          })()
        }}
        open={selfSourceOpen}
        referenceImages={
          avatarSeeds.fullbodySeedUrl
            ? [{ label: dict.selfSource.refs.fullbodySeed, url: avatarSeeds.fullbodySeedUrl }]
            : undefined
        }
        title={`${t.generateButton} · ${dict.selfSource.open}`}
      />

      <ConfirmDialog
        confirmLabel={t.historyDeleteLabel}
        description={t.historyDeleteConfirmDescription}
        onConfirm={() => handleDeleteScene()}
        onOpenChange={open => {
          if (!open) {
            setPendingDelete(null)
          }
        }}
        open={pendingDelete !== null}
        title={t.historyDeleteConfirmTitle}
        variant="destructive"
      />

      {zoom ? <PortraitLightbox name={zoom.name} onClose={() => setZoom(null)} url={zoom.url} /> : null}
    </SettingsContent>
  )
}
