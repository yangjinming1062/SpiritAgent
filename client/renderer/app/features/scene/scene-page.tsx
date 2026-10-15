import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { PickedImage } from '@/modules/character'
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
  refreshSceneStatus,
  regenerateScene,
  type SceneAsset,
  setScenePolicy
} from '@/modules/scene'
import { PortraitLightbox } from '@/shared'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import {
  loadCreationDraftImage,
  saveCreationDraftImage,
  subscribeCreationDraftImage
} from '@/shared/lib/creation-draft-image'
import { triggerHaptic } from '@/shared/lib/haptics'
import { registerStorageClearHandler, registerStorageRestoreHandler } from '@/shared/lib/storage'
import { ConfirmDialog, SettingsContent } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

import { type SceneCreateDraft, SceneCreateView } from './scene-create-view'
import { SceneDetailView, type SceneEditDraft } from './scene-detail-view'
import { SceneLibraryView } from './scene-library-view'
import { $sceneUi, $sceneUiImportRevision, type ScenePageView, updateSceneUi } from './scene-ui-state'

interface ImageZoom {
  name: string
  url: string
}

const INITIAL_CREATE_DRAFT: SceneCreateDraft = { notes: '', reference: null }

interface ScenePageProps {
  startCreating?: boolean
  onMaterialReady?: (sceneId: string) => void
}

export function ScenePage({ startCreating = false, onMaterialReady }: ScenePageProps = {}): React.JSX.Element {
  const ui = useStore($sceneUi)
  const importRevision = useStore($sceneUiImportRevision)
  const observedImportRevision = useRef(importRevision)
  const authKind = useStore($auth).kind
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
  const view = ui.view
  const setView = (next: ScenePageView): void => updateSceneUi({ view: next })
  const [queryDraft, setQueryDraft] = useState(query)
  const [reference, setReference] = useState<PickedImage | null>(null)
  const [referenceLoading, setReferenceLoading] = useState(true)
  const [referenceImportRevision, setReferenceImportRevision] = useState(0)
  const referenceRevision = useRef(0)
  const creationRevision = useRef(0)
  const submittingRef = useRef(false)
  const [submitting, setSubmitting] = useState(false)
  const createDraft: SceneCreateDraft = { notes: ui.notes, reference }
  const editDrafts = ui.editDrafts
  const [detailLoading, setDetailLoading] = useState(false)
  const [zoom, setZoom] = useState<ImageZoom | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SceneAsset | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const returnScrollTop = useRef(0)
  const begin = useAsyncGuard()
  const detail = view.kind === 'detail' ? (details[view.sceneId] ?? null) : null

  useEffect(() => {
    if (startCreating) {
      updateSceneUi({ view: { kind: 'create' } })
    }
  }, [startCreating])

  const restoreReference = useCallback((): void => {
    const revision = ++referenceRevision.current
    const isLive = begin()
    setReferenceLoading(true)
    void loadCreationDraftImage('background').then(image => {
      if (isLive() && revision === referenceRevision.current) {
        setReference(image)
        setReferenceLoading(false)
      }
    })
  }, [begin])

  useEffect(() => {
    if (authKind === 'authenticated') {
      restoreReference()
    }

    const restore = registerStorageRestoreHandler(restoreReference)

    const changed = subscribeCreationDraftImage('background', () => {
      creationRevision.current += 1
      setReferenceImportRevision(revision => revision + 1)
      restoreReference()
    })

    return () => {
      restore()
      changed()
    }
  }, [authKind, restoreReference])

  useEffect(() => {
    if (observedImportRevision.current === importRevision) {
      return
    }

    observedImportRevision.current = importRevision
    creationRevision.current += 1
    setZoom(null)
    setDeleteTarget(null)
    restoreReference()
  }, [importRevision, restoreReference])

  const changeCreateDraft = (next: Partial<SceneCreateDraft>): void => {
    creationRevision.current += 1

    if (next.notes !== undefined) {
      updateSceneUi({ notes: next.notes })
    }

    if (next.reference !== undefined) {
      referenceRevision.current += 1
      setReference(next.reference)
      void saveCreationDraftImage('background', next.reference)
    }
  }

  useEffect(() => {
    if (authKind !== 'authenticated') {
      return
    }

    // 首屏并行拉列表并跳过其尾部同刻重复刷新；state 失败时此路是列表唯一来源。
    void hydrateScene({ skipLibraryRefresh: true })
    void loadSceneLibrary()
  }, [authKind])

  useEffect(() => {
    return registerStorageClearHandler(() => {
      setQueryDraft('')
      referenceRevision.current += 1
      creationRevision.current += 1
      submittingRef.current = false
      setSubmitting(false)
      setReference(null)
      setZoom(null)
      setDeleteTarget(null)
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
    void refreshSceneStatus(view.sceneId)
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
    creationRevision.current += 1

    if (view.kind === 'library' && scrollRef.current) {
      returnScrollTop.current = scrollRef.current.scrollTop
    }

    setView(next)
  }

  const openDetail = (sceneId: string): void => openView({ kind: 'detail', sceneId })

  const openCreate = (): void => openView({ kind: 'create' })

  const returnToLibrary = (): void => openView({ kind: 'library' })

  const dropEditDraft = (sceneId: string): void => {
    const next = { ...$sceneUi.get().editDrafts }
    delete next[sceneId]
    updateSceneUi({ editDrafts: next })
  }

  const saveInfo = async (sceneId: string): Promise<boolean> => {
    const draft = $sceneUi.get().editDrafts[sceneId]

    if (!draft) {
      return false
    }

    const isLive = begin()
    await editScene(sceneId, draft.title.trim(), draft.description.trim())
    const updated = await loadSceneDetail(sceneId)

    if (updated && isLive() && $sceneUi.get().editDrafts[sceneId] === draft) {
      dropEditDraft(sceneId)

      return true
    }

    return false
  }

  const regenerate = async (sceneId: string): Promise<void> => {
    await regenerateScene(sceneId)
    await loadSceneDetail(sceneId)
  }

  const saveAndRegenerate = async (sceneId: string): Promise<void> => {
    const isLive = begin()
    const draft = $sceneUi.get().editDrafts[sceneId]
    const revision = creationRevision.current

    if (!draft) {
      return
    }

    const saved = await saveInfo(sceneId)

    if (!saved || !isLive() || revision !== creationRevision.current) {
      return
    }

    await regenerate(sceneId)
  }

  const startCreation = async (input = createDraft, revision = creationRevision.current): Promise<void> => {
    if (revision !== creationRevision.current) {
      return
    }

    if (submittingRef.current || referenceLoading) {
      return
    }

    submittingRef.current = true
    setSubmitting(true)
    const isLive = begin()
    triggerHaptic('open')

    try {
      const created = await createScene({
        notes: input.notes.trim() || undefined,
        ...(input.reference ? { image: input.reference.base64, content_type: input.reference.contentType } : {})
      })

      if (created && isLive() && revision === creationRevision.current) {
        changeCreateDraft(INITIAL_CREATE_DRAFT)
        openDetail(created.id)
      }
    } finally {
      if (isLive()) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  const adoptCreatedImage = async (image: PickedImage, sceneId?: string): Promise<void> => {
    if (submittingRef.current) {
      return
    }

    submittingRef.current = true
    setSubmitting(true)
    const isLive = begin()
    const revision = creationRevision.current

    try {
      const createdId = await adoptSceneImage(image, sceneId)

      if (createdId && isLive() && revision === creationRevision.current) {
        changeCreateDraft(INITIAL_CREATE_DRAFT)
        openDetail(createdId)
      }
    } finally {
      if (isLive()) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  const startAiForNewScene = async (): Promise<void> => {
    const isLive = begin()
    const input = createDraft
    const revision = creationRevision.current
    const waitingUpload = $pendingScene.get()

    try {
      if (waitingUpload?.stage === 'waiting_upload') {
        await cancelSceneTask(waitingUpload.id)
      }

      if (!isLive() || revision !== creationRevision.current) {
        return
      }

      await startCreation(input, revision)
    } catch (error) {
      if (isLive()) {
        notify({ kind: 'warning', message: errorMessage(error, tToasts.sceneRegenerateFailed) })
      }
    }
  }

  const handleActivate = async (sceneId: string): Promise<void> => {
    triggerHaptic('tap')

    if (onMaterialReady) {
      onMaterialReady(sceneId)

      return
    }

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

  const handleCreatePrompt = (): Promise<{ id: string; prompt: string }> =>
    prepareScenePrompt({ notes: createDraft.notes.trim() || undefined })

  const changeEditDraft = (sceneId: string, next: Partial<SceneEditDraft>): void => {
    const current = $sceneUi.get().editDrafts
    updateSceneUi({ editDrafts: { ...current, [sceneId]: { ...current[sceneId], ...next } } })
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
              onLoad={() => refreshSceneStatus(view.sceneId)}
              onRegenerate={regenerate}
              onRetryAnalysis={async sceneId => {
                await analyzeScene(sceneId)
                await loadSceneDetail(sceneId)
              }}
              onSave={async sceneId => {
                await saveInfo(sceneId)
              }}
              onSaveAndRegenerate={saveAndRegenerate}
              onZoom={(url, name) => setZoom({ url, name })}
              selectingMaterial={Boolean(onMaterialReady)}
            />
          ) : (
            <SceneCreateView
              busy={busy || referenceLoading || submitting}
              draft={createDraft}
              key={`${importRevision}:${referenceImportRevision}`}
              onAdopt={adoptCreatedImage}
              onBack={returnToLibrary}
              onCancelTask={cancelSceneTask}
              onChange={changeCreateDraft}
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
