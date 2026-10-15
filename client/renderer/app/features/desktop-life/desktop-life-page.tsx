import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

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
  type DesktopVideoPromptReference,
  type DesktopVideoSource,
  ensureDesktopVideoCurrent,
  generateDesktopVideoAction,
  generateDesktopVideoImage,
  getDesktopVideoPrompt,
  playDesktopVideoAction,
  refreshDesktopVideos,
  renameDesktopVideoSet,
  reviewDesktopVideoAction,
  setDesktopVideoPreferences,
  uploadDesktopVideoAction
} from '@/modules/desktop-videos'
import { useResolvedMediaSrc } from '@/modules/media'
import { $sceneLibrary, $scenePage, $sceneTotal, loadSceneLibrary, PAGE_SIZE } from '@/modules/scene'
import { PortraitLightbox } from '@/shared'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useImageActions } from '@/shared/hooks/use-image-actions'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { ArrowLeft } from '@/shared/lib/icons'
import { BTN_GHOST, BTN_PRIMARY, BTN_SUBTLE, INPUT_CLASS, PanelSelect, WizardModal } from '@/shared/panel'
import { fileApi, shellApi } from '@/shared/platform/desktop'
import { $auth } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'

import { useDesktopLifeStrings } from './desktop-life-strings'

function isActionPaused(action: DesktopVideoAction): boolean {
  return action.status === 'processing' && action.stage === 'paused'
}

function DesktopLifeCover({ url }: { url: string | null }): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const media = useResolvedMediaSrc({ type: 'image', url: url ?? '' })

  return media.status === 'ready' ? (
    <img alt="" className="aspect-video w-full rounded-lg object-cover" src={media.src} />
  ) : (
    <div className="flex aspect-video w-full items-center justify-center rounded-lg bg-black/10 text-xs text-muted">
      {t.noPreview}
    </div>
  )
}

function DesktopLifePreview({
  action,
  draft = false,
  onImageReady
}: {
  action: DesktopVideoAction
  draft?: boolean
  onImageReady?: () => void
}): React.JSX.Element {
  const active = usePanelActivity()
  const t = useDesktopLifeStrings()
  const videoUrl = draft ? action.draft?.candidate_video_url : action.current?.video_url
  const posterUrl = draft ? action.draft?.candidate_poster_url || action.draft?.image_url : action.current?.poster_url
  const url = videoUrl || posterUrl
  const type = videoUrl ? 'video' : 'image'
  const media = useResolvedMediaSrc({ type, url: url ?? '' })
  const video = useRef<HTMLVideoElement>(null)
  const src = media.status === 'ready' ? media.src : undefined

  useEffect(() => {
    if (!active) {
      video.current?.pause()
    }
  }, [active])

  return (
    <div className="flex aspect-video w-full items-center justify-center overflow-hidden rounded-xl bg-black/20">
      {src ? (
        type === 'video' ? (
          <video
            className="h-full w-full object-contain"
            controls
            loop={action.kind === 'loop'}
            muted
            playsInline
            preload="metadata"
            ref={video}
            src={src}
          />
        ) : (
          <img alt="" className="h-full w-full object-contain" onLoad={onImageReady} src={src} />
        )
      ) : (
        <span className="text-sm text-muted">{t.noPreview}</span>
      )}
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

export function DesktopLifePage(): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const auth = useStore($auth)
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const state = useStore($desktopVideoState)
  const sets = useStore($desktopVideoSets)
  const loading = useStore($desktopVideoLoading)
  const error = useStore($desktopVideoError)
  const [selectedSetId, setSelectedSetId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [imagePreviews, setImagePreviews] = useState<Record<string, boolean>>({})
  const [feedback, setFeedback] = useState<Record<number, string>>({})
  const scenes = useStore($sceneLibrary)
  const scenePage = useStore($scenePage)
  const sceneTotal = useStore($sceneTotal)
  const outfits = useStore($outfits)
  const [createOpen, setCreateOpen] = useState(false)

  const [create, setCreate] = useState({
    name: '',
    sceneId: '',
    sceneLabel: '',
    outfitId: '',
    source: 'ai' as DesktopVideoSource,
    requestId: crypto.randomUUID()
  })

  const [sceneQuery, setSceneQuery] = useState('')
  const [rename, setRename] = useState('')
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [design, setDesign] = useState({ name: '', description: '', kind: 'loop' as 'loop' | 'once', duration: 10 })
  const [designMessage, setDesignMessage] = useState<string | null>(null)
  const [externalAction, setExternalAction] = useState<DesktopVideoAction | null>(null)
  const [externalBaseVersion, setExternalBaseVersion] = useState(0)
  const [externalPrompt, setExternalPrompt] = useState<Awaited<ReturnType<typeof getDesktopVideoPrompt>> | null>(null)
  const [externalRequirements, setExternalRequirements] = useState('')
  const [externalPromptRequirements, setExternalPromptRequirements] = useState('')
  const [externalFilePath, setExternalFilePath] = useState<string | null>(null)
  const [externalPromptLoading, setExternalPromptLoading] = useState(false)
  const [externalUploading, setExternalUploading] = useState(false)
  const [externalCopied, setExternalCopied] = useState(false)
  const [externalReferenceZoom, setExternalReferenceZoom] = useState<{ label: string; url: string } | null>(null)
  const externalRequest = useRef(0)
  const externalAuthSession = useRef(authSessionId)
  const beginAsync = useAsyncGuard()
  const selected = sets.find(item => item.id === selectedSetId)
  const currentMatches = selected?.is_current === true
  const externalSet = sets.find(set => set.id === externalAction?.set_id)
  const liveExternalAction = externalSet?.actions.find(action => action.id === externalAction?.id)

  const externalStale = Boolean(
    externalAction &&
    (!externalSet ||
      externalSet.base_version !== externalBaseVersion ||
      liveExternalAction?.draft?.id !== externalAction.draft?.id ||
      liveExternalAction?.draft?.image_url?.split(/[?#]/)[0] !== externalAction.draft?.image_url?.split(/[?#]/)[0] ||
      liveExternalAction?.draft?.image_confirmed !== externalAction.draft?.image_confirmed ||
      liveExternalAction?.current?.image_url?.split(/[?#]/)[0] !== externalAction.current?.image_url?.split(/[?#]/)[0])
  )

  const working = sets.some(
    item =>
      item.actions.some(action => action.status === 'processing' && !isActionPaused(action)) ||
      item.proposals?.some(proposal => proposal.status === 'pending')
  )

  useEffect(() => {
    if (authSessionId) {
      void refreshDesktopVideos()
    }
  }, [authSessionId])

  useEffect(() => {
    if (externalAuthSession.current === authSessionId) {
      return
    }

    externalAuthSession.current = authSessionId
    externalRequest.current += 1
    setExternalAction(null)
    setExternalPrompt(null)
    setExternalRequirements('')
    setExternalPromptRequirements('')
    setExternalCopied(false)
    setExternalFilePath(null)
    setExternalReferenceZoom(null)
    setExternalPromptLoading(false)
    setExternalUploading(false)
    setSelectedSetId(null)
    setCreateOpen(false)
    setCreate({ name: '', sceneId: '', sceneLabel: '', outfitId: '', source: 'ai', requestId: crypto.randomUUID() })
    setFeedback({})
    setImagePreviews({})
    setDeleteOpen(false)
    setBusy(false)
    setRename('')
    setDesign({ name: '', description: '', kind: 'loop', duration: 10 })
    setDesignMessage(null)
    setSceneQuery('')
  }, [authSessionId])

  useEffect(() => {
    if (!working) {
      return
    }

    const timer = window.setInterval(() => void refreshDesktopVideos(), 5000)

    return () => window.clearInterval(timer)
  }, [working])

  const run = async (action: () => Promise<unknown>): Promise<void> => {
    const isLive = beginAsync()
    setBusy(true)

    try {
      await action()
    } catch (failure) {
      if (isLive()) {
        notifyError(failure, t.title)
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const play = async (action: DesktopVideoAction): Promise<void> => {
    if ($presentation.get().effectiveMode !== 'desktop') {
      await shellApi().presentation.setMode('desktop')
    }

    await playDesktopVideoAction(action.id, action.set_id)
  }

  const prepare = async (): Promise<void> => {
    if ($presentation.get().effectiveMode !== 'desktop') {
      await shellApi().presentation.setMode('desktop')
    }

    await ensureDesktopVideoCurrent()
  }

  const loadExternalPrompt = async (action: DesktopVideoAction, requirements = ''): Promise<void> => {
    const requestId = ++externalRequest.current
    const authScope = captureAuthScope()
    const isLive = beginAsync()
    const isCurrent = (): boolean => Boolean(authScope?.() && isLive() && requestId === externalRequest.current)
    const submittedRequirements = requirements.trim()
    setExternalPromptLoading(true)

    try {
      const set = $desktopVideoSets.get().find(item => item.id === action.set_id)
      const latest = set?.actions.find(item => item.id === action.id)

      if (!set || !latest) {
        throw new Error(t.stalePrompt)
      }

      const prompt = await getDesktopVideoPrompt(latest, set, submittedRequirements)

      if (isCurrent()) {
        if (externalStale) {
          setExternalFilePath(null)
        }

        setExternalAction(latest)
        setExternalBaseVersion(set.base_version)
        setExternalPrompt(prompt)
        setExternalPromptRequirements(submittedRequirements)
        setExternalCopied(false)
      }
    } catch (failure) {
      if (isCurrent()) {
        notifyError(failure, t.externalTitle)
      }
    } finally {
      if (isCurrent()) {
        setExternalPromptLoading(false)
      }
    }
  }

  const openExternal = (action: DesktopVideoAction): void => {
    setExternalAction(action)
    setExternalBaseVersion($desktopVideoSets.get().find(set => set.id === action.set_id)?.base_version ?? 0)
    setExternalPrompt(null)
    setExternalRequirements('')
    setExternalPromptRequirements('')
    setExternalFilePath(null)
    setExternalReferenceZoom(null)
    setExternalCopied(false)
    void loadExternalPrompt(action)
  }

  const closeExternal = (): void => {
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
    if (!externalAction || !externalFilePath) {
      return
    }

    const action = externalAction
    const isCurrent = captureAuthScope()

    if (!authSessionId || !isCurrent) {
      return
    }

    setExternalUploading(true)

    try {
      if (!externalSet || externalStale) {
        throw new Error(t.stalePrompt)
      }

      await uploadDesktopVideoAction(
        action,
        { ...externalSet, base_version: externalBaseVersion },
        externalFilePath,
        authSessionId
      )

      if (isCurrent()) {
        closeExternal()
      }
    } catch (failure) {
      if (isCurrent()) {
        notifyError(failure, t.externalTitle)
        setExternalUploading(false)
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
    await confirmDesktopVideoImage(action, selected, false)

    if (!scope?.()) {
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

    if (!selected || !authSessionId || !scope) {
      return
    }

    const [path] = await fileApi().selectPaths({
      filters: [{ name: t.imageFilterName, extensions: ['png', 'jpg', 'jpeg', 'webp'] }],
      multiple: false,
      title: t.uploadImage
    })

    if (path && scope()) {
      await uploadDesktopVideoAction(action, selected, path, authSessionId, 'image')
    }
  }

  const activate = async (): Promise<void> => {
    if (!selected) {
      return
    }

    const scope = captureAuthScope()
    const activated = await activateDesktopVideoSet(selected)

    if (activated && scope?.() && $presentation.get().effectiveMode !== 'desktop') {
      await shellApi().presentation.setMode('desktop')
    }
  }

  const openCreate = (): void => {
    setCreateOpen(true)
    void loadSceneLibrary('', 0)
    void hydrateWardrobe()
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-strong">{t.title}</h2>
          <p className="mt-1 text-xs text-muted">{t.hint}</p>
        </div>
        <div className="flex gap-2">
          <button
            className={BTN_SUBTLE}
            disabled={loading || busy}
            onClick={() => void refreshDesktopVideos()}
            type="button"
          >
            {t.refresh}
          </button>
          <button className={BTN_PRIMARY} disabled={busy} onClick={() => void run(prepare)} type="button">
            {t.prepare}
          </button>
          <button className={BTN_PRIMARY} disabled={busy} onClick={openCreate} type="button">
            {t.createSet}
          </button>
        </div>
      </header>
      {state && (
        <div className="mb-4 flex flex-wrap gap-5 border-b border-line-hairline pb-3 text-xs text-body">
          <label className="flex items-center gap-2">
            <input
              checked={state.pinned}
              disabled={busy}
              onChange={event => void run(() => setDesktopVideoPreferences({ pinned: event.target.checked }))}
              type="checkbox"
            />
            {t.pin}
          </label>
          <label className="flex items-center gap-2">
            <input
              checked={state.autonomous_enabled}
              disabled={busy}
              onChange={event =>
                void run(() => setDesktopVideoPreferences({ autonomous_enabled: event.target.checked }))
              }
              type="checkbox"
            />
            {t.autonomy}
          </label>
        </div>
      )}
      {(error || state?.preparation_error) && (
        <p className="mb-4 text-sm text-warning" role="status">
          {state?.preparation_error || error || t.fail}
        </p>
      )}
      {loading && !sets.length && <p className="text-sm text-muted">{t.loading}</p>}
      {!loading && !sets.length && <p className="text-sm text-muted">{t.empty}</p>}
      {selected ? (
        <>
          <button className={`${BTN_SUBTLE} mb-4 self-start`} onClick={() => setSelectedSetId(null)} type="button">
            <ArrowLeft size={14} />
            {t.back}
          </button>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <h3 className="mr-auto text-sm font-semibold text-strong">
              {selected.title} {selected.is_default ? `· ${t.defaultSet}` : ''}
            </h3>
            <button
              className={BTN_PRIMARY}
              disabled={
                busy || currentMatches || !selected.actions.some(action => action.key === 'idle' && action.playable)
              }
              onClick={() => void run(activate)}
              type="button"
            >
              {currentMatches ? t.current : t.play}
            </button>
            <input
              aria-label={t.renameSet}
              className={`${INPUT_CLASS} w-40`}
              maxLength={160}
              onChange={event => setRename(event.target.value)}
              placeholder={selected.title}
              value={rename}
            />
            <button
              className={BTN_SUBTLE}
              disabled={busy || !rename.trim()}
              onClick={() =>
                void run(async () => {
                  await renameDesktopVideoSet(selected.id, rename.trim())
                  setRename('')
                })
              }
              type="button"
            >
              {t.renameSet}
            </button>
            <button className={BTN_SUBTLE} disabled={busy} onClick={() => setDeleteOpen(true)} type="button">
              {t.deleteSet}
            </button>
          </div>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {selected.actions.map(action => (
              <article
                className="flex flex-col gap-3 rounded-xl border border-line-hairline p-3"
                id={`desktop-life-action-${action.id}`}
                key={action.id}
              >
                {action.current && (
                  <>
                    <p className="text-xs font-medium text-muted">{t.currentContent}</p>
                    <DesktopLifePreview action={action} />
                  </>
                )}
                {action.draft && (
                  <>
                    <p className="text-xs font-medium text-accent">
                      {action.draft.image_confirmed || action.draft.candidate_video_url
                        ? t.draftContent
                        : t.imageReview}
                    </p>
                    <DesktopLifePreview
                      action={action}
                      draft
                      onImageReady={() =>
                        setImagePreviews(current => ({
                          ...current,
                          [`${action.draft?.id}:${action.draft?.image_url}`]: true
                        }))
                      }
                    />
                  </>
                )}
                {!action.current && !action.draft && <DesktopLifePreview action={action} />}
                {action.needs_update && (
                  <p className="text-xs text-warning" role="status">
                    {t.needsUpdate}
                  </p>
                )}
                {action.unavailable_reason && !action.playable && (
                  <p className="text-xs text-muted">{action.unavailable_reason}</p>
                )}
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h4 className="text-sm font-medium text-strong">
                      {action.preset
                        ? t.actionNames[action.key as keyof typeof t.actionNames] || action.name
                        : action.name}
                    </h4>
                    <p className="mt-1 text-xs text-muted">
                      {action.kind === 'loop' ? t.loop : t.once} · {t.targetDuration} {action.target_duration_seconds}s
                      {action.actual_duration_ms !== null && (
                        <>
                          {' '}
                          · {t.actualDuration} {(action.actual_duration_ms / 1000).toFixed(1)}s
                        </>
                      )}
                      {action.actual_duration_ms === null &&
                        action.duration_seconds !== action.target_duration_seconds && (
                          <>
                            {' '}
                            · {t.creationDuration} {action.duration_seconds}s
                          </>
                        )}{' '}
                      · {isActionPaused(action) ? t.paused : t.statuses[action.status]}
                    </p>
                  </div>
                  {state?.current?.id === selected.id && state.selected_action_id === action.id && (
                    <span className="text-xs text-accent">{t.playing}</span>
                  )}
                </div>
                <p className="text-xs leading-relaxed text-body">{action.description}</p>
                {action.use_when.length > 0 && (
                  <p className="text-xs leading-relaxed text-muted">
                    {t.useWhen}：{action.use_when.join('；')}
                  </p>
                )}
                {action.avoid_when.length > 0 && (
                  <p className="text-xs leading-relaxed text-muted">
                    {t.avoidWhen}：{action.avoid_when.join('；')}
                  </p>
                )}
                <p className="text-xs text-muted">
                  {action.continuity === 'base' && action.base_version === null
                    ? t.basePending
                    : action.continuity === 'base' && action.base_version === selected.base_version
                      ? t.baseReturn
                      : t.independentScene}
                </p>
                {action.error && (
                  <p className="text-xs text-warning" role="status">
                    {action.error}
                  </p>
                )}
                {isActionPaused(action) ? (
                  <p className="text-xs text-muted" role="status">
                    {t.resumeHint}
                  </p>
                ) : (
                  <textarea
                    aria-label={t.feedback}
                    className={`${INPUT_CLASS} min-h-16 resize-y text-xs`}
                    maxLength={600}
                    onChange={event => setFeedback(current => ({ ...current, [action.id]: event.target.value }))}
                    placeholder={t.feedback}
                    rows={2}
                    value={feedback[action.id] ?? ''}
                  />
                )}
                <div className="flex flex-wrap gap-2">
                  <button
                    className={BTN_SUBTLE}
                    disabled={
                      busy ||
                      action.status === 'processing' ||
                      (action.continuity === 'base' && action.key !== 'idle' && !selected.base_image_url)
                    }
                    onClick={() => openExternal(action)}
                    type="button"
                  >
                    {t.external}
                  </button>
                  {action.continuity === 'base' && action.key !== 'idle' ? (
                    <button
                      className={BTN_SUBTLE}
                      onClick={() =>
                        document
                          .getElementById(
                            `desktop-life-action-${selected.actions.find(item => item.key === 'idle')?.id}`
                          )
                          ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                      }
                      type="button"
                    >
                      {t.manageBase}
                    </button>
                  ) : (
                    <>
                      <button
                        className={BTN_SUBTLE}
                        disabled={busy || action.status === 'processing'}
                        onClick={() => void run(() => generateDesktopVideoImage(action, selected, feedback[action.id]))}
                        type="button"
                      >
                        {action.draft?.image_url || action.current ? t.adjustImage : t.generateImage}
                      </button>
                      {(action.draft?.image_url || action.current) && (
                        <button
                          className={BTN_SUBTLE}
                          disabled={busy || action.status === 'processing'}
                          onClick={() =>
                            void run(() => generateDesktopVideoImage(action, selected, feedback[action.id], true))
                          }
                          type="button"
                        >
                          {t.regenerateImage}
                        </button>
                      )}
                      <button
                        className={BTN_SUBTLE}
                        disabled={busy || action.status === 'processing'}
                        onClick={() => void run(() => uploadImage(action))}
                        type="button"
                      >
                        {t.uploadImage}
                      </button>
                    </>
                  )}
                  {action.stage === 'image_review' && action.draft?.image_url && !action.draft.image_confirmed && (
                    <button
                      className={BTN_PRIMARY}
                      disabled={
                        busy ||
                        action.status === 'processing' ||
                        !imagePreviews[`${action.draft.id}:${action.draft.image_url}`]
                      }
                      onClick={() => void run(() => confirmDesktopVideoImage(action, selected))}
                      type="button"
                    >
                      {t.confirmImage}
                    </button>
                  )}
                  {action.stage === 'image_review' && action.draft?.image_url && !action.draft.image_confirmed && (
                    <button
                      className={BTN_SUBTLE}
                      disabled={
                        busy ||
                        action.status === 'processing' ||
                        !imagePreviews[`${action.draft.id}:${action.draft.image_url}`]
                      }
                      onClick={() => void run(() => confirmForExternal(action))}
                      type="button"
                    >
                      {t.confirmExternal}
                    </button>
                  )}
                  <button
                    className={BTN_SUBTLE}
                    disabled={
                      busy ||
                      (action.status === 'processing' && !isActionPaused(action)) ||
                      Boolean(action.draft?.image_url && !action.draft.image_confirmed) ||
                      (action.continuity === 'base' && action.key !== 'idle' && !selected.base_image_url)
                    }
                    onClick={() => void run(() => generateDesktopVideoAction(action, selected, feedback[action.id]))}
                    type="button"
                  >
                    {isActionPaused(action) ? t.resume : action.current ? t.remakeVideo : t.generate}
                  </button>
                  {action.draft && (
                    <button
                      className={BTN_SUBTLE}
                      disabled={busy}
                      onClick={() => void run(() => cancelDesktopVideoDraft(action))}
                      type="button"
                    >
                      {action.status === 'processing' ? t.cancel : t.discardDraft}
                    </button>
                  )}
                  {action.video_url && (
                    <button
                      className={BTN_PRIMARY}
                      disabled={busy || !action.playable || !currentMatches}
                      onClick={() => void run(() => play(action))}
                      type="button"
                    >
                      {t.play}
                    </button>
                  )}
                  {action.status === 'review_pending' && action.draft?.candidate_video_url && (
                    <>
                      <button
                        className={BTN_PRIMARY}
                        disabled={busy}
                        onClick={() => void run(() => reviewDesktopVideoAction(action, selected, true))}
                        type="button"
                      >
                        {action.key === 'idle'
                          ? `${t.accept} · ${t.updateActions(selected.actions.filter(item => item.id !== action.id && item.continuity === 'base' && item.current && item.enabled).length)}`
                          : t.accept}
                      </button>
                      <button
                        className={BTN_SUBTLE}
                        disabled={busy}
                        onClick={() => void run(() => reviewDesktopVideoAction(action, selected, false))}
                        type="button"
                      >
                        {t.reject}
                      </button>
                    </>
                  )}
                </div>
              </article>
            ))}
          </div>
          {selected.proposals?.length ? (
            <section className="mt-5 rounded-xl border border-line-hairline p-4">
              <h4 className="mb-3 text-sm font-medium text-strong">{t.designs}</h4>
              <div className="flex flex-col gap-3">
                {selected.proposals.map(proposal => (
                  <article
                    className="flex flex-wrap items-start justify-between gap-3 border-b border-line-hairline pb-3 last:border-0 last:pb-0"
                    key={proposal.proposal_id}
                  >
                    <div className="min-w-0 flex-1">
                      <strong className="text-xs text-strong">{proposal.name || t.design}</strong>
                      <span className="ml-3 text-xs text-muted">
                        {t.proposalStatuses[proposal.status as keyof typeof t.proposalStatuses] || proposal.status}
                      </span>
                      {proposal.description && <p className="mt-1 text-xs text-body">{proposal.description}</p>}
                      {proposal.review_reason && <p className="mt-1 text-xs text-muted">{proposal.review_reason}</p>}
                    </div>
                    {proposal.action_id && selected.actions.some(action => action.id === proposal.action_id) && (
                      <button
                        className={BTN_SUBTLE}
                        onClick={() =>
                          document.getElementById(`desktop-life-action-${proposal.action_id}`)?.scrollIntoView({
                            behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
                              ? 'instant'
                              : 'smooth',
                            block: 'center'
                          })
                        }
                        type="button"
                      >
                        {t.inspectAction}
                      </button>
                    )}
                  </article>
                ))}
              </div>
            </section>
          ) : null}
          {selected && (
            <details className="mt-5 rounded-xl border border-line-hairline p-4">
              <summary className="cursor-pointer text-sm font-medium text-strong">{t.design}</summary>
              <form
                className="mt-4 flex flex-col gap-3"
                onSubmit={event => {
                  event.preventDefault()
                  const isLive = beginAsync()
                  void run(async () => {
                    const proposal = await designDesktopVideoAction({
                      name: design.name.trim(),
                      motion_description: design.description.trim(),
                      kind: design.kind,
                      duration_seconds: design.duration,
                      expected_set_id: selected.id
                    })

                    if (isLive()) {
                      setDesign(current => ({ ...current, name: '', description: '' }))
                      setDesignMessage(proposal.review_reason || t.designSubmitted)
                    }
                  })
                }}
              >
                {designMessage && (
                  <p className="text-xs text-muted" role="status">
                    {designMessage}
                  </p>
                )}
                <input
                  aria-label={t.actionName}
                  className={INPUT_CLASS}
                  maxLength={64}
                  onChange={event => setDesign(current => ({ ...current, name: event.target.value }))}
                  placeholder={t.actionName}
                  required
                  value={design.name}
                />
                <textarea
                  aria-label={t.actionDescription}
                  className={`${INPUT_CLASS} min-h-24 resize-y`}
                  maxLength={600}
                  minLength={10}
                  onChange={event => setDesign(current => ({ ...current, description: event.target.value }))}
                  placeholder={t.actionDescription}
                  required
                  rows={3}
                  value={design.description}
                />
                <div className="flex flex-wrap items-center gap-4">
                  <PanelSelect
                    ariaLabel={t.design}
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
                  <label className="flex items-center gap-2 text-xs text-body">
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
                <button
                  className={`${BTN_PRIMARY} self-start`}
                  disabled={busy || !design.name.trim() || design.description.trim().length < 10}
                  type="submit"
                >
                  {t.submitDesign}
                </button>
              </form>
            </details>
          )}
        </>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {sets.map(item => (
            <button
              className="flex flex-col gap-2 rounded-xl border border-line-hairline p-4 text-left hover:bg-white/5"
              key={item.id}
              onClick={() => {
                setSelectedSetId(item.id)
                setRename('')
              }}
              type="button"
            >
              <DesktopLifeCover
                url={
                  item.actions.find(action => action.poster_url)?.poster_url ??
                  item.base_image_url ??
                  item.actions.find(action => action.draft?.image_url)?.draft?.image_url ??
                  null
                }
              />
              <strong className="text-sm text-strong">{item.title}</strong>
              <span className="text-xs text-muted">
                {new Date(item.created_at).toLocaleDateString()} ·{' '}
                {item.actions.filter(action => action.playable).length}/{item.actions.length}
              </span>
              {item.is_current && <span className="text-xs text-accent">{t.current}</span>}
            </button>
          ))}
        </div>
      )}
      {createOpen && (
        <WizardModal
          draggable
          onClose={() => setCreateOpen(false)}
          regionId="desktop-life-create"
          title={t.createSet}
          widthClass="max-w-2xl"
        >
          <form
            className="flex flex-col gap-4"
            onSubmit={event => {
              event.preventDefault()
              const scope = captureAuthScope()
              void run(async () => {
                const created = await createDesktopVideoSet({
                  request_id: create.requestId,
                  name: create.name.trim(),
                  scene_id: Number(create.sceneId),
                  outfit_id: Number(create.outfitId),
                  source: create.source
                })

                if (scope?.()) {
                  setSelectedSetId(created.id)
                  setCreateOpen(false)
                  setCreate({
                    name: '',
                    sceneId: '',
                    sceneLabel: '',
                    outfitId: '',
                    source: 'ai',
                    requestId: crypto.randomUUID()
                  })
                }
              })
            }}
          >
            <p className="text-xs text-muted">{t.suiteHint}</p>
            <input
              aria-label={t.setName}
              className={INPUT_CLASS}
              maxLength={160}
              onChange={event =>
                setCreate(current => ({ ...current, name: event.target.value, requestId: crypto.randomUUID() }))
              }
              placeholder={t.setName}
              required
              value={create.name}
            />
            <label className="flex flex-col gap-2 text-xs text-body">
              {t.sceneLibrary}
              <div className="flex gap-2">
                <input
                  aria-label={t.searchScenes}
                  className={INPUT_CLASS}
                  onChange={event => setSceneQuery(event.target.value)}
                  placeholder={t.searchScenes}
                  value={sceneQuery}
                />
                <button className={BTN_SUBTLE} onClick={() => void loadSceneLibrary(sceneQuery, 0)} type="button">
                  {t.search}
                </button>
              </div>
              <PanelSelect
                ariaLabel={t.sceneLibrary}
                onChange={value =>
                  setCreate(current => ({
                    ...current,
                    sceneId: value,
                    sceneLabel: scenes.find(scene => scene.id === value)?.title ?? '',
                    requestId: crypto.randomUUID()
                  }))
                }
                options={[
                  { value: '', label: t.chooseScene },
                  ...(create.sceneId && !scenes.some(scene => scene.id === create.sceneId)
                    ? [{ value: create.sceneId, label: create.sceneLabel }]
                    : []),
                  ...scenes
                    .filter(scene => scene.status === 'ready')
                    .map(scene => ({ value: scene.id, label: scene.title }))
                ]}
                value={create.sceneId}
              />
              <div className="flex gap-2">
                <button
                  className={BTN_SUBTLE}
                  disabled={scenePage === 0}
                  onClick={() => void loadSceneLibrary(sceneQuery, scenePage - 1)}
                  type="button"
                >
                  {t.previous}
                </button>
                <button
                  className={BTN_SUBTLE}
                  disabled={(scenePage + 1) * PAGE_SIZE >= sceneTotal}
                  onClick={() => void loadSceneLibrary(sceneQuery, scenePage + 1)}
                  type="button"
                >
                  {t.next}
                </button>
              </div>
            </label>
            <label className="flex flex-col gap-2 text-xs text-body">
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
            <PanelSelect
              ariaLabel={t.creationSource}
              onChange={value => {
                if (value === 'ai' || value === 'image_upload' || value === 'video_upload') {
                  setCreate(current => ({ ...current, source: value, requestId: crypto.randomUUID() }))
                }
              }}
              options={[
                { value: 'ai', label: t.generateImage },
                { value: 'image_upload', label: t.uploadImage },
                { value: 'video_upload', label: t.external }
              ]}
              value={create.source}
            />
            <button
              className={BTN_PRIMARY}
              disabled={busy || !create.name.trim() || !create.sceneId || !create.outfitId}
              type="submit"
            >
              {t.createSet}
            </button>
          </form>
        </WizardModal>
      )}
      {deleteOpen && selected && (
        <WizardModal
          onClose={() => setDeleteOpen(false)}
          regionId="desktop-life-delete"
          title={t.deleteSet}
          widthClass="max-w-md"
        >
          <p className="mb-4 text-sm text-body">{t.deleteHint}</p>
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
        </WizardModal>
      )}
      {externalAction && (
        <WizardModal
          backdropClose={!externalUploading}
          closeDisabled={externalUploading}
          draggable
          escClose={!externalUploading && !externalReferenceZoom}
          onClose={externalUploading ? () => undefined : closeExternal}
          regionId="desktop-life-external"
          title={t.externalTitle}
          widthClass="max-w-2xl"
        >
          <div className="flex flex-col gap-4">
            <p className="text-xs leading-relaxed text-muted">{t.externalHint}</p>
            {externalStale && (
              <p className="text-xs text-warning" role="status">
                {t.stalePrompt}
              </p>
            )}
            <label className="flex flex-col gap-2 text-xs text-body">
              {t.requirements}
              <textarea
                className={`${INPUT_CLASS} min-h-16 resize-y`}
                maxLength={600}
                onChange={event => setExternalRequirements(event.target.value)}
                placeholder={t.requirements}
                rows={2}
                value={externalRequirements}
              />
            </label>
            {externalPromptLoading ? (
              <p className="text-xs text-muted" role="status">
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
              <section className="flex flex-col gap-2">
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
                  className={`${INPUT_CLASS} min-h-40 resize-y text-xs leading-relaxed`}
                  readOnly
                  rows={7}
                  value={externalPrompt.prompt}
                />
                <h4 className="mt-2 text-sm font-medium text-strong">{t.references}</h4>
                {externalPrompt.references?.length ? (
                  <div className="grid grid-cols-3 gap-2">
                    {externalPrompt.references.map(reference => (
                      <ExternalReference
                        key={`${reference.label}:${reference.url}`}
                        onZoom={url => setExternalReferenceZoom({ label: reference.label, url })}
                        reference={reference}
                      />
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted">{t.noReferences}</p>
                )}
              </section>
            ) : (
              !externalPromptLoading && <p className="text-xs text-muted">{t.promptEmpty}</p>
            )}
            <section className="flex flex-col gap-3 border-t border-line-hairline pt-4">
              <div className="flex flex-wrap items-center gap-3">
                <button
                  className={BTN_SUBTLE}
                  disabled={externalUploading}
                  onClick={() => void chooseExternalVideo()}
                  type="button"
                >
                  {t.selectVideo}
                </button>
                {externalFilePath && (
                  <span className="max-w-full truncate text-xs text-body" title={externalFilePath}>
                    {t.selectedVideo}: {localFileName(externalFilePath)}
                  </span>
                )}
              </div>
              <button
                className={`${BTN_PRIMARY} self-start`}
                disabled={!externalFilePath || externalUploading || externalStale}
                onClick={() => void uploadExternalVideo()}
                type="button"
              >
                {externalUploading ? t.uploading : t.upload}
              </button>
            </section>
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
