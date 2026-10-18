import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

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
  desktopDisplayResolution,
  type DesktopVideoAction,
  desktopVideoAspectMismatch,
  type DesktopVideoAspectMismatch,
  type DesktopVideoAspectPolicy,
  type DesktopVideoPromptReference,
  type DesktopVideoRefinementPreview,
  type DesktopVideoRefinementSource,
  type DesktopVideoSet,
  type DesktopVideoSource,
  generateDesktopVideoAction,
  generateDesktopVideoImage,
  getDesktopVideoPrompt,
  pauseDesktopVideoDraft,
  playDesktopVideoAction,
  previewDesktopVideoRefinement,
  refineDesktopVideo,
  refreshDesktopVideos,
  renameDesktopVideoSet,
  resumeDesktopVideoDraft,
  reviewDesktopVideoAction,
  setDesktopVideoPreferences,
  uploadDesktopVideoAction
} from '@/modules/desktop-videos'
import { useResolvedMediaSrc } from '@/modules/media'
import {
  $activeScene,
  $sceneLibrary,
  $scenePage,
  $sceneTotal,
  loadSceneDetail,
  loadSceneLibrary,
  PAGE_SIZE,
  type SceneAsset
} from '@/modules/scene'
import { PortraitLightbox } from '@/shared'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useImageActions } from '@/shared/hooks/use-image-actions'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { ArrowLeft } from '@/shared/lib/icons'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, INPUT_CLASS, PanelSelect, Toggle, WizardModal } from '@/shared/panel'
import { fileApi, shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { useStrings } from '@/shared/strings'
import { desktopMediaLayout } from '@client-shared/desktop-media-geometry'
import { errorMessage } from '@runtime'

import {
  readyDesktopLifeResolution,
  reconcileDesktopLifeDisplay,
  resolveDesktopLifeRecommendationDisplay
} from './desktop-life-display'
import { DesktopLifeDisplayPicker, DesktopLifeRecommendation, resolutionLabel } from './desktop-life-resolution'
import { $desktopLifeUi, newDesktopLifeCreation, updateDesktopLifeUi } from './desktop-life-state'
import { useDesktopLifeStrings } from './desktop-life-strings'

function isActionPaused(action: DesktopVideoAction): boolean {
  return action.status === 'processing' && action.stage === 'paused'
}

function DesktopLifeCover({ url, target }: { url: string | null; target?: DesktopVideoSet }): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const media = useResolvedMediaSrc({ type: 'image', url: url ?? '' })
  const [size, setSize] = useState({ url, width: 0, height: 0 })

  const frameWidth = target?.target_width ?? 16
  const frameHeight = target?.target_height ?? 9

  const layout = desktopMediaLayout(
    size.url === url ? size.width : 0,
    size.url === url ? size.height : 0,
    frameWidth,
    frameHeight
  )

  return media.status === 'ready' ? (
    <div
      className="relative isolate w-full overflow-hidden rounded-lg bg-black/20"
      style={{ aspectRatio: frameWidth / frameHeight }}
    >
      <div
        className="absolute -inset-8 -z-10 bg-cover bg-center blur-xl brightness-75"
        style={{ backgroundImage: `url("${media.src}")` }}
      />
      <img
        alt=""
        className="object-center"
        onLoad={event =>
          setSize({ url, width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })
        }
        src={media.src}
        style={{
          position: 'absolute',
          left: `${(layout.left / frameWidth) * 100}%`,
          top: `${(layout.top / frameHeight) * 100}%`,
          width: `${(layout.width / frameWidth) * 100}%`,
          height: `${(layout.height / frameHeight) * 100}%`,
          objectFit: layout.objectFit
        }}
      />
    </div>
  ) : (
    <div
      className="flex w-full items-center justify-center rounded-lg bg-black/10 text-xs text-muted"
      style={{ aspectRatio: target ? target.target_width / target.target_height : 16 / 9 }}
    >
      {t.noPreview}
    </div>
  )
}

function DesktopLifePreview({
  action,
  set,
  draft = false,
  onImageReady
}: {
  action: DesktopVideoAction
  set: DesktopVideoSet
  draft?: boolean
  onImageReady?: () => void
}): React.JSX.Element {
  const [attempt, setAttempt] = useState(0)

  const mediaUrl = draft
    ? action.draft?.candidate_video_url || action.draft?.image_url
    : action.current?.video_url || action.current?.poster_url

  return (
    <ResolvedDesktopLifePreview
      action={action}
      draft={draft}
      key={`${draft ? action.draft?.id : action.id}:${mediaUrl?.split(/[?#]/)[0] ?? ''}:${attempt}`}
      onImageReady={onImageReady}
      onRetry={() => setAttempt(value => value + 1)}
      set={set}
    />
  )
}

function ResolvedDesktopLifePreview({
  action,
  set,
  draft,
  onImageReady,
  onRetry
}: {
  action: DesktopVideoAction
  set: DesktopVideoSet
  draft: boolean
  onImageReady?: () => void
  onRetry: () => void
}): React.JSX.Element {
  const active = usePanelActivity()
  const t = useDesktopLifeStrings()
  const videoUrl = draft ? action.draft?.candidate_video_url : action.current?.video_url
  const imageUrl = draft ? action.draft?.candidate_poster_url || action.draft?.image_url : action.current?.poster_url
  const url = videoUrl || imageUrl || ''
  const media = useResolvedMediaSrc({ type: videoUrl ? 'video' : 'image', url })
  const posterMedia = useResolvedMediaSrc({ type: 'image', url: imageUrl || '' })
  const [failed, setFailed] = useState(false)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const video = useRef<HTMLVideoElement>(null)
  const frameWidth = draft ? (action.draft?.target_width ?? set.target_width) : set.target_width
  const frameHeight = draft ? (action.draft?.target_height ?? set.target_height) : set.target_height
  const actualWidth = draft ? action.draft?.frame_width : action.current?.width
  const actualHeight = draft ? action.draft?.frame_height : action.current?.height

  const updateVideoSize = (event: React.SyntheticEvent<HTMLVideoElement>): void => {
    setSize({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight })
  }

  const layout = desktopMediaLayout(
    size.width || actualWidth || 0,
    size.height || actualHeight || 0,
    frameWidth,
    frameHeight
  )

  const mediaStyle = {
    position: 'absolute',
    left: `${(layout.left / frameWidth) * 100}%`,
    top: `${(layout.top / frameHeight) * 100}%`,
    width: `${(layout.width / frameWidth) * 100}%`,
    height: `${(layout.height / frameHeight) * 100}%`,
    objectFit: layout.objectFit
  } as const

  useEffect(() => {
    if (!active) {
      video.current?.pause()
    }
  }, [active])

  return (
    <div className="flex flex-col gap-2">
      <div
        className="relative isolate flex w-full items-center justify-center overflow-hidden rounded-xl bg-black/20"
        style={{ aspectRatio: frameWidth / frameHeight }}
      >
        {posterMedia.status === 'ready' && (
          <div
            className="absolute -inset-8 -z-10 bg-cover bg-center blur-xl brightness-75"
            style={{ backgroundImage: `url("${posterMedia.src}")` }}
          />
        )}
        {media.status === 'ready' && !failed ? (
          videoUrl ? (
            <video
              className="object-center"
              controls
              loop={action.kind === 'loop'}
              muted
              onError={() => setFailed(true)}
              onLoadedMetadata={updateVideoSize}
              onResize={updateVideoSize}
              playsInline
              preload="metadata"
              ref={video}
              src={media.src}
              style={mediaStyle}
            />
          ) : (
            <img
              alt={action.name}
              className="object-center"
              onError={() => setFailed(true)}
              onLoad={event => {
                setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })
                onImageReady?.()
              }}
              src={media.src}
              style={mediaStyle}
            />
          )
        ) : (
          <div className="flex flex-col items-center gap-3 p-5 text-center text-sm text-muted">
            <p>{failed || media.status === 'failed' ? t.previewFailed : url ? t.loading : t.noPreview}</p>
            {(failed || media.status === 'failed') && (
              <button className={BTN_SUBTLE} onClick={onRetry} type="button">
                {t.retryPreview}
              </button>
            )}
          </div>
        )}
      </div>
      {actualWidth && actualHeight ? (
        <p className="text-xs text-muted">
          {t.actualResolution}: {resolutionLabel(actualWidth, actualHeight)}
        </p>
      ) : null}
    </div>
  )
}

function ExternalReference({
  reference,
  onZoom
}: {
  reference: DesktopVideoPromptReference
  onZoom: (url: string) => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const media = useResolvedMediaSrc({ type: 'image', url: reference.url })
  const { copied, copy, error, reset } = useImageActions()
  const [saved, setSaved] = useState(false)
  const beginAsync = useAsyncGuard()

  const save = async (): Promise<void> => {
    if (media.status !== 'ready') {
      return
    }

    const isLive = beginAsync()
    reset()

    try {
      if ((await fileApi().saveImage({ defaultName: reference.label, url: media.src })) && isLive()) {
        setSaved(true)
      }
    } catch (failure) {
      if (isLive()) {
        notifyError(failure, t.saveReference)
      }
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-lg border border-line-hairline p-2">
      <div className="flex h-24 shrink-0 items-center justify-center overflow-hidden rounded bg-black/10">
        {media.status === 'ready' ? (
          <button
            aria-label={reference.label}
            className="h-full w-full cursor-zoom-in"
            onClick={() => onZoom(media.src)}
            type="button"
          >
            <img alt={reference.label} className="h-full w-full object-contain" src={media.src} />
          </button>
        ) : (
          <span className="text-xs text-muted">{reference.label}</span>
        )}
      </div>
      <span className="truncate text-xs text-body" title={reference.label}>
        {reference.label}
      </span>
      <div className="flex flex-wrap gap-1">
        <button
          aria-label={`${t.copyReference} ${reference.label}`}
          className={BTN_GHOST}
          disabled={media.status !== 'ready'}
          onClick={() => {
            if (media.status === 'ready') {
              void copy(media.src)
            }
          }}
          type="button"
        >
          {copied ? t.copied : t.copyReference}
        </button>
        <button
          aria-label={`${t.saveReference} ${reference.label}`}
          className={BTN_GHOST}
          disabled={media.status !== 'ready'}
          onClick={() => void save()}
          type="button"
        >
          {saved ? t.referenceSaved : t.saveReference}
        </button>
      </div>
      {error === 'copy' && (
        <p className="text-xs text-rose-500" role="alert">
          {t.copyReferenceFailed}
        </p>
      )}
    </div>
  )
}

function localFileName(filePath: string): string {
  return filePath.split(/[\\/]/).pop() || filePath
}

function VideoRefinementModal({
  action,
  set,
  source,
  onClose
}: {
  action: DesktopVideoAction
  set: DesktopVideoSet
  source: DesktopVideoRefinementSource
  onClose: () => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const active = usePanelActivity()
  const sets = useStore($desktopVideoSets)
  const [feedback, setFeedback] = useState('')
  const [fitAspect, setFitAspect] = useState(false)
  const [previewAttempt, setPreviewAttempt] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const submittingRequest = useRef(false)

  const [checked, setChecked] = useState<{
    key: string
    result?: DesktopVideoRefinementPreview
    error?: string
  } | null>(null)

  const beginAsync = useAsyncGuard()
  const liveSet = sets.find(item => item.id === set.id)
  const liveAction = liveSet?.actions.find(item => item.id === action.id)
  const sourceUrl = source === 'candidate' ? action.draft?.candidate_video_url : action.current?.video_url

  const liveSourceUrl = source === 'candidate' ? liveAction?.draft?.candidate_video_url : liveAction?.current?.video_url

  const stale =
    !liveSet ||
    !liveAction ||
    liveSet.base_version !== set.base_version ||
    liveSet.resolution_revision !== set.resolution_revision ||
    liveAction.version !== action.version ||
    liveAction.draft?.id !== action.draft?.id ||
    liveAction.status === 'processing' ||
    liveSourceUrl?.split(/[?#]/)[0] !== sourceUrl?.split(/[?#]/)[0]

  const inputKey = JSON.stringify([
    set.id,
    set.base_version,
    set.resolution_revision,
    action.id,
    action.version,
    action.draft?.id,
    source,
    sourceUrl?.split(/[?#]/)[0],
    feedback,
    fitAspect,
    previewAttempt
  ])

  const preview = checked?.key === inputKey ? checked.result : undefined
  const previewError = checked?.key === inputKey ? checked.error : undefined

  useEffect(() => {
    if (!active || stale) {
      return
    }

    const scope = captureAuthScope()
    const isLive = beginAsync()
    let current = true

    const timer = window.setTimeout(() => {
      void previewDesktopVideoRefinement(action, set, source, feedback, fitAspect ? 'expand' : 'keep')
        .then(result => {
          if (current && scope?.() && isLive()) {
            setChecked({ key: inputKey, result })
          }
        })
        .catch((error: unknown) => {
          if (current && scope?.() && isLive()) {
            setChecked({ key: inputKey, error: backendDetailMessage(error, errorMessage(error)) })
          }
        })
    }, 300)

    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [action, active, beginAsync, feedback, fitAspect, inputKey, set, source, stale])

  const submit = async (): Promise<void> => {
    if (submittingRequest.current || !active || stale || !preview || (!feedback.trim() && !fitAspect)) {
      return
    }

    const scope = captureAuthScope()
    const isLive = beginAsync()
    submittingRequest.current = true
    setSubmitting(true)

    try {
      await refineDesktopVideo(action, set, source, feedback, fitAspect ? 'expand' : 'keep')

      if (scope?.() && isLive()) {
        onClose()
      }
    } catch (failure) {
      if (scope?.() && isLive()) {
        notifyError(failure, t.refineVideo)
        void refreshDesktopVideos()
      }
    } finally {
      submittingRequest.current = false

      if (scope?.() && isLive()) {
        setSubmitting(false)
      }
    }
  }

  return (
    <WizardModal
      backdropClose={!submitting}
      closeDisabled={submitting}
      escClose={!submitting}
      onClose={submitting ? () => undefined : onClose}
      regionId="desktop-life-refine-video"
      title={t.refineVideo}
      widthClass="max-w-2xl"
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted">{source === 'candidate' ? t.refinementCandidate : t.refinementCurrent}</p>
        <DesktopLifePreview action={action} draft={source === 'candidate'} set={set} />
        <p className="text-sm leading-relaxed text-body">{t.refineVideoHint}</p>
        <label className="flex flex-col gap-2 text-sm text-body">
          {t.refineVideoFeedback}
          <textarea
            className={`${INPUT_CLASS} min-h-24 resize-y`}
            disabled={submitting}
            maxLength={600}
            onChange={event => setFeedback(event.target.value)}
            placeholder={t.refineVideoExample}
            rows={3}
            value={feedback}
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-body">
          <input
            checked={fitAspect}
            disabled={submitting}
            onChange={event => setFitAspect(event.target.checked)}
            type="checkbox"
          />
          {t.fitVideoAspect}
        </label>
        {stale ? (
          <p className="text-sm text-warning" role="alert">
            {t.refinementStale}
          </p>
        ) : previewError ? (
          <div className="flex flex-col items-start gap-2">
            <p className="text-sm text-warning" role="alert">
              {previewError}
            </p>
            <button className={BTN_SUBTLE} onClick={() => setPreviewAttempt(attempt => attempt + 1)} type="button">
              {t.retryPrompt}
            </button>
          </div>
        ) : preview ? (
          <p className="text-sm text-body" role="status">
            {t.refinementDuration(preview.duration_seconds)}
          </p>
        ) : (
          <p className="text-sm text-muted" role="status">
            {t.refinementPreviewLoading}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            className={BTN_PRIMARY}
            disabled={submitting || !active || stale || !preview || (!feedback.trim() && !fitAspect)}
            onClick={() => void submit()}
            type="button"
          >
            {submitting ? t.generatingVideo : t.startRefinement}
          </button>
          <button className={BTN_GHOST} disabled={submitting} onClick={onClose} type="button">
            {t.cancelUpload}
          </button>
        </div>
      </div>
    </WizardModal>
  )
}

export function DesktopLifePage({
  onPrepareMaterial
}: {
  onPrepareMaterial?: (kind: 'scene' | 'outfit') => void
}): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const queueStrings = useStrings().common.videoQueue
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
  const [preferencesSaving, setPreferencesSaving] = useState(false)
  const [aspectChoice, setAspectChoice] = useState<DesktopVideoAspectMismatch | null>(null)
  const resolveAspectChoice = useRef<((policy: DesktopVideoAspectPolicy | null) => void) | null>(null)
  const aspectGeneration = useRef(0)
  const [imagePreviews, setImagePreviews] = useState<Record<string, boolean>>({})
  const scenes = useStore($sceneLibrary)
  const activeScene = useStore($activeScene)
  const scenePage = useStore($scenePage)
  const sceneTotal = useStore($sceneTotal)
  const outfits = useStore($outfits)
  const [sceneQuery, setSceneQuery] = useState('')
  const [creationScene, setCreationScene] = useState<{ id: string; scene: SceneAsset | null } | null>(null)
  const [rename, setRename] = useState('')
  const [deleteOpen, setDeleteOpen] = useState(false)
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
  const externalUploadRequest = useRef(false)
  const [externalCopied, setExternalCopied] = useState(false)
  const [externalReferenceZoom, setExternalReferenceZoom] = useState<{ label: string; url: string } | null>(null)
  const externalRequest = useRef(0)
  const externalAuthSession = useRef(authSessionId)
  const beginAsync = useAsyncGuard()
  const selected = sets.find(item => item.id === selectedSetId)
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
      item.actions.some(item => item.status === 'processing' && !isActionPaused(item)) ||
      item.proposals.some(proposal => proposal.status === 'pending')
  )

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
    setDeleteOpen(false)
    setDiscardAction(null)
    setRefinement(null)
    setBusy(false)
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

    if ((!previous.sceneId && scene) || (!previous.outfitId && outfit)) {
      updateDesktopLifeUi({
        create: {
          ...previous,
          sceneId: previous.sceneId || scene?.id || '',
          sceneLabel: previous.sceneId ? previous.sceneLabel : scene?.title || '',
          outfitId: previous.outfitId || (outfit ? String(outfit.id) : '')
        }
      })
    }
  }, [createOpen, activeScene, outfits, scenes])

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

  const run = async (operation: () => Promise<unknown>): Promise<void> => {
    if (busy) {
      return
    }

    const isLive = beginAsync()
    setBusy(true)

    try {
      await operation()
    } catch (failure) {
      if (isLive()) {
        notifyError(failure, t.title)
        void refreshDesktopVideos()
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

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

  const makeVideo = async (item: DesktopVideoAction, set: DesktopVideoSet, note = ''): Promise<void> => {
    const scope = captureAuthScope()
    const isLive = beginAsync()
    const generation = aspectGeneration.current
    await withAspectChoice(async policy => {
      const generated = await generateDesktopVideoAction(item, set, note, policy)
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

  const play = async (item: DesktopVideoAction): Promise<void> => {
    if ($presentation.get().effectiveMode !== 'desktop') {
      await shellApi().presentation.setMode('desktop')
    }

    await playDesktopVideoAction(item.id, item.set_id)
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

  const actionStatus = (item: DesktopVideoAction): string => {
    const queue = item.draft?.video_queue ?? item.video_queue

    if (queue) {
      return queueStrings[queue.state]
    }

    if (item.draft?.submission_unknown) {
      return t.resultPending
    }

    if (isActionPaused(item)) {
      return t.paused
    }

    if (item.stage === 'image_review') {
      return t.stepImage
    }

    if (item.status === 'processing') {
      if (item.stage === 'image' || item.stage === 'image_upload') {
        return t.generatingImage
      }

      if (['process', 'download', 'upload'].includes(item.stage)) {
        return t.processingVideo
      }

      if (item.stage === 'video') {
        return t.generatingVideo
      }

      return t.preparing
    }

    if (item.status === 'failed') {
      return t.statuses.failed
    }

    if (item.status === 'review_pending' && item.draft?.candidate_video_url) {
      return t.videoReviewPending
    }

    if (item.playable) {
      return t.ready
    }

    return item.status === 'queued' ? t.notCreated : t.statuses[item.status]
  }

  const adoptCandidate = async (item: DesktopVideoAction): Promise<void> => {
    if (!selected) {
      return
    }

    const scope = captureAuthScope()
    await reviewDesktopVideoAction(item, selected, true)

    if (!scope?.()) {
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

    if (item.key !== 'idle') {
      await play(updated)
    }
  }

  const renderSteps = (step: number): React.JSX.Element => (
    <ol aria-label={t.createSet} className="grid grid-cols-4 gap-2 border-b border-line-hairline pb-4">
      {[t.stepIdea, t.stepImage, t.stepVideo, t.stepUse].map((label, index) => (
        <li
          aria-current={step === index ? 'step' : undefined}
          className={`flex flex-col gap-2 text-xs sm:flex-row sm:items-center ${index === step ? 'font-medium text-accent' : 'text-muted'}`}
          key={label}
        >
          <span
            className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${index === step ? 'bg-accent/15' : 'bg-black/5'}`}
          >
            {index + 1}
          </span>
          {label}
        </li>
      ))}
    </ol>
  )

  const renderAction = (item: DesktopVideoAction): React.JSX.Element => {
    if (!selected) {
      return <></>
    }

    const processing = item.status === 'processing' && !isActionPaused(item)
    const imageReview = Boolean(item.stage === 'image_review' && item.draft?.image_url && !item.draft.image_confirmed)

    const candidate = Boolean(
      item.status === 'review_pending' && item.stage !== 'image_review' && item.draft?.candidate_video_url
    )

    const previewDraft = Boolean(
      item.draft && (item.draft.candidate_video_url || !item.draft.video_refinement || !item.current)
    )

    const showingCurrentVideo = Boolean(item.current?.video_url && !previewDraft)

    const step =
      candidate || (!item.draft && item.current)
        ? 3
        : imageReview
          ? 1
          : item.draft
            ? item.draft.image_confirmed || item.draft.video_refinement || item.draft.source === 'external_upload'
              ? 2
              : 1
            : 0

    const dependent = item.key !== 'idle' && item.continuity === 'base'
    const requiresBase = dependent && !selected.base_image_url
    const note = feedback[item.id] ?? item.draft?.feedback ?? ''

    const affected = selected.actions.filter(
      action => action.id !== item.id && action.continuity === 'base' && (action.current || action.draft)
    ).length

    const canConfirm = item.draft && imagePreviews[`${item.draft.id}:${item.draft.image_url}`]

    return (
      <section className="flex flex-col gap-5" key={item.id}>
        {renderSteps(step)}
        <div>
          <h3 className="text-lg font-semibold text-strong">{actionName(item)}</h3>
          <p className="mt-1 text-sm text-muted" role="status">
            {actionStatus(item)}
          </p>
        </div>
        {requiresBase ? (
          <div className="rounded-xl bg-accent/5 p-5">
            <h4 className="font-medium text-strong">{t.imageRequired}</h4>
            <p className="my-3 text-sm leading-relaxed text-body">{t.imageRequiredHint}</p>
            <button className={BTN_PRIMARY} onClick={() => idle && openAction(idle)} type="button">
              {t.openImage}
            </button>
          </div>
        ) : (
          <>
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
            {item.current && item.draft && <p className="text-sm text-muted">{t.savedCurrent}</p>}
            {!candidate && (item.error || item.draft?.error) && (
              <div className="rounded-xl bg-warning/10 p-4 text-sm text-body" role="status">
                <p>{item.status === 'failed' ? t.creationFailed : t.unavailable}</p>
                <details className="mt-2 text-xs text-muted">
                  <summary className="cursor-pointer">{t.details}</summary>
                  <p className="mt-2 break-words">{item.draft?.error || item.error}</p>
                </details>
              </div>
            )}
            {item.needs_update && <p className="text-sm text-warning">{t.needsUpdate}</p>}
            {processing ? (
              <div className="flex flex-col gap-3 rounded-xl bg-accent/5 p-4" role="status">
                <p className="text-sm font-medium text-strong">{actionStatus(item)}</p>
                <p className="text-sm text-muted">{t.leaveHint}</p>
                <p className="text-xs text-muted">{t.stopHint}</p>
                {item.draft && !['upload', 'image_upload'].includes(item.stage) && (
                  <button
                    className={`${BTN_SUBTLE} self-start`}
                    disabled={busy}
                    onClick={() => void run(() => pauseDesktopVideoDraft(item))}
                    type="button"
                  >
                    {t.cancel}
                  </button>
                )}
              </div>
            ) : item.draft?.submission_unknown ? (
              <div className="rounded-xl bg-black/5 p-4">
                <p className="text-sm font-medium text-strong">{t.resultPending}</p>
                <p className="mt-2 text-sm leading-relaxed text-muted">{t.resultPendingHint}</p>
                <button
                  className={`${BTN_SUBTLE} mt-3`}
                  disabled={loading || busy}
                  onClick={() => void refreshDesktopVideos()}
                  type="button"
                >
                  {t.refresh}
                </button>
              </div>
            ) : candidate ? (
              <>
                <div className="rounded-xl bg-accent/5 p-4 text-sm text-body" role="status">
                  <p>{t.videoReviewHint}</p>
                  {item.draft?.review_reason && (
                    <details className="mt-2 text-xs text-muted">
                      <summary className="cursor-pointer">{t.reviewDetails}</summary>
                      <p className="mt-2 break-words">{item.draft.review_reason}</p>
                    </details>
                  )}
                </div>
                {item.key === 'idle' && affected > 0 && (
                  <p className="rounded-lg bg-warning/10 p-3 text-sm text-warning">{t.updateImpact(affected)}</p>
                )}
                <div className="sticky bottom-0 flex flex-wrap gap-2 border-t border-line-hairline bg-surface-card py-4">
                  <button
                    className={BTN_PRIMARY}
                    disabled={busy}
                    onClick={() => void run(() => adoptCandidate(item))}
                    type="button"
                  >
                    {item.key === 'idle' ? t.play : t.useCandidate}
                  </button>
                  <button className={BTN_SUBTLE} disabled={busy} onClick={() => setDiscardAction(item)} type="button">
                    {t.reject}
                  </button>
                  <button
                    className={BTN_SUBTLE}
                    disabled={busy}
                    onClick={() => setRefinement({ action: item, set: selected, source: 'candidate' })}
                    type="button"
                  >
                    {t.refineVideo}
                  </button>
                </div>
              </>
            ) : imageReview ? (
              <>
                <p className="text-sm text-body">{t.imageReview}</p>
                <details className="rounded-xl border border-line-hairline p-4">
                  <summary className="cursor-pointer text-sm font-medium text-strong">{t.adjustImage}</summary>
                  <label className="mt-3 flex flex-col gap-2 text-sm text-body">
                    {t.adjustHint}
                    <textarea
                      className={`${INPUT_CLASS} min-h-24 resize-y`}
                      maxLength={600}
                      onChange={event => setFeedback(current => ({ ...current, [item.id]: event.target.value }))}
                      rows={3}
                      value={note}
                    />
                  </label>
                  <button
                    className={`${BTN_SUBTLE} mt-3`}
                    disabled={busy || !note.trim()}
                    onClick={() => void run(() => makeImage(item, selected, note))}
                    type="button"
                  >
                    {t.adjustImage}
                  </button>
                </details>
                <div className="sticky bottom-0 flex flex-wrap gap-2 border-t border-line-hairline bg-surface-card py-4">
                  <button
                    className={BTN_PRIMARY}
                    disabled={busy || !canConfirm}
                    onClick={() => void run(() => confirmDesktopVideoImage(item, selected))}
                    type="button"
                  >
                    {t.confirmImage}
                  </button>
                </div>
              </>
            ) : item.current && !item.draft && !item.needs_update ? (
              <div className="flex flex-col gap-3">
                <p className="text-sm text-muted">{t.readyHint}</p>
                <button
                  className={`${BTN_PRIMARY} self-start`}
                  disabled={busy || !item.playable}
                  onClick={() =>
                    void run(async () => {
                      if (!currentMatches) {
                        const activated = await activateDesktopVideoSet(selected)

                        if (!activated) {
                          return
                        }
                      }

                      if (item.key !== 'idle') {
                        await play(item)
                      } else if ($presentation.get().effectiveMode !== 'desktop') {
                        await shellApi().presentation.setMode('desktop')
                      }
                    })
                  }
                  type="button"
                >
                  {currentMatches && item.key === 'idle' ? t.current : item.key === 'idle' ? t.play : t.playAction}
                </button>
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {isActionPaused(item) && <p className="text-sm text-muted">{t.resumeHint}</p>}
                {!isActionPaused(item) && !item.draft?.video_refinement && (
                  <label className="flex flex-col gap-2 text-sm text-body">
                    {t.feedback}
                    <textarea
                      className={`${INPUT_CLASS} min-h-24 resize-y`}
                      maxLength={600}
                      onChange={event => setFeedback(current => ({ ...current, [item.id]: event.target.value }))}
                      rows={3}
                      value={note}
                    />
                  </label>
                )}
                <button
                  className={`${BTN_PRIMARY} self-start`}
                  disabled={busy}
                  onClick={() =>
                    void run(() => {
                      if (
                        isActionPaused(item) ||
                        item.draft?.video_refinement ||
                        item.draft?.source === 'external_upload'
                      ) {
                        return resumeDesktopVideoDraft(item, selected)
                      }

                      if (!dependent && !item.draft?.image_confirmed) {
                        return makeImage(item, selected, isActionPaused(item) ? '' : note)
                      }

                      return makeVideo(item, selected, isActionPaused(item) ? '' : note)
                    })
                  }
                  type="button"
                >
                  {isActionPaused(item)
                    ? t.resume
                    : item.status === 'failed'
                      ? t.retry
                      : dependent || item.draft?.image_confirmed
                        ? t.startAction
                        : t.generateImage}
                </button>
              </div>
            )}
            {!processing && !candidate && (
              <details className="rounded-xl border border-line-hairline p-4">
                <summary className="cursor-pointer text-sm font-medium text-strong">{t.more}</summary>
                <div className="mt-4 flex flex-col gap-3">
                  {showingCurrentVideo && (
                    <button
                      className={`${BTN_SUBTLE} self-start`}
                      disabled={busy || item.status === 'processing' || item.draft?.submission_unknown}
                      onClick={() => setRefinement({ action: item, set: selected, source: 'current' })}
                      type="button"
                    >
                      {t.refineVideo}
                    </button>
                  )}
                  {item.current && !dependent && (
                    <>
                      <p className="text-sm text-muted">{t.recreateHint}</p>
                      <textarea
                        aria-label={t.feedback}
                        className={`${INPUT_CLASS} min-h-20 resize-y`}
                        maxLength={600}
                        onChange={event => setFeedback(current => ({ ...current, [item.id]: event.target.value }))}
                        rows={2}
                        value={note}
                      />
                      <button
                        className={`${BTN_SUBTLE} self-start`}
                        disabled={busy || item.draft?.submission_unknown}
                        onClick={() => void run(() => makeImage(item, selected, note))}
                        type="button"
                      >
                        {t.adjustImage}
                      </button>
                    </>
                  )}
                  {(item.current || item.draft?.image_confirmed) && !item.draft?.video_refinement && (
                    <button
                      className={`${BTN_SUBTLE} self-start`}
                      disabled={busy || item.draft?.submission_unknown}
                      onClick={() => void run(() => makeVideo(item, selected, note))}
                      type="button"
                    >
                      {t.remakeVideo}
                    </button>
                  )}
                  {!dependent && (
                    <button
                      className={`${BTN_SUBTLE} self-start`}
                      disabled={busy || item.draft?.submission_unknown}
                      onClick={() => void run(() => makeImage(item, selected, note, true))}
                      type="button"
                    >
                      {t.regenerateImage}
                    </button>
                  )}
                  {!dependent && (
                    <button
                      className={`${BTN_SUBTLE} self-start`}
                      disabled={busy}
                      onClick={() => void run(() => uploadImage(item))}
                      type="button"
                    >
                      {t.uploadImage}
                    </button>
                  )}
                  <button
                    className={`${BTN_SUBTLE} self-start`}
                    disabled={busy || (imageReview && !canConfirm)}
                    onClick={() =>
                      void run(() => (imageReview ? confirmForExternal(item) : Promise.resolve(openExternal(item))))
                    }
                    type="button"
                  >
                    {imageReview ? t.confirmOnly : t.externalTitle}
                  </button>
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
    )
  }

  const selectedScene =
    creationScene?.id === create.sceneId
      ? creationScene.scene
      : (scenes.find(scene => scene.id === create.sceneId) ?? (activeScene?.id === create.sceneId ? activeScene : null))

  const selectedOutfit = outfits.find(outfit => String(outfit.id) === create.outfitId)
  const materialReady = selectedScene?.status === 'ready' && selectedOutfit?.status === 'ready'
  const recommendationDisplay = resolveDesktopLifeRecommendationDisplay(presentation)
  const recommendationResolution = desktopDisplayResolution(recommendationDisplay)

  const resolutionReady = readyDesktopLifeResolution(create, presentation) !== null

  const displayedActions =
    selected?.actions.filter(item => item.key !== 'idle' && (item.current || item.draft || item.status === 'failed')) ??
    []

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 sm:p-6"
      onScroll={event => {
        if (selectedSetId === null && !createOpen) {
          updateDesktopLifeUi({ libraryScrollTop: event.currentTarget.scrollTop })
        }
      }}
      ref={scrollRef}
    >
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
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
            {selectedActionId !== null && !createOpen ? t.returnToScene : t.back}
          </button>
        )}
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-strong">
              {createOpen ? t.createSet : selected?.title || t.title}
            </h2>
            <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted">
              {createOpen ? t.createHint : !selected ? t.hint : t.savedCurrent}
            </p>
          </div>
          {!selected && !createOpen && (
            <button className={BTN_PRIMARY} disabled={busy} onClick={openCreate} type="button">
              {t.createSet}
            </button>
          )}
        </header>
        {!createOpen && state && (
          <section aria-label={t.preferences} className="space-y-4 rounded-xl border border-line-hairline p-4">
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
          </section>
        )}
        {!createOpen && (
          <p className="text-sm text-muted">
            {recommendationResolution && recommendationDisplay
              ? `${t.recommendationDisplay}: ${recommendationDisplay.label} · ${resolutionLabel(recommendationResolution.width, recommendationResolution.height)}`
              : t.recommendationUnavailable}
          </p>
        )}
        {selected && !createOpen && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted">
              {t.targetResolution}: {resolutionLabel(selected.target_width, selected.target_height)}
            </span>
            <DesktopLifeRecommendation display={recommendationDisplay} set={selected} />
          </div>
        )}
        {(error || state?.preparation_error) && (
          <div className="rounded-xl bg-warning/10 p-4 text-sm text-body" role="status">
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
              className="flex flex-col gap-5"
              onSubmit={event => {
                event.preventDefault()
                const currentCreate = $desktopLifeUi.get().create
                const target = readyDesktopLifeResolution(currentCreate, $presentation.get())

                if (busy || !target) {
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
              {create.source === 'ai' ? (
                <label className="flex flex-col gap-3 text-sm font-medium text-strong">
                  {t.idea}
                  <textarea
                    autoFocus
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
                <div className="rounded-xl bg-accent/5 p-4 text-sm text-body">{t.ownMediaHint}</div>
              )}
              <DesktopLifeDisplayPicker
                disabled={busy}
                displayId={create.targetDisplayId}
                displays={presentation.displays}
                onChange={targetDisplayId => {
                  setCreate(current => reconcileDesktopLifeDisplay(current, $presentation.get(), targetDisplayId))
                }}
              />
              <section className="flex flex-col gap-3 rounded-xl bg-black/5 p-4">
                <p className="text-sm text-body">{t.referencesHint}</p>
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex min-w-0 flex-col gap-2">
                    <DesktopLifeCover url={selectedScene?.assetUrl || null} />
                    <span className="truncate text-sm text-body">
                      {selectedScene?.title || create.sceneLabel || t.sceneMissing}
                    </span>
                  </div>
                  <div className="flex min-w-0 flex-col gap-2">
                    <DesktopLifeCover url={selectedOutfit?.fullbodyPath || null} />
                    <span className="truncate text-sm text-body">{selectedOutfit?.name || t.outfitMissing}</span>
                  </div>
                </div>
                {selectedScene?.status !== 'ready' && onPrepareMaterial && (
                  <button
                    className={`${BTN_SUBTLE} self-start`}
                    onClick={() => onPrepareMaterial('scene')}
                    type="button"
                  >
                    {t.prepareScene}
                  </button>
                )}
                {selectedOutfit?.status !== 'ready' && onPrepareMaterial && (
                  <button
                    className={`${BTN_SUBTLE} self-start`}
                    onClick={() => onPrepareMaterial('outfit')}
                    type="button"
                  >
                    {t.prepareOutfit}
                  </button>
                )}
                {!materialReady && <p className="text-xs text-muted">{t.continueMaterial}</p>}
                <details>
                  <summary className="cursor-pointer text-sm text-accent">{t.changeReferences}</summary>
                  <div className="mt-4 flex flex-col gap-4">
                    <label className="flex flex-col gap-2 text-sm text-body">
                      {t.sceneLibrary}
                      <div className="flex gap-2">
                        <input
                          aria-label={t.searchScenes}
                          className={INPUT_CLASS}
                          onChange={event => setSceneQuery(event.target.value)}
                          placeholder={t.searchScenes}
                          value={sceneQuery}
                        />
                        <button
                          className={BTN_SUBTLE}
                          onClick={() => void loadSceneLibrary(sceneQuery, 0)}
                          type="button"
                        >
                          {t.search}
                        </button>
                      </div>
                      <PanelSelect
                        ariaLabel={t.sceneLibrary}
                        onChange={value =>
                          setCreate(current => ({
                            ...current,
                            sceneId: value,
                            sceneLabel: scenes.find(scene => scene.id === value)?.title || '',
                            requestId: crypto.randomUUID()
                          }))
                        }
                        options={[
                          { value: '', label: t.chooseScene },
                          ...(create.sceneId && !scenes.some(scene => scene.id === create.sceneId)
                            ? [
                                {
                                  value: create.sceneId,
                                  label: selectedScene?.title || create.sceneLabel || t.sceneMissing
                                }
                              ]
                            : []),
                          ...scenes
                            .filter(scene => scene.status === 'ready')
                            .map(scene => ({ value: scene.id, label: scene.title }))
                        ]}
                        value={create.sceneId}
                      />
                      <div className="flex gap-2">
                        <button
                          className={BTN_GHOST}
                          disabled={scenePage === 0}
                          onClick={() => void loadSceneLibrary(sceneQuery, scenePage - 1)}
                          type="button"
                        >
                          {t.previous}
                        </button>
                        <button
                          className={BTN_GHOST}
                          disabled={(scenePage + 1) * PAGE_SIZE >= sceneTotal}
                          onClick={() => void loadSceneLibrary(sceneQuery, scenePage + 1)}
                          type="button"
                        >
                          {t.next}
                        </button>
                      </div>
                    </label>
                    <label className="flex flex-col gap-2 text-sm text-body">
                      {t.outfitLibrary}
                      <PanelSelect
                        ariaLabel={t.outfitLibrary}
                        onChange={value =>
                          setCreate(current => ({ ...current, outfitId: value, requestId: crypto.randomUUID() }))
                        }
                        options={[
                          { value: '', label: t.chooseOutfit },
                          ...outfits
                            .filter(outfit => outfit.status === 'ready')
                            .map(outfit => ({ value: String(outfit.id), label: outfit.name }))
                        ]}
                        value={create.outfitId}
                      />
                    </label>
                    {onPrepareMaterial && (
                      <div className="flex flex-wrap gap-2">
                        <button className={BTN_GHOST} onClick={() => onPrepareMaterial('scene')} type="button">
                          {t.prepareScene}
                        </button>
                        <button className={BTN_GHOST} onClick={() => onPrepareMaterial('outfit')} type="button">
                          {t.prepareOutfit}
                        </button>
                      </div>
                    )}
                  </div>
                </details>
              </section>
              <details>
                <summary className="cursor-pointer text-sm text-muted">{t.advanced}</summary>
                <label className="mt-3 flex flex-col gap-2 text-sm text-body">
                  {t.nameOptional}
                  <input
                    className={INPUT_CLASS}
                    maxLength={160}
                    onChange={event => setCreate(current => ({ ...current, name: event.target.value }))}
                    placeholder={t.newSceneName}
                    value={create.name}
                  />
                </label>
              </details>
              <div className="sticky bottom-0 flex flex-wrap items-center gap-3 border-t border-line-hairline bg-surface-card py-4">
                <button className={BTN_PRIMARY} disabled={busy || !materialReady || !resolutionReady} type="submit">
                  {busy
                    ? t.preparing
                    : create.source === 'ai'
                      ? t.generateImage
                      : create.source === 'image_upload'
                        ? t.uploadImage
                        : t.selectVideo}
                </button>
                <details>
                  <summary className="cursor-pointer text-sm text-muted">
                    {create.source === 'ai' ? t.ownMedia : t.creationSource}
                  </summary>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {(['ai', 'image_upload', 'video_upload'] as DesktopVideoSource[]).map(source => (
                      <button
                        className={BTN_GHOST}
                        key={source}
                        onClick={() => setCreate(current => ({ ...current, source, requestId: crypto.randomUUID() }))}
                        type="button"
                      >
                        {source === 'ai' ? t.useAI : source === 'image_upload' ? t.uploadImage : t.externalTitle}
                      </button>
                    ))}
                  </div>
                </details>
              </div>
            </form>
          </>
        ) : action ? (
          renderAction(action)
        ) : selected ? (
          <>
            {idle && (
              <section className="flex flex-col gap-4">
                {idle.current ? (
                  <DesktopLifePreview action={idle} set={selected} />
                ) : (
                  <DesktopLifeCover target={selected} url={idle.draft?.image_url || selected.base_image_url} />
                )}
                <div className="flex flex-wrap items-center gap-3">
                  {idle.playable && (
                    <button
                      className={BTN_PRIMARY}
                      disabled={busy || currentMatches}
                      onClick={() => void run(activate)}
                      type="button"
                    >
                      {currentMatches ? t.current : t.play}
                    </button>
                  )}
                  {(idle.draft || !idle.playable || idle.needs_update) && (
                    <button
                      className={idle.playable ? BTN_SUBTLE : BTN_PRIMARY}
                      disabled={busy}
                      onClick={() => openAction(idle)}
                      type="button"
                    >
                      {t.continue} · {actionStatus(idle)}
                    </button>
                  )}
                  {idle.current && !idle.draft && (
                    <button className={BTN_SUBTLE} onClick={() => openAction(idle)} type="button">
                      {t.adjustImage}
                    </button>
                  )}
                </div>
                {!idle.playable && <p className="text-sm text-muted">{t.noActionsHint}</p>}
              </section>
            )}
            <section className="flex flex-col gap-4 border-t border-line-hairline pt-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-base font-medium text-strong">{t.actions}</h3>
                <button
                  className={BTN_SUBTLE}
                  onClick={() => updateDesktopLifeUi({ addActionOpen: true })}
                  type="button"
                >
                  {t.design}
                </button>
              </div>
              {displayedActions.length > 0 && (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {displayedActions.map(item => (
                    <button
                      className="flex flex-col gap-2 rounded-xl border border-line-hairline p-3 text-left hover:bg-black/5"
                      key={item.id}
                      onClick={() => openAction(item)}
                      type="button"
                    >
                      <DesktopLifeCover
                        target={selected}
                        url={
                          item.current?.poster_url || item.draft?.candidate_poster_url || item.draft?.image_url || null
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
            <details className="rounded-xl border border-line-hairline p-4">
              <summary className="cursor-pointer text-sm text-body">{t.more}</summary>
              <div className="mt-4 flex flex-col gap-4">
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
                    className={`${INPUT_CLASS} flex-1`}
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
                  onClick={() => setDeleteOpen(true)}
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
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {sets.map(item => {
                const idle = item.actions.find(action => action.key === 'idle')

                const unfinished = item.actions.some(
                  action => action.draft || (action.key === 'idle' && !action.current)
                )

                return (
                  <article
                    className="flex min-w-0 flex-col gap-3 rounded-2xl border border-line-hairline p-4"
                    key={item.id}
                  >
                    <button
                      className="flex w-full min-w-0 flex-col gap-3 text-left"
                      onClick={() => setSelectedSetId(item.id)}
                      type="button"
                    >
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
                        <strong className="text-base text-strong">{item.title}</strong>
                        {item.is_current && (
                          <span className="rounded-full bg-accent/10 px-2 py-1 text-xs text-accent">{t.current}</span>
                        )}
                      </div>
                      <span className="text-sm text-muted">
                        {unfinished ? t.continue : idle?.playable ? t.ready : t.notCreated}
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
                        if (idle?.playable && !item.is_current) {
                          void run(() => activate(item))
                        } else {
                          setSelectedSetId(item.id)
                        }
                      }}
                      type="button"
                    >
                      {idle?.playable && !item.is_current
                        ? t.play
                        : item.is_current && !unfinished
                          ? t.current
                          : t.continue}
                    </button>
                  </article>
                )
              })}
            </div>
            <div className="flex flex-col gap-4 text-sm text-body">
              <button
                className={`${BTN_GHOST} self-start`}
                disabled={loading || busy}
                onClick={() => void refreshDesktopVideos()}
                type="button"
              >
                {t.refresh}
              </button>
            </div>
          </>
        )}
      </div>
      {addActionOpen && selected && (
        <WizardModal
          onClose={() => updateDesktopLifeUi({ addActionOpen: false })}
          regionId="desktop-life-add-action"
          title={t.design}
          widthClass="max-w-2xl"
        >
          <p className="mb-4 text-sm leading-relaxed text-muted">{t.addHint}</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {selected.actions
              .filter(item => item.key !== 'idle')
              .map(item => (
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
          <section className="mt-5 border-t border-line-hairline pt-4">
            <h3 className="text-sm font-medium text-strong">{t.addCustom}</h3>
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
                      onChange={event =>
                        setDesign(current => ({ ...current, duration: Number(event.target.value) || 0 }))
                      }
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
            </form>
          </section>
        </WizardModal>
      )}
      {discardAction && (
        <WizardModal
          onClose={() => setDiscardAction(null)}
          regionId="desktop-life-discard"
          title={t.discardDraft}
          widthClass="max-w-md"
        >
          <p className="mb-4 text-sm leading-relaxed text-body">{t.discardHint}</p>
          <div className="flex gap-2">
            <button className={BTN_SUBTLE} disabled={busy} onClick={() => setDiscardAction(null)} type="button">
              {t.close}
            </button>
            <button
              className={BTN_PRIMARY}
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await cancelDesktopVideoDraft(discardAction)
                  setDiscardAction(null)
                })
              }
              type="button"
            >
              {t.discardConfirm}
            </button>
          </div>
        </WizardModal>
      )}
      {deleteOpen && selected && (
        <WizardModal
          onClose={() => setDeleteOpen(false)}
          regionId="desktop-life-delete"
          title={t.deleteSet}
          widthClass="max-w-md"
        >
          <p className="mb-4 text-sm leading-relaxed text-body">{t.deleteHint}</p>
          <div className="flex gap-2">
            <button className={BTN_SUBTLE} disabled={busy} onClick={() => setDeleteOpen(false)} type="button">
              {t.close}
            </button>
            <button
              className={BTN_PRIMARY}
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await deleteDesktopVideoSet(selected.id)
                  setSelectedSetId(null)
                  setDeleteOpen(false)
                })
              }
              type="button"
            >
              {t.deleteSet}
            </button>
          </div>
        </WizardModal>
      )}
      {externalAction && (
        <WizardModal
          backdropClose={!externalUploading}
          closeDisabled={externalUploading}
          escClose={!externalUploading && !externalReferenceZoom && !aspectChoice}
          onClose={externalUploading ? () => undefined : closeExternal}
          regionId="desktop-life-external"
          title={t.externalTitle}
          widthClass="max-w-2xl"
        >
          <div className="flex flex-col gap-5">
            <p className="text-sm leading-relaxed text-body">{t.uploadDirect}</p>
            {externalStale && (
              <p className="text-sm text-warning" role="status">
                {t.stalePrompt}
              </p>
            )}
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
              <button
                className={`${BTN_PRIMARY} self-start`}
                disabled={!externalFilePath || externalUploading || externalPromptLoading || externalStale}
                onClick={() => void uploadExternalVideo()}
                type="button"
              >
                {externalUploading ? t.uploading : t.upload}
              </button>
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
