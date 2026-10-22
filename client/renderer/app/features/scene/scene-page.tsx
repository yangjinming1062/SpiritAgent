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
  inspectSceneCreationIssue,
  loadSceneDetail,
  loadSceneLibrary,
  prepareScenePrompt,
  refreshSceneStatus,
  regenerateScene,
  reloadSceneImage,
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
import { failureText } from '@/shared/lib/ipc-error'
import { registerStorageClearHandler, registerStorageRestoreHandler } from '@/shared/lib/storage'
import { confirmAction, SettingsContent } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

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
  const [policyBusy, setPolicyBusy] = useState(false)
  const createDraft: SceneCreateDraft = { notes: ui.notes, reference }
  const editDrafts = ui.editDrafts
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailLoadError, setDetailLoadError] = useState<string | null>(null)
  const [zoom, setZoom] = useState<ImageZoom | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const returnScrollTop = useRef(ui.listScrollTop ?? 0)
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
    const isLive = begin()
    setDetailLoading(true)
    setDetailLoadError(null)
    void refreshSceneStatus(view.sceneId)
      .catch(error => {
        if (current && isLive()) {
          setDetailLoadError(failureText(error, tToasts.sceneStatusRefreshFailed))
        }
      })
      .finally(() => {
        if (current && isLive()) {
          setDetailLoading(false)
        }
      })

    return () => {
      current = false
    }
  }, [begin, tToasts.sceneStatusRefreshFailed, view])

  const openView = (next: ScenePageView): void => {
    creationRevision.current += 1

    if (view.kind === 'library' && scrollRef.current) {
      returnScrollTop.current = scrollRef.current.scrollTop
      updateSceneUi({ listScrollTop: returnScrollTop.current })
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

    if (isLive() && $sceneUi.get().editDrafts[sceneId] === draft) {
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
      const result = await createScene({
        notes: input.notes.trim() || undefined,
        ...(input.reference ? { image: input.reference.base64, content_type: input.reference.contentType } : {})
      })

      if (result.kind === 'created' && isLive() && revision === creationRevision.current) {
        changeCreateDraft(INITIAL_CREATE_DRAFT)
        openDetail(result.scene.id)
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
        notifyError(error, tToasts.sceneRegenerateFailed)
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

  const handleDelete = async (target: SceneAsset): Promise<void> => {
    const isLive = begin()
    triggerHaptic('tap')

    try {
      await deleteScene(target.id)

      if (isLive()) {
        dropEditDraft(target.id)
        returnToLibrary()
        notify({ kind: 'success', message: tToasts.sceneDeleteSuccess })
      }
    } catch (error) {
      if (isLive()) {
        notifyError(error, tToasts.sceneDeleteFailed)
      }
    }
  }

  const handlePolicy = async (): Promise<void> => {
    if (policyBusy) {
      return
    }

    setPolicyBusy(true)
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
        notifyError(error, tToasts.sceneLockFailed)
      }
    } finally {
      if (isLive()) {
        setPolicyBusy(false)
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
      {view.kind === 'library' ? (
        <SettingsContent scrollRef={scrollRef}>
          <div className="space-y-4">
            <SceneLibraryView
              activeScene={activeScene}
              entries={library}
              onActivate={sceneId => void handleActivate(sceneId)}
              onCheckStatus={async () => {
                const isLive = begin()
                await inspectSceneCreationIssue()

                if (isLive()) {
                  setQueryDraft('')
                  returnScrollTop.current = 0
                  updateSceneUi({ listScrollTop: 0 })

                  if (scrollRef.current) {
                    scrollRef.current.scrollTop = 0
                  }
                }
              }}
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
              policyBusy={policyBusy}
              query={queryDraft}
              regenerating={regenerating}
              slow={slow}
              status={libraryStatus}
              total={total}
            />
          </div>
        </SettingsContent>
      ) : view.kind === 'detail' ? (
        <SceneDetailView
          activeScene={activeScene}
          busy={status !== 'none' || Boolean(regenerating && regenerating.id !== view.sceneId)}
          detail={detail}
          draft={editDrafts[view.sceneId]}
          key={view.sceneId}
          loadError={detailLoadError}
          loading={detailLoading}
          onActivate={sceneId => void handleActivate(sceneId)}
          onBack={returnToLibrary}
          onCancelEdit={() => dropEditDraft(view.sceneId)}
          onCancelTask={async sceneId => {
            await cancelSceneTask(sceneId)
          }}
          onChangeDraft={next => changeEditDraft(view.sceneId, next)}
          onDelete={async scene => {
            const confirmed = await confirmAction({
              confirmLabel: dict.living.scene.historyDeleteLabel,
              description: dict.living.scene.historyDeleteConfirmDescription,
              title: dict.living.scene.historyDeleteConfirmTitle,
              variant: 'destructive'
            })

            if (confirmed) {
              await handleDelete(scene)
            }
          }}
          onEdit={() => {
            if (detail) {
              changeEditDraft(detail.id, { title: detail.title, description: detail.description })
            }
          }}
          onLoad={async () => {
            const isLive = begin()
            const loaded = await refreshSceneStatus(view.sceneId)
            const currentView = $sceneUi.get().view

            if (loaded && isLive() && currentView.kind === 'detail' && currentView.sceneId === view.sceneId) {
              setDetailLoadError(null)
            }

            return loaded
          }}
          onRegenerate={regenerate}
          onRetryAnalysis={async sceneId => {
            await analyzeScene(sceneId)
          }}
          onRetryImage={() => reloadSceneImage(view.sceneId)}
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
          onCancelTask={async sceneId => {
            await cancelSceneTask(sceneId)
          }}
          onChange={changeCreateDraft}
          onCheckStatus={async () => {
            const isLive = begin()
            await inspectSceneCreationIssue()

            if (isLive()) {
              setQueryDraft('')
              returnScrollTop.current = 0
              updateSceneUi({ listScrollTop: 0 })
              returnToLibrary()
            }
          }}
          onCreate={() => void startCreation()}
          onFetchPrompt={handleCreatePrompt}
          onUseAi={() => void startAiForNewScene()}
        />
      )}
      {zoom ? <PortraitLightbox name={zoom.name} onClose={() => setZoom(null)} url={zoom.url} /> : null}
    </>
  )
}
