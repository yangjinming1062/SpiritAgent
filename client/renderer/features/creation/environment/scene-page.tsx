import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useDetailFlow } from '@/app/navigation/use-detail-flow'
import type { PickedImage } from '@/features/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useCreationSubmit } from '@/shared/hooks/use-creation-submit'
import { usePanelAction } from '@/shared/hooks/use-panel-action'
import {
  loadCreationDraftImage,
  saveCreationDraftImage,
  subscribeCreationDraftImage
} from '@/shared/lib/creation-draft-image'
import { triggerHaptic } from '@/shared/lib/haptics'
import { failureText } from '@/shared/lib/ipc-error'
import { registerStorageClearHandler, registerStorageRestoreHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import { PortraitLightbox } from '@/ui'
import { confirmAction, SettingsContent } from '@/ui'

import { type SceneCreateDraft, SceneCreateView } from './scene-create-view'
import { SceneDetailView, type SceneEditDraft } from './scene-detail-view'
import { SceneLibraryView } from './scene-library-view'
import {
  $pendingScene,
  $sceneDetails,
  $sceneHasMore,
  $sceneLibrary,
  $sceneLibraryStatus,
  $scenePage,
  $scenePolicy,
  $sceneQuery,
  $sceneRegenerating,
  $sceneTaskSlow,
  $sceneTaskStatus,
  $sceneTotal,
  adoptSceneImage,
  analyzeScene,
  cancelSceneTask,
  createScene,
  deleteScene,
  editScene,
  hydrateScene,
  inspectSceneCreationIssue,
  loadSceneLibrary,
  prepareScenePrompt,
  refreshSceneStatus,
  regenerateScene,
  reloadSceneImage,
  type SceneAsset,
  setScenePolicy
} from './scene-store'
import { type ScenePageView } from './scene-ui-state'
import { $sceneUi, updateSceneUi } from './scene-ui-state'

interface ImageZoom {
  name: string
  url: string
}

const INITIAL_CREATE_DRAFT: SceneCreateDraft = { notes: '', reference: null }

export function ScenePage(): React.JSX.Element {
  const authKind = useStore($auth).kind
  const library = useStore($sceneLibrary)
  const libraryStatus = useStore($sceneLibraryStatus)
  const details = useStore($sceneDetails)
  const pending = useStore($pendingScene)
  const regenerating = useStore($sceneRegenerating)
  const slow = useStore($sceneTaskSlow)
  const total = useStore($sceneTotal)
  const hasMore = useStore($sceneHasMore)
  const page = useStore($scenePage)
  const query = useStore($sceneQuery)
  const policy = useStore($scenePolicy)
  const status = useStore($sceneTaskStatus)
  const dict = useStrings()
  const tToasts = dict.living.toasts

  // 视图随组件生命周期；导航指向环境分区时才消费 sceneId 直达，离开的导航不回退视图。
  const {
    detail: view,
    setDetail: setView,
    scrollRef
  } = useDetailFlow<ScenePageView>({
    select: target =>
      target.location.page === 'creation' && target.location.section === 'environment'
        ? target.focus.page === 'creation' && target.focus.sceneId
          ? { kind: 'detail', sceneId: target.focus.sceneId }
          : { kind: 'library' }
        : undefined,
    fallback: { kind: 'library' },
    isDetailOpen: value => value.kind !== 'library'
  })

  const [queryDraft, setQueryDraft] = useState(query)
  const [reference, setReference] = useState<PickedImage | null>(null)
  const [referenceLoading, setReferenceLoading] = useState(true)
  const [referenceImportRevision, setReferenceImportRevision] = useState(0)
  const referenceRevision = useRef(0)
  const createDraft: SceneCreateDraft = { notes: $sceneUi.get().notes, reference }
  const editDrafts = $sceneUi.get().editDrafts
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailLoadError, setDetailLoadError] = useState<string | null>(null)
  const [zoom, setZoom] = useState<ImageZoom | null>(null)
  // await 期间视图可能已切换；闭包值会过期，用 ref 读取最新视图做守卫。
  const viewRef = useRef(view)
  viewRef.current = view
  const begin = useAsyncGuard()
  const { run, busyKey } = usePanelAction()
  const submitGuard = useCreationSubmit()
  const detail = view.kind === 'detail' ? (details[view.sceneId] ?? null) : null

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
      submitGuard.bumpRevision()
      setReferenceImportRevision(revision => revision + 1)
      restoreReference()
    })

    return () => {
      restore()
      changed()
    }
  }, [authKind, restoreReference, submitGuard])

  const changeCreateDraft = (next: Partial<SceneCreateDraft>): void => {
    submitGuard.bumpRevision()

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
      setReference(null)
      setZoom(null)
    })
  }, [])

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
    submitGuard.bumpRevision()
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

  const saveAndRegenerate = async (sceneId: string): Promise<void> => {
    const isLive = begin()
    const draft = $sceneUi.get().editDrafts[sceneId]
    const revision = submitGuard.currentRevision()

    if (!draft) {
      return
    }

    const saved = await saveInfo(sceneId)

    if (!saved || !isLive() || revision !== submitGuard.currentRevision()) {
      return
    }

    await regenerateScene(sceneId)
  }

  // 创建提交：createScene 自身持有提交互斥与失败呈现（creationIssue），页面只管跳转与草稿清理。
  const startCreation = async (input = createDraft, revision = submitGuard.currentRevision()): Promise<void> => {
    if (revision !== submitGuard.currentRevision() || !submitGuard.beginSubmit(() => referenceLoading)) {
      return
    }

    const isLive = begin()
    triggerHaptic('open')

    try {
      const result = await createScene({
        notes: input.notes.trim() || undefined,
        ...(input.reference ? { image: input.reference.base64 } : {})
      })

      if (result.kind === 'created' && isLive() && revision === submitGuard.currentRevision()) {
        changeCreateDraft(INITIAL_CREATE_DRAFT)
        openDetail(result.sceneId)
      }
    } finally {
      submitGuard.endSubmit(revision)
    }
  }

  const adoptCreatedImage = async (image: PickedImage, sceneId?: string): Promise<void> => {
    if (!submitGuard.beginSubmit()) {
      return
    }

    const isLive = begin()
    const revision = submitGuard.currentRevision()

    try {
      const createdId = await adoptSceneImage(image, sceneId)

      if (createdId && isLive() && revision === submitGuard.currentRevision()) {
        changeCreateDraft(INITIAL_CREATE_DRAFT)
        openDetail(createdId)
      }
    } finally {
      submitGuard.endSubmit(revision)
    }
  }

  const startAiForNewScene = async (): Promise<void> => {
    const isLive = begin()
    const input = createDraft
    const revision = submitGuard.currentRevision()
    const waitingUpload = $pendingScene.get()

    try {
      if (waitingUpload?.stage === 'waiting_upload') {
        await cancelSceneTask(waitingUpload.id)
      }

      if (!isLive() || revision !== submitGuard.currentRevision()) {
        return
      }

      await startCreation(input, revision)
    } catch (error) {
      if (isLive()) {
        notifyError(error, tToasts.sceneRegenerateFailed)
      }
    }
  }

  const handleDelete = (target: SceneAsset): void => {
    void run(
      async () => {
        await deleteScene(target.id)
        dropEditDraft(target.id)
        returnToLibrary()
        notify({ kind: 'success', message: tToasts.sceneDeleteSuccess })
      },
      { failure: tToasts.sceneDeleteFailed }
    )
  }

  const handlePolicy = (): void => {
    const next = policy === 'locked' ? 'llm_may_replace' : 'locked'
    triggerHaptic('selection')

    void run(
      async () => {
        await setScenePolicy(next)
        notify({ kind: 'info', message: next === 'locked' ? tToasts.sceneLocked : tToasts.sceneUnlocked })
      },
      { failure: tToasts.sceneLockFailed, key: 'policy' }
    )
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
              entries={library}
              hasMore={hasMore}
              onCheckStatus={async () => {
                await inspectSceneCreationIssue()
                setQueryDraft('')
              }}
              onCreate={openCreate}
              onOpen={openDetail}
              onPolicyChange={handlePolicy}
              onQueryChange={setQueryDraft}
              onRetryLoad={() => void loadSceneLibrary()}
              onSearch={() => void loadSceneLibrary(queryDraft, 0)}
              onSetPage={nextPage => void loadSceneLibrary(undefined, nextPage)}
              page={page}
              pending={pending}
              policy={policy}
              policyBusy={busyKey === 'policy'}
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
          busy={status !== 'none' || Boolean(regenerating && regenerating.id !== view.sceneId)}
          detail={detail}
          draft={editDrafts[view.sceneId]}
          key={view.sceneId}
          loadError={detailLoadError}
          loading={detailLoading}
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
              handleDelete(scene)
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

            // await 期间视图可能已切换（viewRef 持最新值），仅当仍停在同一详情时清除错误。
            if (loaded && isLive() && viewRef.current.kind === 'detail' && viewRef.current.sceneId === view.sceneId) {
              setDetailLoadError(null)
            }

            return loaded
          }}
          onRegenerate={regenerateScene}
          onRetryAnalysis={async sceneId => {
            await analyzeScene(sceneId)
          }}
          onRetryImage={() => reloadSceneImage(view.sceneId)}
          onSave={async sceneId => {
            await saveInfo(sceneId)
          }}
          onSaveAndRegenerate={saveAndRegenerate}
          onZoom={(url, name) => setZoom({ url, name })}
        />
      ) : (
        <SceneCreateView
          busy={busy || referenceLoading || submitGuard.submitting}
          draft={createDraft}
          key={referenceImportRevision}
          onAdopt={adoptCreatedImage}
          onBack={returnToLibrary}
          onCancelTask={async sceneId => {
            await cancelSceneTask(sceneId)
          }}
          onChange={changeCreateDraft}
          onCheckStatus={async () => {
            await inspectSceneCreationIssue()
            setQueryDraft('')
            returnToLibrary()
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
