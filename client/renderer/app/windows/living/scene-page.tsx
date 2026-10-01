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
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { triggerHaptic } from '@/shared/lib/haptics'
import { errorMessage } from '@/shared/lib/ipc-error'
import { registerStorageClearHandler } from '@/shared/lib/storage'
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
  const begin = useAsyncGuard()
  const detail = view.kind === 'detail' ? (details[view.sceneId] ?? null) : null

  useEffect(() => {
    void hydrateAvatarSeeds()
    void hydrateScene()
    void loadSceneLibrary()

    return registerStorageClearHandler(() => {
      setQueryDraft('')
      setCreateDraft(INITIAL_CREATE_DRAFT)
      setEditDrafts({})
      setZoom(null)
      setDeleteTarget(null)
      setView({ kind: 'library' })
      returnScrollTop.current = 0
    })
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

  const openView = (next: ScenePageView): void => {
    if (view.kind === 'library' && scrollRef.current) {
      returnScrollTop.current = scrollRef.current.scrollTop
    }

    setView(next)
  }

  const openDetail = (sceneId: string): void => openView({ kind: 'detail', sceneId })

  const openCreate = (): void => openView({ kind: 'create' })

  const returnToLibrary = (): void => setView({ kind: 'library' })

  const dropEditDraft = (sceneId: string): void => {
    setEditDrafts(current => {
      const next = { ...current }
      delete next[sceneId]

      return next
    })
  }

  const saveInfo = async (sceneId: string): Promise<void> => {
    const draft = editDrafts[sceneId]

    if (!draft) {
      return
    }

    const isLive = begin()
    await editScene(sceneId, draft.title.trim(), draft.description.trim())
    const updated = await loadSceneDetail(sceneId)

    if (updated && isLive()) {
      dropEditDraft(sceneId)
    }
  }

  const regenerate = async (sceneId: string): Promise<void> => {
    await regenerateScene(sceneId)
    await loadSceneDetail(sceneId)
  }

  const saveAndRegenerate = async (sceneId: string): Promise<void> => {
    const isLive = begin()
    const draft = editDrafts[sceneId]

    if (!draft) {
      return
    }

    await saveInfo(sceneId)

    if (!isLive()) {
      return
    }

    await regenerate(sceneId)
  }

  const startCreation = async (): Promise<void> => {
    const isLive = begin()
    triggerHaptic('open')

    const created = await createScene({
      notes: createDraft.notes.trim() || undefined,
      outfit_description: createDraft.outfitDescription.trim() || undefined,
      ...(createDraft.reference
        ? { image: createDraft.reference.base64, content_type: createDraft.reference.contentType }
        : {})
    })

    if (created && isLive()) {
      setCreateDraft(INITIAL_CREATE_DRAFT)
      openDetail(created.id)
    }
  }

  const adoptCreatedImage = async (image: PickedImage): Promise<void> => {
    const isLive = begin()
    await adoptSceneImage(image)
    const created = $pendingScene.get()

    if (created && isLive()) {
      openDetail(created.id)
    }
  }

  const startAiForNewScene = async (): Promise<void> => {
    const isLive = begin()
    const waitingUpload = $pendingScene.get()

    try {
      if (waitingUpload?.stage === 'waiting_upload') {
        await cancelSceneTask(waitingUpload.id)
      }

      if (!isLive()) {
        return
      }

      await startCreation()
    } catch (error) {
      if (isLive()) {
        notify({ kind: 'warning', message: errorMessage(error, tToasts.sceneRegenerateFailed) })
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
    const isLive = begin()
    triggerHaptic('tap')

    try {
      await deleteScene(target.id)

      if (isLive()) {
        dropEditDraft(target.id)
        setDeleteTarget(null)
        returnToLibrary()
        notify({ kind: 'success', message: tToasts.sceneDeleteSuccess })
      }
    } catch (error) {
      if (isLive()) {
        notify({ kind: 'warning', message: errorMessage(error, tToasts.sceneDeleteFailed) })
        throw error
      }
    }
  }

  const handlePolicy = async (): Promise<void> => {
    const isLive = begin()
    const next = policy === 'locked' ? 'llm_may_replace' : 'locked'
    triggerHaptic('selection')

    try {
      await setScenePolicy(next)

      if (isLive()) {
        notify({ kind: 'info', message: next === 'locked' ? tToasts.sceneLocked : tToasts.sceneUnlocked })
      }
    } catch (error) {
      if (isLive()) {
        notify({ kind: 'warning', message: errorMessage(error, tToasts.sceneLockFailed) })
      }
    }
  }

  const handleCreatePrompt = (): Promise<string> =>
    prepareScenePrompt({
      notes: createDraft.notes.trim() || undefined,
      outfit_description: createDraft.outfitDescription.trim() || undefined
    })

  const changeEditDraft = (sceneId: string, next: Partial<SceneEditDraft>): void => {
    setEditDrafts(current => ({ ...current, [sceneId]: { ...current[sceneId], ...next } }))
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
              onCancelEdit={() => dropEditDraft(view.sceneId)}
              onCancelTask={async sceneId => {
                await cancelSceneTask(sceneId)
                await loadSceneDetail(sceneId)
              }}
              onChangeDraft={next => changeEditDraft(view.sceneId, next)}
              onDelete={scene => setDeleteTarget(scene)}
              onEdit={() => {
                if (detail) {
                  changeEditDraft(detail.id, { title: detail.title, description: detail.description })
                }
              }}
              onLoad={async () => {
                await hydrateScene()
                await loadSceneDetail(view.sceneId)
              }}
              onRegenerate={regenerate}
              onRetryAnalysis={async sceneId => {
                await analyzeScene(sceneId)
                await loadSceneDetail(sceneId)
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
              onCancelTask={cancelSceneTask}
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
