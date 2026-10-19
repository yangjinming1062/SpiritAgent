import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { ActionSyncStatus } from '@/app/components/action-sync-status'
import { $outfits, hydrateWardrobe } from '@/modules/character'
import {
  $desktopVideoError,
  $desktopVideoLoading,
  $desktopVideoSets,
  $desktopVideoState,
  activateDesktopVideoSet,
  cancelDesktopVideoDraft,
  confirmDesktopVideoImage,
  createDesktopVideoSet,
  deleteDesktopVideoSet,
  designDesktopVideoAction,
  type DesktopVideoAction,
  desktopVideoAspectMismatch,
  type DesktopVideoAspectMismatch,
  type DesktopVideoAspectPolicy,
  type DesktopVideoRefinementSource,
  type DesktopVideoSet,
  type DesktopVideoSource,
  generateDesktopVideoAction,
  generateDesktopVideoImage,
  getDesktopVideoPrompt,
  pauseDesktopVideoDraft,
  refreshDesktopVideos,
  renameDesktopVideoSet,
  retryDesktopVideoDraft,
  reviewDesktopVideoAction,
  setDesktopVideoPreferences,
  uploadDesktopVideoAction
} from '@/modules/desktop-videos'
import { $activeScene, $sceneLibrary, loadSceneDetail, loadSceneLibrary, type SceneAsset } from '@/modules/scene'
import { PortraitLightbox } from '@/shared'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { ArrowLeft, Check, ChevronDown, Plus, RefreshCw, Settings } from '@/shared/lib/icons'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import {
  ConfirmDialog,
  CreationActionBar,
  CreationFeedback,
  CreationWorkspace,
  INPUT_CLASS,
  PanelSelect,
  Toggle,
  WizardModal
} from '@/shared/panel'
import { fileApi, shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { useStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE } from './desktop-life-buttons'
import {
  readyDesktopLifeResolution,
  reconcileDesktopLifeDisplay,
  resolveDesktopLifeRecommendationDisplay
} from './desktop-life-display'
import { DesktopLifeReferences, ExternalReference } from './desktop-life-materials'
import { DesktopLifeCover, DesktopLifePreview } from './desktop-life-preview'
import { VideoRefinementModal } from './desktop-life-refinement'
import { DesktopLifeDisplayPicker, DesktopLifeRecommendation, resolutionLabel } from './desktop-life-resolution'
import { $desktopLifeUi, newDesktopLifeCreation, updateDesktopLifeUi } from './desktop-life-state'
import {
  desktopLifeActionStatus,
  desktopLifeActionsToCreate,
  desktopLifeCreationStage,
  isActionPaused
} from './desktop-life-status'
import { useDesktopLifeStrings } from './desktop-life-strings'
import styles from './desktop-life.module.css'

function previewUsesDraft(action: DesktopVideoAction): boolean {
  const draft = action.draft

  return Boolean(
    draft &&
    (draft.candidate_video_url ||
      (draft.image_url &&
        (draft.image_confirmed || (action.status === 'review_pending' && action.stage === 'image_review')) &&
        (!draft.video_refinement || !action.current)))
  )
}

function hasPreviewMedia(action: DesktopVideoAction, draft: boolean): boolean {
  return Boolean(
    draft
      ? action.draft?.candidate_video_url || action.draft?.candidate_poster_url || action.draft?.image_url
      : action.current?.video_url || action.current?.poster_url
  )
}

function localFileName(filePath: string): string {
  return filePath.split(/[\\/]/).pop() || filePath
}

export function DesktopLifePage({
  onPrepareMaterial
}: {
  onPrepareMaterial?: (kind: 'scene' | 'outfit') => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
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
  const ui = useStore($desktopLifeUi)
  const { selectedSetId, selectedActionId, create, feedback, design, addActionOpen } = ui
  const createOpen = ui.creating
  const scrollRef = useRef<HTMLDivElement>(null)
  const libraryScrollTop = useRef(ui.libraryScrollTop ?? 0)
  libraryScrollTop.current = ui.libraryScrollTop ?? 0

  useLayoutEffect(() => {
    const element = scrollRef.current

    if (element) {
      element.scrollTop = selectedSetId === null && !createOpen ? libraryScrollTop.current : 0
    }

    const savePosition = (): void => {
      if (element && selectedSetId === null && !createOpen) {
        updateDesktopLifeUi({ libraryScrollTop: element.scrollTop })
      }
    }

    element?.addEventListener('scroll', savePosition)

    return () => element?.removeEventListener('scroll', savePosition)
  }, [selectedSetId, selectedActionId, createOpen])

  const setCreate = (next: React.SetStateAction<typeof create>): void => {
    updateDesktopLifeUi({ create: typeof next === 'function' ? next($desktopLifeUi.get().create) : next })
  }

  const setFeedback = (next: React.SetStateAction<typeof feedback>): void => {
    updateDesktopLifeUi({ feedback: typeof next === 'function' ? next($desktopLifeUi.get().feedback) : next })
  }

  const setDesign = (next: React.SetStateAction<typeof design>): void => {
    updateDesktopLifeUi({ design: typeof next === 'function' ? next($desktopLifeUi.get().design) : next })
  }

  const setSelectedSetId = (id: number | null): void =>
    updateDesktopLifeUi({ selectedSetId: id, selectedActionId: null })

  const setCreateOpen = (creating: boolean): void => updateDesktopLifeUi({ creating })
  const [busy, setBusy] = useState(false)
  const [requestTitle, setRequestTitle] = useState(t.processingRequest)
  const [operationError, setOperationError] = useState<{ scope: string; message: string } | null>(null)
  const operationScope = `${selectedSetId}:${selectedActionId}:${createOpen}`
  const visibleOperationError = operationError?.scope === operationScope ? operationError.message : null
  const [preferencesSaving, setPreferencesSaving] = useState(false)
  const [aspectChoice, setAspectChoice] = useState<DesktopVideoAspectMismatch | null>(null)
  const resolveAspectChoice = useRef<((policy: DesktopVideoAspectPolicy | null) => void) | null>(null)
  const aspectGeneration = useRef(0)
  const [imagePreviews, setImagePreviews] = useState<Record<string, boolean>>({})
  const scenes = useStore($sceneLibrary)
  const activeScene = useStore($activeScene)
  const outfits = useStore($outfits)
  const [creationScene, setCreationScene] = useState<{ id: string; scene: SceneAsset | null } | null>(null)
  const [rename, setRename] = useState('')
  const [deleteSetId, setDeleteSetId] = useState<number | null>(null)
  const [discardAction, setDiscardAction] = useState<DesktopVideoAction | null>(null)

  const [refinement, setRefinement] = useState<{
    action: DesktopVideoAction
    set: DesktopVideoSet
    source: DesktopVideoRefinementSource
  } | null>(null)

  const [designMessage, setDesignMessage] = useState<string | null>(null)
  const [externalAction, setExternalActionLocal] = useState<DesktopVideoAction | null>(null)

  const setExternalAction = (action: DesktopVideoAction | null): void => {
    setExternalActionLocal(action)
    updateDesktopLifeUi({ externalActionId: action?.id ?? null })
  }

  const [externalBaseVersion, setExternalBaseVersion] = useState(0)
  const [externalResolutionRevision, setExternalResolutionRevision] = useState(0)
  const [externalPrompt, setExternalPrompt] = useState<Awaited<ReturnType<typeof getDesktopVideoPrompt>> | null>(null)
  const externalRequirements = ui.externalRequirements
  const setExternalRequirements = (value: string): void => updateDesktopLifeUi({ externalRequirements: value })
  const [externalPromptRequirements, setExternalPromptRequirements] = useState('')
  const externalFilePath = ui.externalFilePath
  const setExternalFilePath = (value: string | null): void => updateDesktopLifeUi({ externalFilePath: value })
  const [externalPromptLoading, setExternalPromptLoading] = useState(false)
  const [externalUploading, setExternalUploading] = useState(false)
  const [externalError, setExternalError] = useState<string | null>(null)
  const externalUploadRequest = useRef(false)
  const [externalCopied, setExternalCopied] = useState(false)
  const [externalReferenceZoom, setExternalReferenceZoom] = useState<{ label: string; url: string } | null>(null)
  const externalRequest = useRef(0)
  const externalAuthSession = useRef(authSessionId)
  const beginAsync = useAsyncGuard()
  const selected = sets.find(item => item.id === selectedSetId)
  const actionsToCreate = desktopLifeActionsToCreate(selected?.actions ?? [])
  const idle = selected?.actions.find(action => action.key === 'idle')
  const action = selected?.actions.find(item => item.id === selectedActionId)
  const currentMatches = selected?.is_current === true
  const externalSet = sets.find(set => set.id === externalAction?.set_id)
  const liveExternalAction = externalSet?.actions.find(item => item.id === externalAction?.id)

  const externalStale = Boolean(
    externalAction &&
    (!externalSet ||
      externalSet.base_version !== externalBaseVersion ||
      externalSet.resolution_revision !== externalResolutionRevision ||
      liveExternalAction?.version !== externalAction.version ||
      liveExternalAction?.draft?.id !== externalAction.draft?.id ||
      liveExternalAction?.draft?.image_url?.split(/[?#]/)[0] !== externalAction.draft?.image_url?.split(/[?#]/)[0] ||
      liveExternalAction?.draft?.image_confirmed !== externalAction.draft?.image_confirmed ||
      liveExternalAction?.current?.image_url?.split(/[?#]/)[0] !== externalAction.current?.image_url?.split(/[?#]/)[0])
  )

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
    if (externalAuthSession.current === authSessionId) {
      return
    }

    externalAuthSession.current = authSessionId
    externalRequest.current += 1
    setExternalActionLocal(null)
    setExternalPrompt(null)
    setExternalPromptRequirements('')
    setExternalCopied(false)
    setExternalReferenceZoom(null)
    setExternalPromptLoading(false)
    setExternalUploading(false)
    externalUploadRequest.current = false
    setImagePreviews({})
    setCreationScene(null)
    setDeleteSetId(null)
    setDiscardAction(null)
    setRefinement(null)
    setBusy(false)
    setOperationError(null)
    setExternalError(null)
    setPreferencesSaving(false)
    setRename('')
    setDesignMessage(null)
  }, [authSessionId])

  useEffect(() => {
    if (!working) {
      return
    }

    const timer = window.setInterval(() => void refreshDesktopVideos(), 5000)

    return () => window.clearInterval(timer)
  }, [working])

  useEffect(() => {
    if (!externalAction && ui.externalActionId) {
      const set = sets.find(item => item.actions.some(item => item.id === ui.externalActionId))
      const savedAction = set?.actions.find(item => item.id === ui.externalActionId)

      if (set && savedAction) {
        setExternalActionLocal(savedAction)
        setExternalBaseVersion(set.base_version)
        setExternalResolutionRevision(set.resolution_revision)
      }
    }
  }, [externalAction, sets, ui.externalActionId])

  useEffect(() => {
    if (!createOpen) {
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
      updateDesktopLifeUi({
        create: {
          ...previous,
          sceneId: replaceScene && scene ? scene.id : previous.sceneId,
          sceneLabel: replaceScene && scene ? scene.title : previous.sceneLabel,
          outfitId: replaceOutfit && outfit ? String(outfit.id) : previous.outfitId,
          requestId: crypto.randomUUID()
        }
      })
    }
  }, [createOpen, activeScene, creationScene, outfits, scenes])

  useEffect(() => {
    if (!createOpen) {
      return
    }

    const previous = $desktopLifeUi.get().create
    const create = reconcileDesktopLifeDisplay(previous, presentation)

    if (create !== previous) {
      updateDesktopLifeUi({ create })
    }
  }, [createOpen, presentation, create.targetDisplayId, create.targetResolution])

  useEffect(() => {
    if (!createOpen || !create.sceneId) {
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
  }, [createOpen, create.sceneId, authSessionId])

  const perform = async (operation: () => Promise<unknown>, title = t.processingRequest): Promise<void> => {
    if (busy) {
      throw new Error(t.processingRequest)
    }

    const isLive = beginAsync()
    setBusy(true)
    setRequestTitle(title)
    setOperationError(null)

    try {
      await operation()
    } catch (failure) {
      if (isLive()) {
        setOperationError({ scope: operationScope, message: backendDetailMessage(failure, errorMessage(failure)) })
        notifyError(failure, t.title)
        void refreshDesktopVideos()
      }

      throw failure
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  // 普通操作在页面呈现错误；确认弹窗直接等待 perform 的成功或失败。
  const run = (operation: () => Promise<unknown>, title?: string): Promise<void> =>
    perform(operation, title).catch(() => undefined)

  const savePreferences = async (change: Parameters<typeof setDesktopVideoPreferences>[0]): Promise<void> => {
    if (busy || preferencesSaving) {
      return
    }

    const isLive = beginAsync()
    setPreferencesSaving(true)

    try {
      await run(() => setDesktopVideoPreferences(change))
    } finally {
      if (isLive()) {
        setPreferencesSaving(false)
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

    try {
      await operation('ask')

      return true
    } catch (failure) {
      const mismatch = desktopVideoAspectMismatch(failure)

      if (!mismatch) {
        throw failure
      }

      if (!scope?.() || !isLive() || !isCurrent() || generation !== aspectGeneration.current) {
        return false
      }

      const policy = await new Promise<DesktopVideoAspectPolicy | null>(resolve => {
        resolveAspectChoice.current?.(null)
        resolveAspectChoice.current = resolve
        setAspectChoice(mismatch)
      })

      if (policy && scope() && isLive() && isCurrent() && generation === aspectGeneration.current) {
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
    await withAspectChoice(async policy => {
      const generated = await generateDesktopVideoAction(item, set, note, policy, videoFeedback)
      const currentUi = $desktopLifeUi.get()

      if (
        generated.id !== item.id &&
        scope?.() &&
        isLive() &&
        generation === aspectGeneration.current &&
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

  const loadExternalPrompt = async (action: DesktopVideoAction, requirements = ''): Promise<void> => {
    if (externalUploadRequest.current) {
      return
    }

    const requestId = ++externalRequest.current
    const authScope = captureAuthScope()
    const isLive = beginAsync()
    const generation = aspectGeneration.current

    const ownsRequest = (): boolean => Boolean(authScope?.() && isLive() && requestId === externalRequest.current)

    const isCurrent = (): boolean =>
      Boolean(
        ownsRequest() && generation === aspectGeneration.current && $desktopLifeUi.get().externalActionId === action.id
      )

    const submittedRequirements = requirements.trim()
    setExternalPromptLoading(true)
    setExternalError(null)

    try {
      const set = $desktopVideoSets.get().find(item => item.id === action.set_id)
      const latest = set?.actions.find(item => item.id === action.id)

      if (!set || !latest) {
        throw new Error(t.stalePrompt)
      }

      await withAspectChoice(async (policy, mismatch) => {
        if (policy === 'expand') {
          const liveSet = $desktopVideoSets.get().find(item => item.id === set.id)
          const targetAction = liveSet?.actions.find(item => item.id === (mismatch?.adjust_action_id ?? latest.id))

          if (
            !isCurrent() ||
            !liveSet ||
            !targetAction ||
            liveSet.base_version !== set.base_version ||
            liveSet.resolution_revision !== set.resolution_revision
          ) {
            throw new Error(t.stalePrompt)
          }

          if (targetAction.id !== latest.id && targetAction.draft) {
            throw new Error(t.sharedImageDraftHint)
          }

          if (
            targetAction.id === latest.id &&
            (targetAction.draft?.id !== latest.draft?.id ||
              targetAction.draft?.image_url?.split(/[?#]/)[0] !== latest.draft?.image_url?.split(/[?#]/)[0] ||
              targetAction.current?.image_url?.split(/[?#]/)[0] !== latest.current?.image_url?.split(/[?#]/)[0])
          ) {
            throw new Error(t.stalePrompt)
          }

          await generateDesktopVideoImage(targetAction, liveSet, '', false, 'expand')

          if (isCurrent()) {
            closeExternal()
            updateDesktopLifeUi({ selectedSetId: liveSet.id, selectedActionId: targetAction.id, creating: false })
          }

          return
        }

        const prompt = await getDesktopVideoPrompt(latest, set, submittedRequirements, policy)

        if (isCurrent()) {
          if (externalStale) {
            setExternalFilePath(null)
          }

          setExternalAction(latest)
          setExternalBaseVersion(set.base_version)
          setExternalResolutionRevision(set.resolution_revision)
          setExternalPrompt(prompt)
          setExternalPromptRequirements(submittedRequirements)
          setExternalCopied(false)
        }
      }, isCurrent)
    } catch (failure) {
      if (isCurrent()) {
        setExternalError(backendDetailMessage(failure, errorMessage(failure)))
        notifyError(failure, t.externalTitle)
      }
    } finally {
      if (ownsRequest()) {
        setExternalPromptLoading(false)
      }
    }
  }

  const openExternal = (action: DesktopVideoAction): void => {
    const sameAction = $desktopLifeUi.get().externalActionId === action.id
    setExternalAction(action)
    setExternalBaseVersion($desktopVideoSets.get().find(set => set.id === action.set_id)?.base_version ?? 0)
    setExternalResolutionRevision(
      $desktopVideoSets.get().find(set => set.id === action.set_id)?.resolution_revision ?? 0
    )
    setExternalPrompt(null)
    setExternalError(null)

    if (!sameAction) {
      setExternalRequirements('')
      setExternalFilePath(null)
    }

    setExternalPromptRequirements('')
    setExternalReferenceZoom(null)
    setExternalCopied(false)
  }

  const closeExternal = (): void => {
    chooseAspect(null)
    externalRequest.current += 1
    setExternalAction(null)
    setExternalPrompt(null)
    setExternalError(null)
    setExternalRequirements('')
    setExternalPromptRequirements('')
    setExternalFilePath(null)
    setExternalReferenceZoom(null)
    setExternalCopied(false)
    setExternalPromptLoading(false)
    setExternalUploading(false)
    externalUploadRequest.current = false
  }

  const chooseExternalVideo = async (): Promise<void> => {
    const requestId = externalRequest.current
    const authScope = captureAuthScope()
    const isLive = beginAsync()
    const isCurrent = (): boolean => Boolean(authScope?.() && isLive() && requestId === externalRequest.current)

    try {
      const [path] = await fileApi().selectPaths({
        filters: [{ extensions: ['mp4', 'mov'], name: t.videoFilterName }],
        multiple: false,
        title: t.selectVideo
      })

      if (path && isCurrent()) {
        setExternalFilePath(path)
        setExternalError(null)
      }
    } catch (failure) {
      if (isCurrent()) {
        notifyError(failure, t.selectVideo)
      }
    }
  }

  const uploadExternalVideo = async (): Promise<void> => {
    if (!externalAction || !externalFilePath || externalUploadRequest.current || externalPromptLoading) {
      return
    }

    const action = externalAction
    const authScope = captureAuthScope()
    const isLive = beginAsync()
    const requestId = externalRequest.current
    const isCurrent = (): boolean => Boolean(authScope?.() && isLive() && requestId === externalRequest.current)

    if (!authSessionId || !authScope) {
      return
    }

    externalUploadRequest.current = true
    setExternalUploading(true)
    setExternalError(null)

    try {
      if (!externalSet || externalStale) {
        throw new Error(t.stalePrompt)
      }

      const uploaded = await withAspectChoice(
        policy =>
          uploadDesktopVideoAction(
            action,
            { ...externalSet, base_version: externalBaseVersion, resolution_revision: externalResolutionRevision },
            externalFilePath,
            authSessionId,
            'video',
            policy
          ),
        isCurrent
      )

      if (isCurrent() && uploaded) {
        closeExternal()
      } else if (isCurrent()) {
        setExternalUploading(false)
      }
    } catch (failure) {
      if (isCurrent()) {
        setExternalError(backendDetailMessage(failure, errorMessage(failure)))
        notifyError(failure, t.externalTitle)
        void refreshDesktopVideos()
        setExternalUploading(false)
      }
    } finally {
      if (isCurrent()) {
        externalUploadRequest.current = false
      }
    }
  }

  const copyExternalPrompt = async (): Promise<void> => {
    if (!externalPrompt?.prompt) {
      return
    }

    const requestId = externalRequest.current
    const authScope = captureAuthScope()
    const isLive = beginAsync()
    const isCurrent = (): boolean => Boolean(authScope?.() && isLive() && requestId === externalRequest.current)

    try {
      if ((await fileApi().writeClipboard(externalPrompt.prompt)) && isCurrent()) {
        setExternalCopied(true)
      }
    } catch (failure) {
      if (isCurrent()) {
        notifyError(failure, t.copyPrompt)
      }
    }
  }

  const confirmForExternal = async (action: DesktopVideoAction): Promise<void> => {
    if (!selected) {
      return
    }

    const scope = captureAuthScope()
    const isLive = beginAsync()
    await confirmDesktopVideoImage(action, selected, false)

    if (!scope?.() || !isLive()) {
      return
    }

    const latest = $desktopVideoSets
      .get()
      .find(set => set.id === selected.id)
      ?.actions.find(item => item.id === action.id)

    if (latest) {
      openExternal(latest)
    }
  }

  const uploadImage = async (action: DesktopVideoAction): Promise<void> => {
    const scope = captureAuthScope()
    const isLive = beginAsync()

    if (!selected || !authSessionId || !scope) {
      return
    }

    const [path] = await fileApi().selectPaths({
      filters: [{ name: t.imageFilterName, extensions: ['png', 'jpg', 'jpeg', 'webp'] }],
      multiple: false,
      title: t.uploadImage
    })

    if (path && scope() && isLive()) {
      await withAspectChoice(policy => uploadDesktopVideoAction(action, selected, path, authSessionId, 'image', policy))
    }
  }

  const replaceUploadedVideo = async (action: DesktopVideoAction, set: DesktopVideoSet): Promise<void> => {
    const scope = captureAuthScope()
    const isLive = beginAsync()

    if (!authSessionId || !scope) {
      return
    }

    const [path] = await fileApi().selectPaths({
      filters: [{ name: t.videoFilterName, extensions: ['mp4', 'mov'] }],
      multiple: false,
      title: t.selectVideo
    })

    if (path && scope() && isLive()) {
      await withAspectChoice(policy => uploadDesktopVideoAction(action, set, path, authSessionId, 'video', policy))
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
    setCreateOpen(true)
    void loadSceneLibrary('', 0)
    void hydrateWardrobe()
  }

  const actionName = (item: DesktopVideoAction): string =>
    item.preset ? t.actionNames[item.key as keyof typeof t.actionNames] || item.name : item.name

  const openAction = (item: DesktopVideoAction): void => {
    updateDesktopLifeUi({ selectedActionId: item.id, addActionOpen: false })
  }

  const actionStatus = (item: DesktopVideoAction): string =>
    desktopLifeActionStatus(item, t, queueStrings, commonStrings.actionVariants)

  const adoptCandidate = async (item: DesktopVideoAction): Promise<void> => {
    if (!selected) {
      return
    }

    const scope = captureAuthScope()
    await reviewDesktopVideoAction(item, selected, true)

    if (!scope?.() || item.key !== 'idle') {
      return
    }

    const latest = $desktopVideoSets.get().find(set => set.id === selected.id)
    const updated = latest?.actions.find(action => action.id === item.id)

    if (!latest || !updated?.playable) {
      return
    }

    if (!latest.is_current && !(await activateDesktopVideoSet(latest))) {
      return
    }

    if (!scope()) {
      return
    }

    if ($presentation.get().effectiveMode !== 'desktop') {
      await shellApi().presentation.setMode('desktop')
    }
  }

  const renderSteps = (step: number, skipImage = false): React.JSX.Element => {
    const labels = [t.stepIdea, ...(skipImage ? [] : [t.stepImage]), t.stepVideo, t.stepUse]
    const currentStep = skipImage && step >= 2 ? step - 1 : step

    return (
      <ol aria-label={t.createSet} className={styles.steps}>
        {labels.map((label, index) => (
          <li
            aria-current={currentStep === index ? 'step' : undefined}
            className={styles.step}
            data-complete={index < currentStep}
            key={label}
          >
            <span aria-hidden="true" className={styles.stepNumber}>
              {index < currentStep ? <Check size={14} /> : index + 1}
            </span>
            <span>{label}</span>
          </li>
        ))}
      </ol>
    )
  }

  const creationStatus = (item: DesktopVideoAction): React.ComponentProps<typeof CreationFeedback> | null => {
    if (item.draft?.submission_unknown || item.draft?.recovery_action === 'wait') {
      return {
        state: 'unknown',
        title: t.resultPending,
        description: t.resultPendingHint,
        actions: (
          <button
            className={BTN_SUBTLE}
            disabled={loading || busy}
            onClick={() => void refreshDesktopVideos()}
            type="button"
          >
            <RefreshCw size={16} />
            {t.refresh}
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
        title: t.statuses.failed
      }
    }

    return null
  }

  const renderFailure = (item: DesktopVideoAction): React.JSX.Element | null => {
    if (item.status !== 'failed' || item.draft?.submission_unknown || item.draft?.recovery_action === 'wait') {
      return null
    }

    return (
      <div className="space-y-2 text-sm text-body">
        <p>{t.failedAt(desktopLifeCreationStage(item, t))}</p>
        <p>
          {item.draft?.recovery_action === 'choose_activity'
            ? t.chooseActivityHint
            : item.draft?.recovery_action === 'replace_upload'
              ? t.replaceUploadHint
              : t.creationFailed}
        </p>
        {(item.draft?.error || item.error) && (
          <details>
            <summary className="cursor-pointer">{t.details}</summary>
            <p className="mt-2 break-words">{item.draft?.error || item.error}</p>
          </details>
        )}
      </div>
    )
  }

  const renderMetadata = (target: DesktopVideoSet): React.JSX.Element => (
    <div className={styles.metadata}>
      <span>
        {t.targetResolution}: {resolutionLabel(target.target_width, target.target_height)}
      </span>
      <DesktopLifeRecommendation display={recommendationDisplay} set={target} />
      <span>
        {recommendationDisplay
          ? `${t.recommendationDisplay}: ${recommendationDisplay.label}`
          : t.recommendationUnavailable}
      </span>
    </div>
  )

  const renderAction = (
    item: DesktopVideoAction,
    feedbackStatus: React.ReactNode
  ): { content: React.JSX.Element; footer: React.ReactNode } => {
    if (!selected) {
      return { content: <></>, footer: null }
    }

    const unknown = Boolean(item.draft?.submission_unknown || item.draft?.recovery_action === 'wait')
    const chooseActivity = item.draft?.recovery_action === 'choose_activity'
    const replaceUpload = item.draft?.recovery_action === 'replace_upload'
    const processing = item.status === 'processing' && !isActionPaused(item) && !unknown

    const reusesBaseImage =
      item.key !== 'idle' &&
      (item.draft
        ? item.draft.continuity === 'base' && item.draft.image_confirmed && !item.draft.video_refinement
        : item.current?.continuity === 'base')

    const hideDraftImage = Boolean(reusesBaseImage && item.draft && !item.draft.candidate_video_url)

    const imageReview = Boolean(
      item.status === 'review_pending' &&
      item.stage === 'image_review' &&
      item.draft?.image_url &&
      !item.draft.image_confirmed
    )

    const preparingImage = Boolean(
      !reusesBaseImage &&
      item.draft &&
      !item.draft.video_refinement &&
      ['planning', 'checking', 'repairing', 'image', 'image_upload'].includes(item.stage)
    )

    const candidate = Boolean(
      item.status === 'review_pending' && item.stage !== 'image_review' && item.draft?.candidate_video_url
    )

    const previewDraft = previewUsesDraft(item) && !hideDraftImage
    const showPreview = hasPreviewMedia(item, previewDraft)
    const hasCurrentPreview = hasPreviewMedia(item, false)

    const showingCurrentVideo = Boolean(item.current?.video_url && !previewDraft)

    const step =
      candidate || (!item.draft && item.current)
        ? 3
        : imageReview || preparingImage
          ? 1
          : item.draft
            ? item.draft.image_confirmed || item.draft.video_refinement || item.draft.source === 'external_upload'
              ? 2
              : 1
            : 0

    const dependent = item.key !== 'idle' && (item.draft?.continuity ?? item.continuity) === 'base'
    const requiresBase = dependent && !selected.base_image_url && !chooseActivity && !replaceUpload && !unknown

    const awaitingFirstGeneration =
      item.definition_id !== null && ['not_generated', 'blocked'].includes(item.status) && !item.current && !item.draft

    const unavailableFirstGeneration = awaitingFirstGeneration && !item.can_generate
    const persistedNote = item.draft?.feedback ?? item.current?.feedback ?? ''

    const customizationReadOnly =
      unknown || isActionPaused(item) || candidate || chooseActivity || replaceUpload || unavailableFirstGeneration

    const note = customizationReadOnly ? persistedNote : (feedback[item.id] ?? persistedNote)
    const noteDirty = note !== persistedNote
    const videoNote = item.draft?.video_feedback ?? item.current?.video_feedback ?? ''
    const canRemakeVideo = !imageReview && Boolean(item.current || item.draft?.image_confirmed)

    const affected = selected.actions.filter(
      action => action.id !== item.id && (action.current?.continuity === 'base' || action.draft?.continuity === 'base')
    ).length

    const canConfirm = item.draft && imagePreviews[`${item.draft.id}:${item.draft.image_url}`]

    const controls = unknown ? null : processing ? (
      item.draft && !['upload', 'image_upload'].includes(item.stage) ? (
        <button
          className={BTN_SUBTLE}
          disabled={busy}
          onClick={() => void run(() => pauseDesktopVideoDraft(item), t.pausingCreation)}
          title={t.stopHint}
          type="button"
        >
          {t.cancel}
        </button>
      ) : null
    ) : unavailableFirstGeneration ? null : candidate ? (
      <>
        <button
          className={BTN_PRIMARY}
          disabled={busy}
          onClick={() => void run(() => adoptCandidate(item), t.acceptingVideo)}
          type="button"
        >
          {item.key === 'idle' ? t.play : t.acceptCandidate}
        </button>
        <button className={BTN_SUBTLE} disabled={busy} onClick={() => setDiscardAction(item)} type="button">
          {t.reject}
        </button>
      </>
    ) : chooseActivity ? (
      <button
        className={BTN_PRIMARY}
        disabled={busy}
        onClick={() => updateDesktopLifeUi({ selectedActionId: null, addActionOpen: true })}
        type="button"
      >
        {t.chooseActivity}
      </button>
    ) : replaceUpload ? (
      <button
        className={BTN_PRIMARY}
        disabled={busy}
        onClick={() => void run(() => replaceUploadedVideo(item, selected), t.uploadProcessing)}
        type="button"
      >
        {t.selectVideo}
      </button>
    ) : noteDirty ? (
      <button
        className={BTN_PRIMARY}
        disabled={busy}
        onClick={() => void run(() => makeImage(item, selected, note))}
        type="button"
      >
        {t.applyCustomization}
      </button>
    ) : imageReview ? (
      <>
        <button
          className={BTN_PRIMARY}
          disabled={busy || !canConfirm}
          onClick={() => void run(() => confirmDesktopVideoImage(item, selected), t.confirmingImage)}
          type="button"
        >
          {t.confirmImage}
        </button>
        <button
          className={BTN_SUBTLE}
          disabled={busy}
          onClick={() => void run(() => makeImage(item, selected, note, true))}
          type="button"
        >
          {t.anotherImage}
        </button>
      </>
    ) : item.current && !item.draft && !item.needs_update ? (
      item.key === 'idle' ? (
        <button
          className={BTN_PRIMARY}
          disabled={busy || !item.playable || currentMatches}
          onClick={() => void run(activate)}
          type="button"
        >
          {currentMatches ? t.currentInUse : t.play}
        </button>
      ) : null
    ) : (
      <button
        className={BTN_PRIMARY}
        disabled={busy}
        onClick={() =>
          void run(() => {
            if (
              item.draft &&
              (item.draft.recovery_action === 'retry' || isActionPaused(item) || item.status === 'failed')
            ) {
              return retryDesktopVideoDraft(item, selected)
            }

            return makeVideo(item, selected, note)
          })
        }
        type="button"
      >
        {isActionPaused(item)
          ? t.resume
          : item.draft?.recovery_action === 'retry' || item.status === 'failed'
            ? t.retry
            : item.key === 'idle'
              ? t.generateImage
              : t.startAction}
      </button>
    )

    return {
      content: (
        <section className={styles.detail} key={item.id}>
          {renderSteps(step, reusesBaseImage)}
          <div>
            <h3 className="text-lg font-semibold text-strong">{actionName(item)}</h3>
          </div>
          <ActionSyncStatus targets={item.sync_targets} />
          {renderFailure(item)}
          {requiresBase ? (
            <div className="space-y-2">
              <h4 className="font-medium text-strong">{t.imageRequired}</h4>
              <p className="my-3 text-sm leading-relaxed text-body">{t.imageRequiredHint}</p>
            </div>
          ) : (
            <>
              {hideDraftImage && <p className="text-sm text-muted">{t.reusedBaseImageHint}</p>}
              {showPreview && (
                <>
                  <p className="text-sm font-medium text-strong">
                    {previewDraft
                      ? candidate
                        ? t.newVideo
                        : imageReview
                          ? t.stepImage
                          : t.candidateImage
                      : t.currentProduct}
                  </p>
                  <DesktopLifePreview
                    action={item}
                    draft={previewDraft}
                    onImageReady={() => {
                      const draft = item.draft

                      if (draft) {
                        setImagePreviews(current => ({ ...current, [`${draft.id}:${draft.image_url}`]: true }))
                      }
                    }}
                    set={selected}
                  />
                </>
              )}
              {renderMetadata(selected)}
              {hasCurrentPreview && item.draft && <p className="text-sm text-muted">{t.savedCurrent}</p>}
              {hasCurrentPreview && previewDraft && (
                <details className="rounded-xl border border-line-hairline p-3">
                  <summary className="cursor-pointer text-sm font-medium text-strong">{t.currentProduct}</summary>
                  <div className="mt-3">
                    <DesktopLifePreview action={item} set={selected} />
                  </div>
                </details>
              )}
              {!candidate &&
                item.status !== 'failed' &&
                !processing &&
                !unknown &&
                (item.error || item.draft?.error) && (
                  <p className="text-sm text-warning" role="status">
                    {t.unavailable}
                  </p>
                )}
              {item.needs_update && <p className="text-sm text-warning">{t.needsUpdate}</p>}
              {unknown || processing ? null : candidate ? (
                <>
                  <div className="space-y-2 text-sm text-body">
                    <p>{t.videoReviewHint}</p>
                    {item.draft?.review_reason && (
                      <details className="mt-2 text-xs text-muted">
                        <summary className="cursor-pointer">{t.reviewDetails}</summary>
                        <p className="mt-2 break-words">{item.draft.review_reason}</p>
                      </details>
                    )}
                  </div>
                  {item.key === 'idle' && affected > 0 && (
                    <p className="text-sm text-warning">{t.updateImpact(affected)}</p>
                  )}
                </>
              ) : imageReview ? (
                <p className="text-sm text-body">{t.imageReview}</p>
              ) : item.current && !item.draft && !item.needs_update ? (
                <p className="text-sm text-muted">{item.key === 'idle' ? t.readyHint : t.actionReadyHint}</p>
              ) : null}
              {!processing && (
                <details
                  className="rounded-xl border border-line-hairline p-4"
                  open={Boolean(note.trim() || videoNote.trim() || noteDirty) || undefined}
                >
                  <summary className="cursor-pointer text-sm font-medium text-strong">{t.advanced}</summary>
                  <div className="mt-4 flex flex-col gap-3">
                    {(showingCurrentVideo || candidate) && (
                      <button
                        className={`${BTN_SUBTLE} self-start`}
                        disabled={busy || item.status === 'processing' || item.draft?.submission_unknown}
                        onClick={() =>
                          setRefinement({ action: item, set: selected, source: candidate ? 'candidate' : 'current' })
                        }
                        type="button"
                      >
                        {t.refineVideo}
                      </button>
                    )}
                    {(note.trim() || noteDirty || (!candidate && !item.draft?.video_refinement && !unknown)) && (
                      <>
                        <label className="flex flex-col gap-2 text-sm text-body">
                          {t.feedback}
                          <textarea
                            className={`${INPUT_CLASS} min-h-20 resize-y`}
                            disabled={busy || customizationReadOnly}
                            maxLength={1000}
                            onChange={event => setFeedback(current => ({ ...current, [item.id]: event.target.value }))}
                            rows={2}
                            value={note}
                          />
                        </label>
                        {(note.trim() || noteDirty) && (
                          <div className="flex flex-wrap gap-2">
                            <button
                              className={BTN_SUBTLE}
                              disabled={busy || customizationReadOnly}
                              onClick={() => void run(() => makeImage(item, selected, note))}
                              type="button"
                            >
                              {t.applyCustomization}
                            </button>
                            <button
                              className={BTN_GHOST}
                              disabled={busy || customizationReadOnly}
                              onClick={() => setFeedback(current => ({ ...current, [item.id]: '' }))}
                              type="button"
                            >
                              {t.clearCustomization}
                            </button>
                          </div>
                        )}
                      </>
                    )}
                    {videoNote.trim() && (
                      <div className="flex flex-col gap-2 rounded-lg bg-accent/5 p-3">
                        <p className="text-sm font-medium text-strong">{t.savedVideoRequirements}</p>
                        <p className="whitespace-pre-wrap break-words text-sm text-body">{videoNote}</p>
                        {!customizationReadOnly && canRemakeVideo && (
                          <button
                            className={`${BTN_SUBTLE} self-start`}
                            disabled={busy || noteDirty}
                            onClick={() => void run(() => makeVideo(item, selected, persistedNote, ''))}
                            type="button"
                          >
                            {t.clearVideoRequirements}
                          </button>
                        )}
                      </div>
                    )}
                    {!candidate && canRemakeVideo && !item.draft?.video_refinement && (
                      <button
                        className={`${BTN_SUBTLE} self-start`}
                        disabled={busy || unknown || chooseActivity || replaceUpload || noteDirty}
                        onClick={() => void run(() => makeVideo(item, selected, note))}
                        type="button"
                      >
                        {t.remakeVideo}
                      </button>
                    )}
                    {!candidate && !dependent && (
                      <button
                        className={`${BTN_SUBTLE} self-start`}
                        disabled={busy || unknown || chooseActivity || unavailableFirstGeneration}
                        onClick={() => void run(() => makeImage(item, selected, note, true))}
                        type="button"
                      >
                        {t.regenerateImage}
                      </button>
                    )}
                    {!candidate && !dependent && (
                      <button
                        className={`${BTN_SUBTLE} self-start`}
                        disabled={busy || unknown}
                        onClick={() => void run(() => uploadImage(item), t.uploadingImage)}
                        type="button"
                      >
                        {t.uploadImage}
                      </button>
                    )}
                    {!candidate && (
                      <button
                        className={`${BTN_SUBTLE} self-start`}
                        disabled={busy || unknown || noteDirty || (imageReview && !canConfirm)}
                        onClick={() =>
                          void run(() => (imageReview ? confirmForExternal(item) : Promise.resolve(openExternal(item))))
                        }
                        type="button"
                      >
                        {imageReview ? t.confirmOnly : t.externalTitle}
                      </button>
                    )}
                    {item.draft && (
                      <button
                        className={`${BTN_GHOST} self-start`}
                        disabled={busy}
                        onClick={() => setDiscardAction(item)}
                        type="button"
                      >
                        {t.discardDraft}
                      </button>
                    )}
                    <details className="text-xs text-muted">
                      <summary className="cursor-pointer">{t.details}</summary>
                      <p className="mt-2">{item.description}</p>
                      <p className="mt-2">
                        {item.kind === 'loop' ? t.loop : t.once} · {item.target_duration_seconds}s{' '}
                        {item.actual_duration_ms !== null && `· ${(item.actual_duration_ms / 1000).toFixed(1)}s`}
                      </p>
                      {item.use_when.length > 0 && (
                        <p className="mt-2">
                          {t.useWhen}：{item.use_when.join('；')}
                        </p>
                      )}
                      {item.avoid_when.length > 0 && (
                        <p className="mt-2">
                          {t.avoidWhen}：{item.avoid_when.join('；')}
                        </p>
                      )}
                    </details>
                  </div>
                </details>
              )}
            </>
          )}
        </section>
      ),
      footer: requiresBase ? (
        <CreationActionBar description={feedbackStatus}>
          <button className={BTN_PRIMARY} onClick={() => idle && openAction(idle)} type="button">
            {t.openImage}
          </button>
        </CreationActionBar>
      ) : controls ? (
        <CreationActionBar description={feedbackStatus}>{controls}</CreationActionBar>
      ) : feedbackStatus ? (
        <CreationActionBar description={feedbackStatus}>{null}</CreationActionBar>
      ) : null
    }
  }

  const selectedScene =
    creationScene?.id === create.sceneId
      ? creationScene.scene
      : (scenes.find(scene => scene.id === create.sceneId) ?? (activeScene?.id === create.sceneId ? activeScene : null))

  const selectedOutfit = outfits.find(outfit => String(outfit.id) === create.outfitId)
  const materialReady = selectedScene?.status === 'ready' && selectedOutfit?.status === 'ready'
  const recommendationDisplay = resolveDesktopLifeRecommendationDisplay(presentation)

  const resolutionReady = readyDesktopLifeResolution(create, presentation) !== null

  const displayedActions =
    selected?.actions.filter(
      item =>
        item.key !== 'idle' && (item.definition_id !== null || item.current || item.draft || item.status === 'failed')
    ) ?? []

  const statusAction = !createOpen ? (action ?? idle) : null
  const taskStatus = statusAction ? creationStatus(statusAction) : null

  const workspaceStatus =
    busy || visibleOperationError ? (
      <CreationFeedback
        description={visibleOperationError || undefined}
        state={busy ? 'working' : 'error'}
        title={busy ? requestTitle : t.operationFailed}
      />
    ) : taskStatus ? (
      <CreationFeedback {...taskStatus} key={statusAction?.draft?.id ?? statusAction?.id} />
    ) : null

  const detailView = !createOpen && action ? renderAction(action, workspaceStatus) : null

  const externalFeedback =
    externalUploading || externalPromptLoading || externalStale || externalError ? (
      <CreationFeedback
        description={externalError || (externalStale ? t.stalePrompt : undefined)}
        state={externalUploading || externalPromptLoading ? 'working' : 'error'}
        title={externalUploading ? t.uploadProcessing : externalPromptLoading ? t.promptLoading : t.operationFailed}
      />
    ) : null

  const createFooter = createOpen ? (
    <CreationActionBar
      description={
        workspaceStatus ??
        (!resolutionReady ? t.screenUnavailable : !materialReady ? t.creationMissingMaterials : t.creationReady)
      }
    >
      <button
        className={BTN_PRIMARY}
        disabled={busy || !materialReady || !resolutionReady}
        form="desktop-life-create"
        type="submit"
      >
        {busy
          ? t.preparing
          : create.source === 'ai'
            ? t.generateImage
            : create.source === 'image_upload'
              ? t.uploadImage
              : t.selectVideo}
      </button>
    </CreationActionBar>
  ) : null

  const overviewFooter =
    !createOpen && !action && selected && idle ? (
      <CreationActionBar description={workspaceStatus}>
        {(idle.draft || !idle.playable || idle.needs_update) && (
          <button className={BTN_PRIMARY} disabled={busy} onClick={() => openAction(idle)} type="button">
            {t.continue}
          </button>
        )}
        {idle.playable && (
          <button
            className={idle.draft ? BTN_SUBTLE : BTN_PRIMARY}
            disabled={busy || currentMatches}
            onClick={() => void run(activate)}
            type="button"
          >
            {currentMatches ? t.currentInUse : idle.draft ? t.useCurrent : t.play}
          </button>
        )}
        {idle.current && !idle.draft && (
          <button className={BTN_SUBTLE} onClick={() => openAction(idle)} type="button">
            {t.adjustImage}
          </button>
        )}
      </CreationActionBar>
    ) : null

  const libraryFooter =
    !createOpen && !selected ? (
      <CreationActionBar description={workspaceStatus}>
        <button className={BTN_PRIMARY} disabled={busy} onClick={openCreate} type="button">
          <Plus size={16} />
          {t.createSet}
        </button>
      </CreationActionBar>
    ) : null

  const workspaceFooter = createFooter ?? detailView?.footer ?? overviewFooter ?? libraryFooter

  const customActionForm = selected ? (
    <>
      <h3 className="mt-3 text-sm font-medium text-strong">{t.addCustom}</h3>
      <form
        className="mt-4 flex flex-col gap-3"
        onSubmit={event => {
          event.preventDefault()
          const isLive = beginAsync()
          void run(async () => {
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

            setDesign(current => ({ ...current, name: '', description: '' }))
            setDesignMessage(proposal.review_reason || t.designSubmitted)

            if (proposal.action_id) {
              updateDesktopLifeUi({ selectedActionId: proposal.action_id, addActionOpen: false })
            }
          })
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
            maxLength={600}
            minLength={10}
            onChange={event => setDesign(current => ({ ...current, description: event.target.value }))}
            required
            rows={4}
            value={design.description}
          />
        </label>
        <details>
          <summary className="cursor-pointer text-sm text-muted">{t.actionSettings}</summary>
          <div className="mt-3 flex flex-col gap-3">
            <input
              aria-label={t.actionName}
              className={INPUT_CLASS}
              maxLength={64}
              onChange={event => setDesign(current => ({ ...current, name: event.target.value }))}
              placeholder={t.nameOptional}
              value={design.name}
            />
            <PanelSelect
              ariaLabel={t.actionSettings}
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
        </details>
        <button
          className={`${BTN_PRIMARY} self-start`}
          disabled={busy || design.description.trim().length < 10}
          type="submit"
        >
          {t.startAction}
        </button>
        {(design.name.trim() || design.description.trim()) && (
          <button
            className={`${BTN_GHOST} self-start`}
            disabled={busy}
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
      <CreationWorkspace
        footer={workspaceFooter ? <div className={styles.fixedRegion}>{workspaceFooter}</div> : undefined}
        scrollRef={scrollRef}
      >
        <div className={styles.content}>
          {(selected || createOpen) && (
            <button
              className={`${BTN_GHOST} self-start`}
              onClick={() => {
                if (createOpen) {
                  setCreateOpen(false)
                } else if (selectedActionId !== null) {
                  updateDesktopLifeUi({ selectedActionId: null })
                } else {
                  setSelectedSetId(null)
                }
              }}
              type="button"
            >
              <ArrowLeft size={15} />
              {selectedActionId !== null && !createOpen ? t.backToBackground : t.backToLibrary}
            </button>
          )}
          <header className={styles.header}>
            <div>
              <h2 className={styles.heading}>{createOpen ? t.createSet : selected?.title || t.title}</h2>
              {(!action || createOpen) && (
                <p className={styles.intro}>
                  {createOpen ? t.createHint : !selected ? t.hint : idle?.playable ? t.readyHint : t.leaveHint}
                </p>
              )}
            </div>
          </header>

          {!createOpen && !selected && state && (
            <details className={styles.settings}>
              <summary>
                <Settings size={16} />
                {t.preferences}
                <ChevronDown size={16} />
              </summary>
              <div className="mt-4 space-y-4">
                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-strong">{t.autonomy}</p>
                    <p className="mt-1 text-xs leading-relaxed text-muted">{t.autonomyHint}</p>
                  </div>
                  <Toggle
                    ariaLabel={t.autonomy}
                    checked={state.autonomous_enabled}
                    disabled={busy}
                    onChange={() => void savePreferences({ autonomous_enabled: !state.autonomous_enabled })}
                  />
                </div>
                <div className="flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-strong">{t.pin}</p>
                    <p className="mt-1 text-xs leading-relaxed text-muted">{t.pinHint}</p>
                  </div>
                  <Toggle
                    ariaLabel={t.pin}
                    checked={state.pinned}
                    disabled={busy}
                    onChange={() => void savePreferences({ pinned: !state.pinned })}
                  />
                </div>
                {state.pinned && (
                  <p className="text-xs text-muted" role="status">
                    {t.pinnedHint}
                  </p>
                )}
                {preferencesSaving && (
                  <p aria-live="polite" className="text-xs text-muted">
                    {t.preferencesSaving}
                  </p>
                )}
              </div>
            </details>
          )}
          {(error || state?.preparation_error) && (
            <div className="space-y-2 text-sm text-body" role="alert">
              <p>{t.unavailable}</p>
              <button
                className={`${BTN_SUBTLE} mt-3`}
                disabled={loading || busy}
                onClick={() => void refreshDesktopVideos()}
                type="button"
              >
                {t.refresh}
              </button>
              <details className="mt-3 text-xs text-muted">
                <summary className="cursor-pointer">{t.details}</summary>
                <p className="mt-2 break-words">{state?.preparation_error || error}</p>
              </details>
            </div>
          )}
          {createOpen ? (
            <>
              {renderSteps(0)}
              <form
                className={styles.form}
                id="desktop-life-create"
                onSubmit={event => {
                  event.preventDefault()
                  const currentCreate = $desktopLifeUi.get().create
                  const target = readyDesktopLifeResolution(currentCreate, $presentation.get())

                  if (busy || !target || !materialReady) {
                    return
                  }

                  const scope = captureAuthScope()
                  const isLive = beginAsync()
                  void run(async () => {
                    const created = await createDesktopVideoSet({
                      request_id: currentCreate.requestId,
                      name: currentCreate.name.trim() || t.newSceneName,
                      scene_id: Number(currentCreate.sceneId),
                      outfit_id: Number(currentCreate.outfitId),
                      source: currentCreate.source,
                      target_width: target.width,
                      target_height: target.height,
                      feedback: currentCreate.feedback.trim()
                    })

                    if (!scope?.() || !isLive()) {
                      return
                    }

                    const first = created.actions.find(item => item.key === 'idle')
                    updateDesktopLifeUi({
                      selectedSetId: created.id,
                      selectedActionId: first?.id ?? null,
                      creating: false,
                      create: newDesktopLifeCreation()
                    })

                    if (first && currentCreate.source === 'image_upload') {
                      const [path] = await fileApi().selectPaths({
                        title: t.uploadImage,
                        multiple: false,
                        filters: [{ name: t.imageFilterName, extensions: ['png', 'jpg', 'jpeg', 'webp'] }]
                      })

                      if (path && authSessionId && scope()) {
                        await withAspectChoice(policy =>
                          uploadDesktopVideoAction(first, created, path, authSessionId, 'image', policy)
                        )
                      }
                    }

                    if (first && currentCreate.source === 'video_upload') {
                      openExternal(first)
                    }
                  })
                }}
              >
                <DesktopLifeDisplayPicker
                  disabled={busy}
                  displayId={create.targetDisplayId}
                  displays={presentation.displays}
                  onChange={targetDisplayId => {
                    setCreate(current => reconcileDesktopLifeDisplay(current, $presentation.get(), targetDisplayId))
                  }}
                />
                <section className={styles.section}>
                  <div>
                    <h3 className="text-sm font-medium text-strong">{t.referencesTitle}</h3>
                  </div>
                  <DesktopLifeReferences
                    disabled={busy}
                    onPickOutfit={outfitId =>
                      setCreate(current => ({ ...current, outfitId, requestId: crypto.randomUUID() }))
                    }
                    onPickScene={scene => {
                      setCreationScene({ id: scene.id, scene })
                      setCreate(current => ({
                        ...current,
                        sceneId: scene.id,
                        sceneLabel: scene.title,
                        requestId: crypto.randomUUID()
                      }))
                    }}
                    onPrepareMaterial={onPrepareMaterial}
                    outfit={selectedOutfit}
                    outfitId={create.outfitId}
                    scene={selectedScene}
                    sceneId={create.sceneId}
                    sceneLabel={create.sceneLabel}
                  />
                </section>
                <details
                  className="rounded-xl border border-line-hairline p-4"
                  open={Boolean(create.name.trim() || create.feedback.trim() || create.source !== 'ai') || undefined}
                >
                  <summary className="cursor-pointer text-sm font-medium text-strong">{t.advanced}</summary>
                  <div className="mt-4 flex flex-col gap-4">
                    <label className={styles.field}>
                      {t.nameOptional}
                      <input
                        className={INPUT_CLASS}
                        maxLength={160}
                        onChange={event => setCreate(current => ({ ...current, name: event.target.value }))}
                        placeholder={t.newSceneName}
                        value={create.name}
                      />
                    </label>
                    <div aria-label={t.creationSource} className={styles.sourceChoice} role="group">
                      {(['ai', 'image_upload', 'video_upload'] as DesktopVideoSource[]).map(source => (
                        <button
                          aria-pressed={create.source === source}
                          className={BTN_SUBTLE}
                          disabled={busy}
                          key={source}
                          onClick={() => setCreate(current => ({ ...current, source, requestId: crypto.randomUUID() }))}
                          type="button"
                        >
                          {source === 'ai' ? t.useAI : source === 'image_upload' ? t.imageSource : t.videoSource}
                        </button>
                      ))}
                    </div>
                    {create.source === 'ai' ? (
                      <label className={styles.field}>
                        {t.idea}
                        <textarea
                          className={`${INPUT_CLASS} min-h-28 resize-y font-normal`}
                          maxLength={1000}
                          onChange={event =>
                            setCreate(current => ({
                              ...current,
                              feedback: event.target.value,
                              requestId: crypto.randomUUID()
                            }))
                          }
                          placeholder={t.ideaExample}
                          rows={4}
                          value={create.feedback}
                        />
                      </label>
                    ) : (
                      <p className="text-sm text-body">{t.ownMediaHint}</p>
                    )}
                    {(create.name.trim() || create.feedback.trim() || create.source !== 'ai') && (
                      <button
                        className={`${BTN_GHOST} self-start`}
                        disabled={busy}
                        onClick={() =>
                          setCreate(current => ({
                            ...current,
                            name: '',
                            feedback: '',
                            source: 'ai',
                            requestId: crypto.randomUUID()
                          }))
                        }
                        type="button"
                      >
                        {t.clearCustomization}
                      </button>
                    )}
                  </div>
                </details>
              </form>
            </>
          ) : action ? (
            detailView?.content
          ) : selected ? (
            <>
              {idle && (
                <section className="flex flex-col gap-4">
                  {renderFailure(idle)}

                  {idle.draft?.candidate_video_url ? (
                    <span className={`${styles.badge} self-start`}>{t.newVideo}</span>
                  ) : idle.stage === 'image_review' ? (
                    <span className={`${styles.badge} self-start`}>{t.stepImage}</span>
                  ) : null}
                  {hasPreviewMedia(idle, previewUsesDraft(idle)) && (
                    <DesktopLifePreview action={idle} draft={previewUsesDraft(idle)} set={selected} />
                  )}
                  {renderMetadata(selected)}
                  {hasPreviewMedia(idle, false) && idle.draft && <p className="text-sm text-muted">{t.savedCurrent}</p>}

                  {!idle.playable && <p className="text-sm text-muted">{t.noActionsHint}</p>}
                </section>
              )}
              <section className="flex flex-col gap-4 border-t border-line-hairline pt-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-base font-medium text-strong">{t.actions}</h3>
                  {actionsToCreate.length > 0 && (
                    <button
                      className={BTN_SUBTLE}
                      onClick={() => updateDesktopLifeUi({ addActionOpen: true })}
                      type="button"
                    >
                      {t.design}
                    </button>
                  )}
                </div>
                {displayedActions.length > 0 && (
                  <div className={styles.actionGrid}>
                    {displayedActions.map(item => (
                      <button
                        className={styles.libraryCard}
                        key={item.id}
                        onClick={() => openAction(item)}
                        type="button"
                      >
                        <DesktopLifeCover
                          target={selected}
                          url={
                            item.current?.poster_url ||
                            item.draft?.candidate_poster_url ||
                            item.draft?.image_url ||
                            null
                          }
                        />
                        <span className="font-medium text-strong">{actionName(item)}</span>
                        <span className="text-xs text-muted">{actionStatus(item)}</span>
                      </button>
                    ))}
                  </div>
                )}
                {selected.proposals
                  ?.filter(proposal => !proposal.action_id)
                  .map(proposal => (
                    <div className="rounded-xl bg-black/5 p-4" key={proposal.proposal_id}>
                      <p className="text-sm font-medium text-strong">{proposal.name}</p>
                      <p className="mt-1 text-sm text-muted" role="status">
                        {t.proposalStatuses[proposal.status as keyof typeof t.proposalStatuses] || t.preparing}
                      </p>
                      {proposal.review_reason && <p className="mt-2 text-sm text-body">{proposal.review_reason}</p>}
                    </div>
                  ))}
              </section>
              <details
                className="rounded-xl border border-line-hairline p-4"
                open={
                  Boolean(
                    rename.trim() || (actionsToCreate.length === 0 && (design.name.trim() || design.description.trim()))
                  ) || undefined
                }
              >
                <summary className="cursor-pointer text-sm text-body">{t.advanced}</summary>
                <div className="mt-4 flex flex-col gap-4">
                  {actionsToCreate.length === 0 && customActionForm}
                  <form
                    className="flex flex-wrap gap-2"
                    onSubmit={event => {
                      event.preventDefault()
                      void run(async () => {
                        await renameDesktopVideoSet(selected.id, rename.trim())
                        setRename('')
                      })
                    }}
                  >
                    <input
                      aria-label={t.setName}
                      className={`${INPUT_CLASS} min-w-0 flex-1`}
                      maxLength={160}
                      onChange={event => setRename(event.target.value)}
                      placeholder={selected.title}
                      value={rename}
                    />
                    <button className={BTN_SUBTLE} disabled={busy || !rename.trim()} type="submit">
                      {t.renameSet}
                    </button>
                  </form>
                  <button
                    className={`${BTN_GHOST} self-start`}
                    disabled={busy}
                    onClick={() => setDeleteSetId(selected.id)}
                    type="button"
                  >
                    {t.deleteSet}
                  </button>
                </div>
              </details>
            </>
          ) : (
            <>
              {loading && !sets.length && (
                <p className="py-8 text-center text-sm text-muted" role="status">
                  {t.loading}
                </p>
              )}
              {!loading && !sets.length && (
                <div className="rounded-2xl bg-accent/5 px-5 py-12 text-center">
                  <h3 className="text-lg font-medium text-strong">{t.noSets}</h3>
                  <p className="mx-auto mt-3 max-w-md text-sm leading-relaxed text-muted">{t.emptyHint}</p>
                </div>
              )}
              <div className={styles.toolbar}>
                <h3 className="mr-auto text-sm font-medium text-strong">{t.libraryTitle}</h3>
                <button
                  className={BTN_GHOST}
                  disabled={loading || busy}
                  onClick={() => void refreshDesktopVideos()}
                  type="button"
                >
                  <RefreshCw size={16} />
                  {t.refresh}
                </button>
              </div>
              <div className={styles.libraryGrid}>
                {sets.map(item => {
                  const idle = item.actions.find(action => action.key === 'idle')

                  const unfinished = item.actions.some(
                    action => action.draft || (action.key === 'idle' && !action.current)
                  )

                  const pendingAction =
                    item.actions.find(action => action.draft?.submission_unknown) ??
                    item.actions.find(action => action.status === 'processing' && !isActionPaused(action)) ??
                    item.actions.find(
                      action => action.status === 'review_pending' || action.stage === 'image_review'
                    ) ??
                    item.actions.find(isActionPaused) ??
                    item.actions.find(action => action.draft)

                  const statusAction = pendingAction ?? idle

                  return (
                    <article className={styles.libraryCard} data-current={item.is_current} key={item.id}>
                      <button className={styles.cardLink} onClick={() => setSelectedSetId(item.id)} type="button">
                        <DesktopLifeCover
                          target={item}
                          url={
                            idle?.current?.poster_url ||
                            idle?.draft?.candidate_poster_url ||
                            idle?.draft?.image_url ||
                            item.base_image_url
                          }
                        />
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <strong className={styles.cardTitle}>{item.title}</strong>
                          {item.is_current && <span className={styles.badge}>{t.current}</span>}
                        </div>
                        <span className={styles.cardStatus}>
                          {statusAction ? (
                            <>
                              {statusAction.key !== 'idle' && <>{actionName(statusAction)} · </>}
                              {actionStatus(statusAction)}
                            </>
                          ) : (
                            t.notCreated
                          )}
                        </span>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs text-muted">
                            {resolutionLabel(item.target_width, item.target_height)}
                          </span>
                          <DesktopLifeRecommendation display={recommendationDisplay} set={item} />
                        </div>
                      </button>
                      <button
                        className={`${BTN_SUBTLE} self-start`}
                        disabled={busy || (item.is_current && !unfinished)}
                        onClick={() => {
                          if (idle?.playable && !item.is_current && !unfinished) {
                            void run(() => activate(item))
                          } else {
                            updateDesktopLifeUi({
                              selectedSetId: item.id,
                              selectedActionId: pendingAction?.id ?? idle?.id ?? null
                            })
                          }
                        }}
                        type="button"
                      >
                        {idle?.playable && !item.is_current && !unfinished
                          ? t.play
                          : item.is_current && !unfinished
                            ? t.current
                            : t.continue}
                      </button>
                    </article>
                  )
                })}
              </div>
            </>
          )}
        </div>
      </CreationWorkspace>
      {addActionOpen && selected && actionsToCreate.length > 0 && (
        <WizardModal
          onClose={() => updateDesktopLifeUi({ addActionOpen: false })}
          regionId="desktop-life-add-action"
          title={t.design}
          widthClass="max-w-2xl"
        >
          <p className="mb-4 text-sm leading-relaxed text-muted">{t.addHint}</p>
          <div className={styles.actionGrid}>
            {actionsToCreate.map(item => (
              <button
                className="flex flex-col gap-2 rounded-xl border border-line-hairline p-4 text-left hover:bg-black/5"
                key={item.id}
                onClick={() => openAction(item)}
                type="button"
              >
                <strong className="text-sm text-strong">{actionName(item)}</strong>
                <span className="text-xs text-muted">{actionStatus(item)}</span>
              </button>
            ))}
          </div>
          <details
            className="mt-5 border-t border-line-hairline pt-4"
            open={Boolean(design.name.trim() || design.description.trim()) || undefined}
          >
            <summary className="cursor-pointer text-sm font-medium text-strong">{t.advanced}</summary>
            {customActionForm}
          </details>
        </WizardModal>
      )}
      {discardAction && (
        <ConfirmDialog
          confirmLabel={t.discardConfirm}
          description={t.discardHint}
          key={`discard:${discardAction.id}:${discardAction.draft?.id ?? ''}`}
          onConfirm={() => perform(() => cancelDesktopVideoDraft(discardAction))}
          onOpenChange={open => {
            if (!open) {
              setDiscardAction(null)
            }
          }}
          open
          title={t.discardDraft}
        />
      )}
      {selected && deleteSetId === selected.id && (
        <ConfirmDialog
          confirmLabel={t.deleteSet}
          description={t.deleteHint}
          key={`delete:${selected.id}`}
          onConfirm={async () => {
            const isLive = beginAsync()
            await perform(() => deleteDesktopVideoSet(selected.id))

            if (isLive()) {
              setDeleteSetId(current => (current === selected.id ? null : current))

              if ($desktopLifeUi.get().selectedSetId === selected.id) {
                setSelectedSetId(null)
              }
            }
          }}
          onOpenChange={open => {
            if (!open) {
              setDeleteSetId(null)
            }
          }}
          open
          title={t.deleteSet}
        />
      )}
      {externalAction && (
        <WizardModal
          backdropClose={!externalUploading}
          closeDisabled={externalUploading}
          escClose={!externalUploading && !externalReferenceZoom && !aspectChoice}
          footer={
            <CreationActionBar
              className="w-full"
              description={
                externalFeedback ??
                (externalFilePath ? `${t.selectedVideo}: ${localFileName(externalFilePath)}` : t.uploadDirect)
              }
            >
              <button
                className={BTN_PRIMARY}
                disabled={!externalFilePath || externalUploading || externalPromptLoading || externalStale}
                onClick={() => void uploadExternalVideo()}
                type="button"
              >
                {externalUploading ? t.uploadProcessing : t.upload}
              </button>
              <button className={BTN_GHOST} disabled={externalUploading} onClick={closeExternal} type="button">
                {t.close}
              </button>
            </CreationActionBar>
          }
          onClose={externalUploading ? () => undefined : closeExternal}
          regionId="desktop-life-external"
          title={t.externalTitle}
          widthClass="max-w-2xl"
        >
          <div className="flex flex-col gap-5">
            <p className="text-sm leading-relaxed text-body">{t.uploadDirect}</p>
            <section className="flex flex-col gap-3">
              <button
                className={`${BTN_SUBTLE} self-start`}
                disabled={externalUploading}
                onClick={() => void chooseExternalVideo()}
                type="button"
              >
                {t.selectVideo}
              </button>
              {externalFilePath && (
                <span className="max-w-full truncate text-sm text-body" title={externalFilePath}>
                  {t.selectedVideo}: {localFileName(externalFilePath)}
                </span>
              )}
            </section>
            <details
              className="border-t border-line-hairline pt-4"
              onToggle={event => {
                if (event.currentTarget.open && !externalPrompt && !externalPromptLoading) {
                  void loadExternalPrompt(externalAction, externalRequirements)
                }
              }}
            >
              <summary className="cursor-pointer text-sm font-medium text-strong">{t.externalReferences}</summary>
              <div className="mt-4 flex flex-col gap-4">
                <p className="text-sm leading-relaxed text-muted">{t.externalHint}</p>
                <label className="flex flex-col gap-2 text-sm text-body">
                  {t.requirements}
                  <textarea
                    className={`${INPUT_CLASS} min-h-20 resize-y`}
                    disabled={externalUploading || externalPromptLoading}
                    maxLength={600}
                    onChange={event => setExternalRequirements(event.target.value)}
                    rows={2}
                    value={externalRequirements}
                  />
                </label>
                {externalPromptLoading ? (
                  <p className="text-sm text-muted" role="status">
                    {t.promptLoading}
                  </p>
                ) : (
                  (!externalPrompt || externalStale || externalRequirements.trim() !== externalPromptRequirements) && (
                    <button
                      className={`${BTN_SUBTLE} self-start`}
                      disabled={externalUploading}
                      onClick={() => void loadExternalPrompt(externalAction, externalRequirements)}
                      type="button"
                    >
                      {externalPrompt ? t.updatePrompt : t.retryPrompt}
                    </button>
                  )
                )}
                {externalPrompt ? (
                  <section className="flex flex-col gap-3">
                    <div className="flex items-center justify-between gap-2">
                      <h4 className="text-sm font-medium text-strong">{t.prompt}</h4>
                      <button
                        className={BTN_SUBTLE}
                        disabled={externalStale}
                        onClick={() => void copyExternalPrompt()}
                        type="button"
                      >
                        {externalCopied ? t.copied : t.copyPrompt}
                      </button>
                    </div>
                    <textarea
                      aria-label={t.prompt}
                      className={`${INPUT_CLASS} min-h-40 resize-y text-sm leading-relaxed`}
                      readOnly
                      rows={7}
                      value={externalPrompt.prompt}
                    />
                    <h4 className="mt-2 text-sm font-medium text-strong">{t.references}</h4>
                    {externalPrompt.references?.length ? (
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                        {externalPrompt.references.map(reference => (
                          <ExternalReference
                            key={`${reference.label}:${reference.url}`}
                            onZoom={url => setExternalReferenceZoom({ label: reference.label, url })}
                            reference={reference}
                          />
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-muted">{t.noReferences}</p>
                    )}
                  </section>
                ) : (
                  !externalPromptLoading && <p className="text-sm text-muted">{t.promptEmpty}</p>
                )}
              </div>
            </details>
          </div>
        </WizardModal>
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
        <WizardModal onClose={() => chooseAspect(null)} regionId="desktop-life-aspect-choice" title={t.aspectMismatch}>
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
                {t.cancelUpload}
              </button>
            </div>
          </div>
        </WizardModal>
      )}
      {externalReferenceZoom && (
        <PortraitLightbox
          name={externalReferenceZoom.label}
          onClose={() => setExternalReferenceZoom(null)}
          url={externalReferenceZoom.url}
        />
      )}
    </div>
  )
}
