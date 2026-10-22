import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useAssetLibrarySearch } from '@/app/components/asset-library-header'
import { $outfits, hydrateWardrobe } from '@/modules/character'
import {
  $desktopVideoError,
  $desktopVideoLibraryError,
  $desktopVideoLoading,
  $desktopVideoSets,
  $desktopVideoState,
  activateDesktopVideoSet,
  cancelDesktopVideoDraft,
  createDesktopVideoSet,
  deleteDesktopVideoSet,
  designDesktopVideoAction,
  type DesktopVideoAction,
  desktopVideoAspectMismatch,
  type DesktopVideoAspectMismatch,
  type DesktopVideoAspectPolicy,
  type DesktopVideoRefinementSource,
  type DesktopVideoSet,
  generateDesktopVideoAction,
  generateDesktopVideoImage,
  pauseDesktopVideoDraft,
  refreshDesktopVideos,
  renameDesktopVideoSet,
  retryDesktopVideoDraft,
  setDesktopVideoPreferences,
  uploadDesktopVideoAction
} from '@/modules/desktop-videos'
import { $activeScene, $sceneLibrary, loadSceneDetail, loadSceneLibrary, type SceneAsset } from '@/modules/scene'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { RefreshCw } from '@/shared/lib/icons'
import { failureText } from '@/shared/lib/ipc-error'
import {
  confirmAction,
  CreationFeedback,
  CreationWorkspace,
  Disclosure,
  INPUT_CLASS,
  LoadingBlock,
  PanelSelect
} from '@/shared/panel'
import { fileApi, shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { useStrings } from '@/shared/strings'
import type { DesktopLifeUiSnapshot } from '@ipc/contracts'

import { DesktopLifeActionView } from './desktop-life-action-view'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE } from './desktop-life-buttons'
import { DesktopLifeCreateView } from './desktop-life-create-view'
import { DesktopLifeDialog } from './desktop-life-dialog'
import { readyDesktopLifeResolution, reconcileDesktopLifeDisplay } from './desktop-life-display'
import { DesktopLifeHeader } from './desktop-life-header'
import { DesktopLifeLibraryView } from './desktop-life-library-view'
import { VideoRefinementModal } from './desktop-life-refinement'
import { resolutionLabel } from './desktop-life-resolution'
import {
  $desktopLifeUi,
  newDesktopLifeCreation,
  updateDesktopLifeCreate,
  updateDesktopLifeUi
} from './desktop-life-state'
import {
  desktopLifeActionName,
  desktopLifeActionStatus,
  desktopLifeActionsToCreate,
  desktopLifeReadyForActions,
  desktopLifeSelectedAction,
  isActionOutcomeUnknown,
  isActionPaused
} from './desktop-life-status'
import { DesktopLifeWorkbench } from './desktop-life-workbench'
import styles from './desktop-life.module.css'

export function DesktopLifePage({
  onPrepareMaterial
}: {
  onPrepareMaterial?: (kind: 'scene' | 'outfit') => void
}): React.JSX.Element {
  const strings = useStrings()
  const t = strings.desktopLife
  const commonStrings = useStrings().common
  const queueStrings = commonStrings.videoQueue
  const auth = useStore($auth)
  const panelActive = usePanelActivity()
  const presentation = useStore($presentation)
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const state = useStore($desktopVideoState)
  const sets = useStore($desktopVideoSets)
  const loading = useStore($desktopVideoLoading)
  const error = useStore($desktopVideoError)
  const libraryError = useStore($desktopVideoLibraryError)
  const ui = useStore($desktopLifeUi)
  const { selectedSetId, selectedActionId, create, feedback, design, addActionOpen } = ui
  const createOpen = ui.creating
  const navigationRevision = useRef(0)
  const libraryFocus = useRef<number | 'create' | null>(selectedSetId)
  const librarySearch = useAssetLibrarySearch()

  const setDesign = (next: React.SetStateAction<typeof design>): void => {
    updateDesktopLifeUi({ design: typeof next === 'function' ? next($desktopLifeUi.get().design) : next })
  }

  // 提交锁按背景、动作或创建入口归属：同一入口不并发，不同入口互不阻塞。
  const requestRef = useRef<Map<string, { scope: string; title: string }>>(new Map())
  const [requests, setRequests] = useState<Record<string, { scope: string; title: string }>>({})
  const [operationErrors, setOperationErrors] = useState<Record<string, string>>({})
  const [aspectChoice, setAspectChoice] = useState<DesktopVideoAspectMismatch | null>(null)
  const resolveAspectChoice = useRef<((policy: DesktopVideoAspectPolicy | null) => void) | null>(null)
  const aspectGeneration = useRef(0)
  const aspectQueue = useRef<Promise<unknown>>(Promise.resolve())
  const scenes = useStore($sceneLibrary)
  const activeScene = useStore($activeScene)
  const outfits = useStore($outfits)
  const [creationScene, setCreationScene] = useState<{ id: string; scene: SceneAsset | null } | null>(null)
  const [rename, setRename] = useState('')

  const [refinement, setRefinement] = useState<{
    action: DesktopVideoAction
    set: DesktopVideoSet
    source: DesktopVideoRefinementSource
  } | null>(null)

  const [designMessage, setDesignMessage] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [customActionOpen, setCustomActionOpen] = useState(false)
  const customActionRevision = useRef(0)
  const authSession = useRef(authSessionId)
  const beginAsync = useAsyncGuard()
  const selected = sets.find(item => item.id === selectedSetId)
  const selectedTitle = selected?.title.trim() || t.newSceneName
  const sourceError = selected?.source_error
  const readyForActions = desktopLifeReadyForActions(selected)
  const actionsToCreate = readyForActions && !sourceError ? desktopLifeActionsToCreate(selected?.actions ?? []) : []
  const idle = selected?.actions.find(action => action.key === 'idle')
  const action = selected ? desktopLifeSelectedAction(selected, selectedActionId) : undefined
  const setScope = `set:${selectedSetId}`
  const createScope = `create:${selectedSetId}`
  const actionScope = `${setScope}:action:${action?.id}`
  // 库级入口仅偏好保存占用：LibraryView 的 busy/savingPreferences 均由此派生。
  const libraryScope = 'library'

  const operationScope = createOpen ? createScope : selected ? actionScope : libraryScope

  const busy = useCallback((scope: string): boolean => scope in requests, [requests])
  const anyBusy = Object.keys(requests).length > 0

  const navigate = useCallback((change: Partial<DesktopLifeUiSnapshot>): void => {
    navigationRevision.current += 1
    aspectGeneration.current += 1
    resolveAspectChoice.current?.(null)
    resolveAspectChoice.current = null
    setAspectChoice(null)
    setSettingsOpen(false)
    setRename('')
    setCustomActionOpen(false)
    setRefinement(null)
    customActionRevision.current += 1
    updateDesktopLifeUi({ ...change, addActionOpen: false })
  }, [])

  const closeCustomAction = (): void => {
    customActionRevision.current += 1
    setCustomActionOpen(false)
  }

  useEffect(() => {
    if (loading || !state || error || libraryError) {
      return
    }

    if (selectedSetId !== null && !selected) {
      navigate({ selectedSetId: null, selectedActionId: null })

      return
    }

    if (selected && (action?.id ?? null) !== selectedActionId) {
      setRefinement(null)
      updateDesktopLifeUi({ selectedActionId: action?.id ?? null })
    }
  }, [loading, state, error, libraryError, selected, selectedSetId, action?.id, selectedActionId, navigate])

  const working = sets.some(
    item =>
      item.actions.some(
        item =>
          (item.status === 'processing' && !isActionPaused(item)) ||
          item.sync_targets.some(target => ['queued', 'processing'].includes(target.status))
      ) || item.proposals.some(proposal => proposal.status === 'pending')
  )

  useEffect(() => {
    if (addActionOpen && (!selected || actionsToCreate.length === 0)) {
      updateDesktopLifeUi({ addActionOpen: false })
    }
  }, [actionsToCreate.length, addActionOpen, selected])

  useEffect(() => {
    return () => {
      aspectGeneration.current += 1
      resolveAspectChoice.current?.(null)
      resolveAspectChoice.current = null
      setAspectChoice(null)
    }
  }, [authSessionId, panelActive])

  useEffect(() => {
    if (authSessionId) {
      void refreshDesktopVideos()
      void hydrateWardrobe()
      void loadSceneLibrary('', 0)
    }
  }, [authSessionId])

  useEffect(() => {
    if (authSession.current === authSessionId) {
      return
    }

    authSession.current = authSessionId
    setCreationScene(null)
    setRefinement(null)
    requestRef.current.clear()
    setRequests({})
    setOperationErrors({})
    setRename('')
    setDesignMessage(null)
    libraryFocus.current = null
    setSettingsOpen(false)
    setCustomActionOpen(false)
    customActionRevision.current += 1
  }, [authSessionId])

  useEffect(() => {
    if (!working) {
      return
    }

    const timer = window.setInterval(() => void refreshDesktopVideos(), 5000)

    return () => window.clearInterval(timer)
  }, [working])

  useEffect(() => {
    if (!createOpen || create.source !== 'ai') {
      return
    }

    const previous = $desktopLifeUi.get().create
    const scene = activeScene?.status === 'ready' ? activeScene : scenes.find(item => item.status === 'ready')

    const outfit =
      outfits.find(item => item.active && item.status === 'ready') ?? outfits.find(item => item.status === 'ready')

    const replaceScene =
      !previous.sceneId ||
      (creationScene?.id === previous.sceneId && (!creationScene.scene || creationScene.scene.status !== 'ready'))

    const replaceOutfit =
      !previous.outfitId ||
      (outfits.length > 0 && !outfits.some(item => String(item.id) === previous.outfitId && item.status === 'ready'))

    if ((replaceScene && scene) || (replaceOutfit && outfit)) {
      updateDesktopLifeCreate({
        ...(replaceScene && scene ? { sceneId: scene.id, sceneLabel: scene.title } : {}),
        ...(replaceOutfit && outfit ? { outfitId: String(outfit.id) } : {}),
        requestId: crypto.randomUUID()
      })
    }
  }, [createOpen, create.source, activeScene, creationScene, outfits, scenes])

  useEffect(() => {
    if (!createOpen) {
      return
    }

    const previous = $desktopLifeUi.get().create
    const create = reconcileDesktopLifeDisplay(previous, presentation)

    if (create !== previous) {
      updateDesktopLifeCreate(create)
    }
  }, [createOpen, presentation, create.targetDisplayId, create.targetResolution])

  useEffect(() => {
    if (!createOpen || create.source !== 'ai' || !create.sceneId) {
      return
    }

    const scope = captureAuthScope()
    let current = true
    void loadSceneDetail(create.sceneId).then(scene => {
      if (current && scope?.()) {
        setCreationScene({ id: create.sceneId, scene })
      }
    })

    return () => {
      current = false
    }
  }, [createOpen, create.source, create.sceneId, authSessionId])

  /** 执行一个桌景操作：失败由本函数呈现并返回 null，成功返回结果值。 */
  const perform = async <T,>(
    operation: () => Promise<T>,
    title = t.processingRequest,
    scope = operationScope
  ): Promise<null | T> => {
    if (requestRef.current.has(scope)) {
      return null
    }

    const isLive = beginAsync()
    const pending = { scope, title }
    requestRef.current.set(scope, pending)
    setRequests(current => ({ ...current, [scope]: pending }))
    setOperationErrors(current => {
      const next = { ...current }
      delete next[scope]

      return next
    })

    try {
      return await operation()
    } catch (failure) {
      if (isLive()) {
        setOperationErrors(current => ({ ...current, [scope]: failureText(failure, t.operationFailed) }))
        notifyError(failure, t.title)
        void refreshDesktopVideos()
      }

      return null
    } finally {
      if (isLive() && requestRef.current.get(scope) === pending) {
        requestRef.current.delete(scope)
        setRequests(current => {
          if (current[scope] !== pending) {
            return current
          }

          const next = { ...current }
          delete next[scope]

          return next
        })
      }
    }
  }

  const withAspectChoice = async (
    operation: (policy: DesktopVideoAspectPolicy, mismatch?: DesktopVideoAspectMismatch) => Promise<void>,
    isCurrent: () => boolean = () => true
  ): Promise<boolean> => {
    const scope = captureAuthScope()
    const isLive = beginAsync()
    const generation = aspectGeneration.current
    const navigation = navigationRevision.current

    try {
      await operation('ask')

      return true
    } catch (failure) {
      const mismatch = desktopVideoAspectMismatch(failure)

      if (!mismatch) {
        throw failure
      }

      if (
        !scope?.() ||
        !isLive() ||
        !isCurrent() ||
        generation !== aspectGeneration.current ||
        navigation !== navigationRevision.current
      ) {
        return false
      }

      // 画幅确认一次只展示一个：后到的冲突等前一个选择结束后再展示自己的。
      const aspectResult = aspectQueue.current.then(() => {
        if (
          !scope?.() ||
          !isLive() ||
          !isCurrent() ||
          generation !== aspectGeneration.current ||
          navigation !== navigationRevision.current
        ) {
          return null
        }

        return new Promise<DesktopVideoAspectPolicy | null>(resolve => {
          resolveAspectChoice.current = resolve
          setAspectChoice(mismatch)
        })
      })

      aspectQueue.current = aspectResult.catch(() => undefined)

      const policy = await aspectResult

      if (
        policy &&
        scope() &&
        isLive() &&
        isCurrent() &&
        generation === aspectGeneration.current &&
        navigation === navigationRevision.current
      ) {
        await operation(policy, mismatch)

        return true
      }

      return false
    }
  }

  const makeImage = async (
    item: DesktopVideoAction,
    set: DesktopVideoSet,
    note = '',
    regenerate = false
  ): Promise<void> => {
    await withAspectChoice(policy => generateDesktopVideoImage(item, set, note, regenerate, policy))
  }

  const makeVideo = async (
    item: DesktopVideoAction,
    set: DesktopVideoSet,
    note = '',
    videoFeedback?: string | null
  ): Promise<void> => {
    const scope = captureAuthScope()
    const isLive = beginAsync()
    const generation = aspectGeneration.current
    const navigation = navigationRevision.current
    await withAspectChoice(async policy => {
      const generated = await generateDesktopVideoAction(item, set, note, policy, videoFeedback)
      const currentUi = $desktopLifeUi.get()

      if (
        generated.id !== item.id &&
        scope?.() &&
        isLive() &&
        generation === aspectGeneration.current &&
        navigation === navigationRevision.current &&
        !currentUi.creating &&
        currentUi.selectedSetId === set.id &&
        currentUi.selectedActionId === item.id
      ) {
        updateDesktopLifeUi({ selectedActionId: generated.id })
      }
    })
  }

  const chooseAspect = (policy: DesktopVideoAspectPolicy | null): void => {
    const resolve = resolveAspectChoice.current
    resolveAspectChoice.current = null
    setAspectChoice(null)
    resolve?.(policy)
  }

  const uploadSource = async (
    action: DesktopVideoAction,
    set: DesktopVideoSet,
    selectedPath?: string
  ): Promise<void> => {
    const scope = captureAuthScope()
    const isLive = beginAsync()

    if (!authSessionId || !scope || !set.can_upload_source || action.key !== 'idle' || set.source === 'ai') {
      return
    }

    const image = set.source === 'image_upload'

    const path =
      selectedPath ??
      (
        await fileApi().selectPaths({
          filters: [
            image
              ? { name: t.imageFilterName, extensions: ['png', 'jpg', 'jpeg', 'webp'] }
              : { name: t.videoFilterName, extensions: ['mp4', 'mov'] }
          ],
          multiple: false,
          title: image ? t.uploadImage : t.selectVideo
        })
      )[0]

    const currentSet = $desktopVideoSets.get().find(item => item.id === set.id)
    const currentAction = currentSet?.actions.find(item => item.id === action.id)

    if (path && scope() && isLive() && currentSet?.can_upload_source && currentAction) {
      await withAspectChoice(
        policy =>
          uploadDesktopVideoAction(currentAction, currentSet, path, authSessionId, image ? 'image' : 'video', policy),
        () => scope() && isLive() && !$desktopLifeUi.get().creating && $desktopLifeUi.get().selectedSetId === set.id
      )
    }
  }

  const activate = async (target: DesktopVideoSet | undefined = selected): Promise<void> => {
    if (!target) {
      return
    }

    const scope = captureAuthScope()
    const activated = await activateDesktopVideoSet(target)

    if (activated && scope?.() && $presentation.get().effectiveMode !== 'desktop') {
      await shellApi().presentation.setMode('desktop')
    }
  }

  const openCreate = (): void => {
    if (selectedSetId === null) {
      libraryFocus.current = 'create'
    }

    navigate({ creating: true })
    void loadSceneLibrary('', 0)
    void hydrateWardrobe()
  }

  const openSet = (set: DesktopVideoSet): void => {
    libraryFocus.current = set.id
    navigate({
      selectedSetId: set.id,
      selectedActionId: desktopLifeSelectedAction(set, null)?.id ?? null,
      creating: false
    })
  }

  const openAction = (item: DesktopVideoAction): void => navigate({ selectedActionId: item.id })

  const actionStatus = (item: DesktopVideoAction): string =>
    desktopLifeActionStatus(item, t, queueStrings, commonStrings.actionVariants)

  const creationStatus = (item: DesktopVideoAction): React.ComponentProps<typeof CreationFeedback> | null => {
    if (isActionOutcomeUnknown(item)) {
      return {
        state: 'unknown',
        title: t.resultPending,
        description: t.resultPendingHint,
        actions: (
          <button
            className={BTN_SUBTLE}
            disabled={loading || anyBusy}
            onClick={() => void refreshDesktopVideos()}
            type="button"
          >
            <RefreshCw size={16} />
            {strings.common.refresh}
          </button>
        )
      }
    }

    if (isActionPaused(item)) {
      return { state: 'paused', title: t.paused, description: t.resumeHint }
    }

    if (item.status === 'processing') {
      const queue = item.draft?.video_queue ?? item.video_queue

      return {
        state: queue?.state === 'blocked' ? 'unknown' : queue ? 'waiting' : 'working',
        title: actionStatus(item),
        description: queue?.state === 'blocked' ? t.resultPendingHint : t.leaveHint
      }
    }

    if (item.status === 'failed') {
      return {
        state: 'error',
        title: strings.common.actionVariants.statuses.failed
      }
    }

    return null
  }

  const selectedScene =
    creationScene?.id === create.sceneId
      ? creationScene.scene
      : (scenes.find(scene => scene.id === create.sceneId) ?? (activeScene?.id === create.sceneId ? activeScene : null))

  const selectedOutfit = outfits.find(outfit => String(outfit.id) === create.outfitId)

  const materialReady =
    create.source !== 'ai' || (selectedScene?.status === 'ready' && selectedOutfit?.status === 'ready')

  const resolutionReady = readyDesktopLifeResolution(create, presentation) !== null

  const taskStatus = !createOpen && action ? creationStatus(action) : null
  const visibleError = operationErrors[operationScope] ?? (!createOpen ? operationErrors[setScope] : undefined)

  const visibleRequest = requests[operationScope] ?? (!createOpen ? requests[setScope] : undefined) ?? null

  const workspaceStatus =
    visibleError || visibleRequest ? (
      <CreationFeedback
        description={visibleError}
        state={visibleRequest ? 'working' : 'error'}
        title={visibleRequest ? (createOpen ? undefined : visibleRequest.title) : t.operationFailed}
      />
    ) : taskStatus ? (
      <CreationFeedback {...taskStatus} key={action?.draft?.id ?? action?.id} />
    ) : null

  const loadError = error || libraryError

  const loadIssue =
    loadError || state?.preparation_error ? (
      <CreationFeedback
        actions={
          <button className={BTN_SUBTLE} disabled={loading} onClick={() => void refreshDesktopVideos()} type="button">
            <RefreshCw className="size-4" />
            {strings.common.refresh}
          </button>
        }
        description={
          <Disclosure bodyClassName="break-words" title={t.details} variant="inline">
            <p className="mt-2 break-words">{loadError || state?.preparation_error}</p>
          </Disclosure>
        }
        state="error"
        title={t.unavailable}
      />
    ) : null

  const submitCreation = async (): Promise<void> => {
    const currentCreate = $desktopLifeUi.get().create

    if (
      requestRef.current.has(createScope) ||
      !readyDesktopLifeResolution(currentCreate, $presentation.get()) ||
      !materialReady
    ) {
      return
    }

    const scope = captureAuthScope()
    const isLive = beginAsync()
    const navigation = navigationRevision.current

    const created = await perform(async () => {
      let mediaPath: string | undefined

      if (currentCreate.source !== 'ai') {
        const image = currentCreate.source === 'image_upload'

        const paths = await fileApi().selectPaths({
          title: image ? t.uploadImage : t.selectVideo,
          multiple: false,
          filters: [
            image
              ? { name: t.imageFilterName, extensions: ['png', 'jpg', 'jpeg', 'webp'] }
              : { name: t.videoFilterName, extensions: ['mp4', 'mov'] }
          ]
        })

        mediaPath = paths[0]

        if (!mediaPath) {
          return null
        }
      }

      const liveUi = $desktopLifeUi.get()
      const target = readyDesktopLifeResolution(liveUi.create, $presentation.get())

      if (
        !scope?.() ||
        !isLive() ||
        !authSessionId ||
        !target ||
        !liveUi.creating ||
        liveUi.create.requestId !== currentCreate.requestId ||
        navigation !== navigationRevision.current
      ) {
        return null
      }

      const set = await createDesktopVideoSet({
        request_id: currentCreate.requestId,
        name: currentCreate.name.trim() || t.newSceneName,
        ...(currentCreate.source === 'ai'
          ? {
              scene_id: Number(currentCreate.sceneId),
              outfit_id: Number(currentCreate.outfitId),
              feedback: currentCreate.feedback.trim()
            }
          : {}),
        source: currentCreate.source,
        target_width: target.width,
        target_height: target.height
      })

      return { set, mediaPath }
    }, t.preparing)

    if (!created || !scope?.() || !isLive()) {
      return
    }

    const first = created.set.actions.find(item => item.key === 'idle')
    const liveUi = $desktopLifeUi.get()

    if (liveUi.create.requestId === currentCreate.requestId) {
      if (navigation === navigationRevision.current && liveUi.creating) {
        navigate({
          selectedSetId: created.set.id,
          selectedActionId: first?.id ?? null,
          creating: false,
          create: newDesktopLifeCreation()
        })
      } else {
        updateDesktopLifeUi({ create: newDesktopLifeCreation() })
      }
    }

    if (first && created.mediaPath) {
      await perform(
        () => uploadSource(first, created.set, created.mediaPath),
        currentCreate.source === 'image_upload' ? t.uploadingImage : t.uploadProcessing,
        `set:${created.set.id}:action:${first.id}`
      )
    }
  }

  const customActionForm =
    selected && readyForActions && !sourceError ? (
      <>
        <h3 className="mt-3 text-sm font-medium text-strong">{t.addCustom}</h3>
        <form
          className="mt-4 flex flex-col gap-3"
          onSubmit={event => {
            event.preventDefault()
            const isLive = beginAsync()
            const navigation = navigationRevision.current
            const dialogRevision = customActionRevision.current
            const submittedDesign = $desktopLifeUi.get().design
            void perform(
              async () => {
                const proposal = await designDesktopVideoAction({
                  name: design.name.trim() || design.description.trim().slice(0, 24),
                  motion_description: design.description.trim(),
                  kind: design.kind,
                  duration_seconds: design.duration,
                  expected_set_id: selected.id
                })

                if (!isLive()) {
                  return
                }

                if ($desktopLifeUi.get().design === submittedDesign) {
                  setDesign(current => ({ ...current, name: '', description: '' }))
                }

                const currentUi = $desktopLifeUi.get()

                if (
                  navigation !== navigationRevision.current ||
                  dialogRevision !== customActionRevision.current ||
                  currentUi.selectedSetId !== selected.id ||
                  currentUi.creating
                ) {
                  return
                }

                setDesignMessage(proposal.review_reason || t.designSubmitted)

                if (proposal.action_id) {
                  navigate({ selectedActionId: proposal.action_id })
                }
              },
              t.processingRequest,
              setScope
            )
          }}
        >
          {designMessage && (
            <p className="text-sm text-muted" role="status">
              {designMessage}
            </p>
          )}
          <label className="flex flex-col gap-2 text-sm text-body">
            {t.actionDescription}
            <textarea
              className={`${INPUT_CLASS} min-h-28 resize-y`}
              disabled={busy(setScope)}
              maxLength={600}
              minLength={10}
              onChange={event => setDesign(current => ({ ...current, description: event.target.value }))}
              required
              rows={4}
              value={design.description}
            />
          </label>
          <Disclosure
            bodyClassName="flex flex-col gap-3"
            summaryClassName="text-sm text-muted"
            title={t.actionSettings}
            variant="inline"
          >
            <div className="mt-3 flex flex-col gap-3">
              <input
                aria-label={t.actionName}
                className={INPUT_CLASS}
                disabled={busy(setScope)}
                maxLength={64}
                onChange={event => setDesign(current => ({ ...current, name: event.target.value }))}
                placeholder={t.nameOptional}
                value={design.name}
              />
              <PanelSelect
                ariaLabel={t.actionSettings}
                disabled={busy(setScope)}
                onChange={value =>
                  setDesign(current => ({
                    ...current,
                    kind: value === 'once' ? 'once' : 'loop',
                    duration: value === 'once' ? 6 : 10
                  }))
                }
                options={[
                  { value: 'loop', label: t.loop },
                  { value: 'once', label: t.once }
                ]}
                value={design.kind}
              />
              <label className="flex items-center gap-2 text-sm text-body">
                {t.duration}
                <input
                  className={`${INPUT_CLASS} w-20`}
                  disabled={busy(setScope)}
                  max={15}
                  min={1}
                  onChange={event => setDesign(current => ({ ...current, duration: Number(event.target.value) || 0 }))}
                  required
                  step={1}
                  type="number"
                  value={design.duration}
                />
              </label>
            </div>
          </Disclosure>
          <button
            className={`${BTN_PRIMARY} self-start`}
            disabled={busy(setScope) || design.description.trim().length < 10}
            type="submit"
          >
            {t.startAction}
          </button>
          {(design.name.trim() || design.description.trim()) && (
            <button
              className={`${BTN_GHOST} self-start`}
              disabled={busy(setScope)}
              onClick={() => setDesign({ name: '', description: '', kind: 'loop', duration: 10 })}
              type="button"
            >
              {t.clearCustomization}
            </button>
          )}
        </form>
      </>
    ) : null

  return (
    <div className={styles.page}>
      {createOpen ? (
        <DesktopLifeCreateView
          busy={busy(createScope)}
          create={create}
          materialReady={materialReady}
          onBack={() => navigate({ creating: false })}
          // 草稿一改就换请求标识：创建按 request_id 去重，沿用旧标识会把新参数当成同一次提交。
          onChange={change => updateDesktopLifeCreate({ ...change, requestId: crypto.randomUUID() })}
          onChangeDisplay={displayId =>
            updateDesktopLifeCreate(current => reconcileDesktopLifeDisplay(current, $presentation.get(), displayId))
          }
          onPickScene={scene => {
            setCreationScene({ id: scene.id, scene })
            updateDesktopLifeCreate({
              sceneId: scene.id,
              sceneLabel: scene.title,
              requestId: crypto.randomUUID()
            })
          }}
          onPrepareMaterial={onPrepareMaterial}
          onSubmit={() => void submitCreation().catch(() => undefined)}
          presentation={presentation}
          resolutionReady={resolutionReady}
          selectedOutfit={selectedOutfit}
          selectedScene={selectedScene}
          status={workspaceStatus ?? loadIssue}
        />
      ) : selected ? (
        <DesktopLifeWorkbench
          action={action}
          busy={busy(setScope)}
          canAdd={actionsToCreate.length > 0}
          canCustomize={readyForActions && !sourceError}
          key={selected.id}
          onActivate={() => void perform(() => activate(selected), t.processingRequest, setScope)}
          onAdd={() => updateDesktopLifeUi({ addActionOpen: true })}
          onBack={() => navigate({ selectedSetId: null, selectedActionId: null })}
          onCustomize={() => {
            customActionRevision.current += 1
            setCustomActionOpen(true)
          }}
          onMore={() => setSettingsOpen(true)}
          onSelect={openAction}
          set={selected}
        >
          {action ? (
            <DesktopLifeActionView
              action={action}
              busy={busy(actionScope)}
              feedbackStatus={workspaceStatus}
              issue={loadIssue}
              key={action.id}
              noteDraft={feedback[action.id]}
              onAddAction={() => updateDesktopLifeUi({ addActionOpen: true })}
              onChangeNote={note =>
                updateDesktopLifeUi({ feedback: { ...$desktopLifeUi.get().feedback, [action.id]: note } })
              }
              onContinueBase={() => {
                if (idle) {
                  openAction(idle)
                }
              }}
              onCreateSet={openCreate}
              onDiscard={() => {
                void confirmAction({
                  confirmLabel: t.discardConfirm,
                  description: t.discardHint,
                  title: t.discardDraft
                }).then(confirmed => {
                  if (confirmed) {
                    void perform(
                      () => cancelDesktopVideoDraft(action),
                      t.processingRequest,
                      `set:${action.set_id}:action:${action.id}`
                    )
                  }
                })
              }}
              onMakeImage={(note, regenerate) => void perform(() => makeImage(action, selected, note, regenerate))}
              onMakeVideo={(note, videoFeedback) =>
                void perform(() => makeVideo(action, selected, note, videoFeedback))
              }
              onPause={() => void perform(() => pauseDesktopVideoDraft(action), t.pausingCreation)}
              onRefine={source => setRefinement({ action, set: selected, source })}
              onRetry={() => void perform(() => retryDesktopVideoDraft(action, selected))}
              onUpload={() =>
                void perform(
                  () => uploadSource(action, selected),
                  selected.source === 'image_upload' ? t.uploadingImage : t.uploadProcessing
                )
              }
              set={selected}
            />
          ) : (
            <div className={styles.content}>
              {loadIssue ?? (
                <CreationFeedback
                  actions={
                    <button
                      className={BTN_SUBTLE}
                      disabled={loading}
                      onClick={() => void refreshDesktopVideos()}
                      type="button"
                    >
                      {strings.common.refresh}
                    </button>
                  }
                  state={loading ? 'working' : 'error'}
                  title={loading ? strings.common.loading : t.noActions}
                />
              )}
            </div>
          )}
        </DesktopLifeWorkbench>
      ) : selectedSetId !== null ? (
        <CreationWorkspace
          header={
            <DesktopLifeHeader
              onBack={() => navigate({ selectedSetId: null, selectedActionId: null })}
              title={t.title}
            />
          }
        >
          <div className={styles.content}>{loadIssue ?? <LoadingBlock label={strings.common.loading} />}</div>
        </CreationWorkspace>
      ) : (
        <DesktopLifeLibraryView
          busy={busy(libraryScope)}
          feedback={workspaceStatus}
          focusTarget={libraryFocus.current}
          issue={loadIssue}
          loading={loading}
          onCreate={openCreate}
          onOpenSet={openSet}
          onSavePreferences={change =>
            void perform(() => setDesktopVideoPreferences(change), t.preferencesSaving, libraryScope)
          }
          savingPreferences={busy(libraryScope)}
          search={librarySearch}
          sets={sets}
          state={state}
        />
      )}
      {settingsOpen && selected && (
        <DesktopLifeDialog
          onClose={() => setSettingsOpen(false)}
          regionId="desktop-life-settings"
          title={t.backgroundSettings}
        >
          <form
            className="flex flex-col gap-3"
            onSubmit={event => {
              event.preventDefault()
              const name = rename.trim()
              const navigation = navigationRevision.current

              if (!name) {
                return
              }

              void perform(() => renameDesktopVideoSet(selected.id, name), t.processingRequest, setScope).then(
                renamed => {
                  if (renamed !== null && navigation === navigationRevision.current) {
                    setRename('')
                  }
                }
              )
            }}
          >
            <label className={styles.field}>
              {t.setName}
              <input
                className={INPUT_CLASS}
                disabled={busy(setScope)}
                maxLength={160}
                onChange={event => setRename(event.target.value)}
                placeholder={selectedTitle}
                value={rename}
              />
            </label>
            <button className={`${BTN_SUBTLE} self-start`} disabled={busy(setScope) || !rename.trim()} type="submit">
              {t.renameSet}
            </button>
          </form>
          {operationErrors[setScope] && (
            <p className="mt-3 text-xs text-danger-fg" role="alert">
              {operationErrors[setScope]}
            </p>
          )}
          <button
            className={`${BTN_GHOST} mt-5 text-danger-fg`}
            disabled={busy(setScope)}
            onClick={() => {
              setSettingsOpen(false)
              void confirmAction({
                confirmLabel: t.deleteSet,
                description: t.deleteHint,
                title: t.deleteSet,
                variant: 'destructive'
              }).then(async confirmed => {
                if (!confirmed) {
                  return
                }

                const isLive = beginAsync()

                const deleted = await perform(() => deleteDesktopVideoSet(selected.id), t.processingRequest, setScope)

                if (deleted === null) {
                  // 失败已由 perform 呈现，删除没完成就不再导航。
                  return
                }

                if (isLive() && $desktopLifeUi.get().selectedSetId === selected.id) {
                  navigate({ selectedSetId: null, selectedActionId: null })
                }
              })
            }}
            type="button"
          >
            {t.deleteSet}
          </button>
        </DesktopLifeDialog>
      )}
      {addActionOpen && selected && actionsToCreate.length > 0 && !createOpen && (
        <DesktopLifeDialog
          onClose={() => updateDesktopLifeUi({ addActionOpen: false })}
          regionId="desktop-life-add-action"
          title={t.design}
          widthClass="max-w-2xl"
        >
          <p className="mb-4 text-sm leading-relaxed text-muted">{t.addHint}</p>
          <div className={styles.actionGrid}>
            {actionsToCreate.map(item => (
              <button
                className="flex flex-col gap-2 rounded-xl border border-line-hairline p-4 text-left hover:bg-fill-hover"
                key={item.id}
                onClick={() => openAction(item)}
                type="button"
              >
                <strong className="text-sm text-strong">{desktopLifeActionName(item, t)}</strong>
                <span className="text-xs text-muted">{actionStatus(item)}</span>
              </button>
            ))}
          </div>
        </DesktopLifeDialog>
      )}
      {customActionOpen && selected && !createOpen && (
        <DesktopLifeDialog
          onClose={closeCustomAction}
          regionId="desktop-life-custom-action"
          title={t.customAction}
          widthClass="max-w-2xl"
        >
          {customActionForm}
          {requests[setScope] && <CreationFeedback state="working" title={requests[setScope].title} />}
          {operationErrors[setScope] && (
            <CreationFeedback description={operationErrors[setScope]} state="error" title={t.operationFailed} />
          )}
        </DesktopLifeDialog>
      )}
      {refinement && (
        <VideoRefinementModal
          action={refinement.action}
          key={`${authSessionId}:${refinement.action.id}:${refinement.source}:${refinement.action.version}:${refinement.action.draft?.id ?? ''}:${refinement.set.base_version}:${refinement.set.resolution_revision}`}
          onClose={() => setRefinement(null)}
          set={refinement.set}
          source={refinement.source}
        />
      )}
      {aspectChoice && (
        <DesktopLifeDialog
          onClose={() => chooseAspect(null)}
          regionId="desktop-life-aspect-choice"
          title={t.aspectMismatch}
        >
          <div className="flex flex-col gap-4 text-sm text-body">
            <p>
              {t.actualResolution}: {resolutionLabel(aspectChoice.source_width, aspectChoice.source_height)}
            </p>
            <p>
              {t.targetResolution}: {resolutionLabel(aspectChoice.target_width, aspectChoice.target_height)}
            </p>
            <p>{t.aspectMismatchHint}</p>
            {aspectChoice.choices.includes('expand') && (
              <p>{aspectChoice.media_kind === 'video' ? t.videoExpandHint : t.expandHint}</p>
            )}
            {aspectChoice.duration_seconds !== undefined && (
              <p>{t.refinementDuration(aspectChoice.duration_seconds)}</p>
            )}
            {aspectChoice.adjustment_error && (
              <p className="text-warning" role="alert">
                {aspectChoice.adjustment_error}
              </p>
            )}
            {aspectChoice.media_kind === 'image' && aspectChoice.adjust_action_name && (
              <p>{t.sharedImageExpandHint(aspectChoice.adjust_action_name)}</p>
            )}
            <div className="flex flex-wrap gap-2">
              {aspectChoice.choices.includes('expand') && (
                <button
                  className={BTN_PRIMARY}
                  disabled={Boolean(aspectChoice.adjustment_error)}
                  onClick={() => chooseAspect('expand')}
                  type="button"
                >
                  {t.expandAspect}
                </button>
              )}
              <button className={BTN_SUBTLE} onClick={() => chooseAspect('keep')} type="button">
                {t.keepAspect}
              </button>
              <button className={BTN_GHOST} onClick={() => chooseAspect(null)} type="button">
                {strings.common.cancel}
              </button>
            </div>
          </div>
        </DesktopLifeDialog>
      )}
    </div>
  )
}
