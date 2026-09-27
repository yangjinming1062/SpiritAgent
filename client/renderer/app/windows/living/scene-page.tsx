import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { hydrateAvatarSeeds, type PickedImage } from '@/modules/character'
import {
  $activeScene,
  $pendingScene,
  $sceneDetails,
  $sceneLibrary,
  $sceneLibraryStatus,
  $scenePage,
  $scenePolicy,
  $sceneQuery,
  $sceneRegenerating,
  $sceneTaskSlow,
  $sceneTaskStatus,
  $sceneTotal,
  activateScene,
  adoptSceneImage,
  analyzeScene,
  cancelSceneTask,
  createScene,
  deleteScene,
  editScene,
  hydrateScene,
  loadSceneDetail,
  loadSceneLibrary,
  prepareScenePrompt,
  regenerateScene,
  type SceneAsset,
  setScenePolicy
} from '@/modules/scene'
import { PortraitLightbox } from '@/shared'
import { triggerHaptic } from '@/shared/lib/haptics'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { ConfirmDialog, SettingsContent } from '@/shared/panel'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { type SceneCreateDraft, SceneCreateView } from './scene-create-view'
import { SceneDetailView, type SceneEditDraft } from './scene-detail-view'
import { SceneLibraryView } from './scene-library-view'

type ScenePageView = { kind: 'create' } | { kind: 'detail'; sceneId: string } | { kind: 'library' }
interface ImageZoom {
  name: string
  url: string
}

const INITIAL_CREATE_DRAFT: SceneCreateDraft = { notes: '', outfitDescription: '', reference: null }

export function ScenePage(): React.JSX.Element {
  const library = useStore($sceneLibrary)
  const libraryStatus = useStore($sceneLibraryStatus)
  const details = useStore($sceneDetails)
  const activeScene = useStore($activeScene)
  const pending = useStore($pendingScene)
  const regenerating = useStore($sceneRegenerating)
  const slow = useStore($sceneTaskSlow)
  const total = useStore($sceneTotal)
  const page = useStore($scenePage)
  const query = useStore($sceneQuery)
  const policy = useStore($scenePolicy)
  const status = useStore($sceneTaskStatus)
  const dict = useStrings()
  const tToasts = dict.living.toasts
  const [view, setView] = useState<ScenePageView>({ kind: 'library' })
  const [queryDraft, setQueryDraft] = useState(query)
  const [createDraft, setCreateDraft] = useState(INITIAL_CREATE_DRAFT)
  const [editDrafts, setEditDrafts] = useState<Record<string, SceneEditDraft>>({})
  const [detailLoading, setDetailLoading] = useState(false)
  const [zoom, setZoom] = useState<ImageZoom | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SceneAsset | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const returnScrollTop = useRef(0)
  const mounted = useRef(true)
  const detail = view.kind === 'detail' ? (details[view.sceneId] ?? null) : null

  useEffect(() => {
    mounted.current = true
    void hydrateAvatarSeeds()
    void hydrateScene()
    void loadSceneLibrary()

    const unregister = registerStorageClearHandler(() => {
      setQueryDraft('')
      setCreateDraft(INITIAL_CREATE_DRAFT)
      setEditDrafts({})
      setZoom(null)
      setDeleteTarget(null)
      setView({ kind: 'library' })
      returnScrollTop.current = 0
    })

    return () => {
      mounted.current = false
      unregister()
    }
  }, [])

  useLayoutEffect(() => {
    if (view.kind === 'library' && scrollRef.current) {
      scrollRef.current.scrollTop = returnScrollTop.current
    }
  }, [view])

  useEffect(() => {
    if (view.kind !== 'detail') {
      setDetailLoading(false)

      return
    }

    let current = true
    setDetailLoading(true)
    void loadSceneDetail(view.sceneId)
      .catch(() => null)
      .finally(() => {
        if (current) {
          setDetailLoading(false)
        }
      })

    return () => {
      current = false
    }
  }, [view])

  useEffect(() => {
    if (view.kind !== 'detail' || !detail) {
      return
    }

    setEditDrafts(current => {
      const existing = current[view.sceneId]

      if (existing?.editing || (existing?.title === detail.title && existing.description === detail.description)) {
        return current
      }

      return {
        ...current,
        [view.sceneId]: { editing: false, title: detail.title, description: detail.description }
      }
    })
  }, [detail, editDrafts, view])

  const openDetail = (sceneId: string): void => {
    if (view.kind === 'library' && scrollRef.current) {
      returnScrollTop.current = scrollRef.current.scrollTop
    }

    setView({ kind: 'detail', sceneId })
  }

  const openCreate = (): void => {
    if (view.kind === 'library' && scrollRef.current) {
      returnScrollTop.current = scrollRef.current.scrollTop
    }

    setView({ kind: 'create' })
  }

  const returnToLibrary = (): void => setView({ kind: 'library' })

  const updateDetail = async (sceneId: string): Promise<SceneAsset | null> => await loadSceneDetail(sceneId)

  const saveInfo = async (sceneId: string): Promise<void> => {
    const draft = editDrafts[sceneId]

    if (!draft) {
      return
    }

    await editScene(sceneId, draft.title.trim(), draft.description.trim())
    const updated = await updateDetail(sceneId)

    if (updated && mounted.current) {
      setEditDrafts(current => ({
        ...current,
        [sceneId]: { editing: false, title: updated.title, description: updated.description }
      }))
    }
  }

  const regenerate = async (sceneId: string): Promise<void> => {
    await regenerateScene(sceneId)
    await updateDetail(sceneId)
  }

  const saveAndRegenerate = async (sceneId: string): Promise<void> => {
    const epoch = currentClearEpoch()
    const draft = editDrafts[sceneId]

    if (!draft) {
      return
    }

    await saveInfo(sceneId)

    if (!mounted.current || epoch !== currentClearEpoch()) {
      return
    }

    await regenerate(sceneId)
  }

  const startCreation = async (): Promise<void> => {
    triggerHaptic('open')

    const created = await createScene({
      notes: createDraft.notes.trim() || undefined,
      outfit_description: createDraft.outfitDescription.trim() || undefined,
      ...(createDraft.reference
        ? { image: createDraft.reference.base64, content_type: createDraft.reference.contentType }
        : {})
    })

    if (created && mounted.current) {
      setCreateDraft(INITIAL_CREATE_DRAFT)
      openDetail(created.id)
    }
  }

  const adoptCreatedImage = async (image: PickedImage): Promise<void> => {
    await adoptSceneImage(image)
    const created = $pendingScene.get()

    if (created && mounted.current) {
      openDetail(created.id)
    }
  }

  const startAiForNewScene = async (): Promise<void> => {
    const epoch = currentClearEpoch()
    const waitingUpload = $pendingScene.get()

    try {
      if (waitingUpload?.stage === 'waiting_upload') {
        await cancelSceneTask(waitingUpload.id)
      }

      if (!mounted.current || currentClearEpoch() !== epoch) {
        return
      }

      await startCreation()
    } catch (error) {
      if (mounted.current && epoch === currentClearEpoch()) {
        notify({
          kind: 'warning',
          message: error instanceof Error ? error.message : tToasts.sceneRegenerateFailed
        })
      }
    }
  }

  const handleActivate = async (sceneId: string): Promise<void> => {
    triggerHaptic('tap')
    await activateScene(sceneId)
  }

  const handleDelete = async (): Promise<void> => {
    if (!deleteTarget) {
      return
    }

    const target = deleteTarget
    const epoch = currentClearEpoch()
    triggerHaptic('tap')

    try {
      await deleteScene(target.id)

      if (mounted.current && currentClearEpoch() === epoch) {
        setEditDrafts(current => {
          const next = { ...current }
          delete next[target.id]

          return next
        })
        setDeleteTarget(null)
        returnToLibrary()
        notify({ kind: 'success', message: tToasts.sceneDeleteSuccess })
      }
    } catch (error) {
      if (mounted.current && currentClearEpoch() === epoch) {
        notify({ kind: 'warning', message: error instanceof Error ? error.message : tToasts.sceneDeleteFailed })
        throw error
      }
    }
  }

  const handlePolicy = async (): Promise<void> => {
    const epoch = currentClearEpoch()
    const next = policy === 'locked' ? 'llm_may_replace' : 'locked'
    triggerHaptic('selection')

    try {
      await setScenePolicy(next)

      if (mounted.current && epoch === currentClearEpoch()) {
        notify({ kind: 'info', message: next === 'locked' ? tToasts.sceneLocked : tToasts.sceneUnlocked })
      }
    } catch (error) {
      if (mounted.current && epoch === currentClearEpoch()) {
        notify({ kind: 'warning', message: error instanceof Error ? error.message : tToasts.sceneLockFailed })
      }
    }
  }

  const handleCreatePrompt = async (): Promise<string> =>
    await prepareScenePrompt({
      notes: createDraft.notes.trim() || undefined,
      outfit_description: createDraft.outfitDescription.trim() || undefined
    })

  const changeEditDraft = (sceneId: string, next: Partial<SceneEditDraft>): void => {
    setEditDrafts(current => {
      const draft = current[sceneId] ?? {
        editing: true,
        title: detail?.title ?? '',
        description: detail?.description ?? ''
      }

      return { ...current, [sceneId]: { ...draft, ...next } }
    })
  }

  const busy = status !== 'none' || regenerating !== null

  return (
    <>
      <SettingsContent contentClassName={view.kind === 'detail' ? 'max-w-6xl' : undefined} scrollRef={scrollRef}>
        <div className="space-y-4">
          {view.kind === 'library' ? (
            <SceneLibraryView
              activeScene={activeScene}
              entries={library}
              onActivate={sceneId => void handleActivate(sceneId)}
              onCreate={openCreate}
              onOpen={openDetail}
              onPolicyChange={() => void handlePolicy()}
              onQueryChange={setQueryDraft}
              onRetryLoad={() => void loadSceneLibrary()}
              onSearch={() => void loadSceneLibrary(queryDraft, 0)}
              onSetPage={nextPage => void loadSceneLibrary(undefined, nextPage)}
              page={page}
              pending={pending}
              policy={policy}
              query={queryDraft}
              regenerating={regenerating}
              slow={slow}
              status={libraryStatus}
              taskBusy={busy}
              total={total}
            />
          ) : view.kind === 'detail' ? (
            <SceneDetailView
              activeScene={activeScene}
              busy={status !== 'none' || Boolean(regenerating && regenerating.id !== view.sceneId)}
              detail={detail}
              draft={editDrafts[view.sceneId]}
              key={view.sceneId}
              loading={detailLoading}
              onActivate={sceneId => void handleActivate(sceneId)}
              onBack={returnToLibrary}
              onCancelTask={async sceneId => {
                await cancelSceneTask(sceneId)
                await updateDetail(sceneId)
              }}
              onChangeDraft={next => changeEditDraft(view.sceneId, next)}
              onDelete={scene => setDeleteTarget(scene)}
              onEdit={() => {
                if (detail) {
                  changeEditDraft(detail.id, { editing: true, title: detail.title, description: detail.description })
                }
              }}
              onLoad={async () => {
                await hydrateScene()
                await updateDetail(view.sceneId)
              }}
              onRegenerate={regenerate}
              onRetryAnalysis={async sceneId => {
                await analyzeScene(sceneId)
                await updateDetail(sceneId)
              }}
              onSave={saveInfo}
              onSaveAndRegenerate={saveAndRegenerate}
              onZoom={(url, name) => setZoom({ url, name })}
            />
          ) : (
            <SceneCreateView
              busy={busy}
              draft={createDraft}
              onAdopt={adoptCreatedImage}
              onBack={returnToLibrary}
              onCancelTask={async sceneId => {
                await cancelSceneTask(sceneId)
              }}
              onChange={next => setCreateDraft(current => ({ ...current, ...next }))}
              onCreate={() => void startCreation()}
              onFetchPrompt={handleCreatePrompt}
              onUseAi={() => void startAiForNewScene()}
            />
          )}
        </div>
      </SettingsContent>
      {zoom ? <PortraitLightbox name={zoom.name} onClose={() => setZoom(null)} url={zoom.url} /> : null}
      <ConfirmDialog
        confirmLabel={dict.living.scene.historyDeleteLabel}
        description={dict.living.scene.historyDeleteConfirmDescription}
        onConfirm={handleDelete}
        onOpenChange={open => {
          if (!open) {
            setDeleteTarget(null)
          }
        }}
        open={deleteTarget !== null}
        title={dict.living.scene.historyDeleteConfirmTitle}
        variant="destructive"
      />
    </>
  )
}
