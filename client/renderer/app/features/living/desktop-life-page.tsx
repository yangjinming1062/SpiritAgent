import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import {
  $desktopVideoError,
  $desktopVideoLoading,
  $desktopVideoSets,
  $desktopVideoState,
  designDesktopVideoAction,
  type DesktopVideoAction,
  ensureDesktopVideoCurrent,
  generateDesktopVideoAction,
  playDesktopVideoAction,
  refreshDesktopVideos,
  reviewDesktopVideoAction,
  setDesktopVideoPreferences
} from '@/modules/desktop-videos'
import { useResolvedMediaSrc } from '@/modules/media'
import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { ArrowLeft } from '@/shared/lib/icons'
import { BTN_PRIMARY, BTN_SUBTLE, INPUT_CLASS, PanelSelect } from '@/shared/panel'
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

function DesktopLifePreview({ action }: { action: DesktopVideoAction }): React.JSX.Element {
  const active = usePanelActivity()
  const t = useDesktopLifeStrings()
  const candidate = action.status === 'review_pending'
  const videoUrl = candidate ? action.candidate_video_url || action.video_url : action.video_url
  const posterUrl = candidate ? action.candidate_poster_url || action.poster_url : action.poster_url
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
          <img alt="" className="h-full w-full object-contain" src={src} />
        )
      ) : (
        <span className="text-sm text-muted">{t.noPreview}</span>
      )}
    </div>
  )
}

export function DesktopLifePage(): React.JSX.Element {
  const t = useDesktopLifeStrings()
  const auth = useStore($auth)
  const state = useStore($desktopVideoState)
  const sets = useStore($desktopVideoSets)
  const loading = useStore($desktopVideoLoading)
  const error = useStore($desktopVideoError)
  const [selectedSetId, setSelectedSetId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<Record<number, string>>({})
  const [design, setDesign] = useState({ name: '', description: '', kind: 'loop' as 'loop' | 'once', duration: 10 })
  const [designMessage, setDesignMessage] = useState<string | null>(null)
  const beginAsync = useAsyncGuard()
  const selected = sets.find(item => item.id === selectedSetId)
  const currentMatches = selected?.is_current && selected.context_hash === state?.desired_context_hash

  const working = sets.some(
    item =>
      item.actions.some(action => action.status === 'processing' && !isActionPaused(action)) ||
      item.proposals?.some(proposal => proposal.status === 'pending')
  )

  useEffect(() => {
    if (auth.kind === 'authenticated') {
      void refreshDesktopVideos()
    }
  }, [auth.kind])

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
      await window.spiritagent.presentation.setMode('desktop')
    }

    await playDesktopVideoAction(action.id, action.set_id)
  }

  const prepare = async (): Promise<void> => {
    if ($presentation.get().effectiveMode !== 'desktop') {
      await window.spiritagent.presentation.setMode('desktop')
    }

    await ensureDesktopVideoCurrent()
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
          <h3 className="mb-4 text-sm font-semibold text-strong">{selected.title}</h3>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {selected.actions.map(action => (
              <article
                className="flex flex-col gap-3 rounded-xl border border-line-hairline p-3"
                id={`desktop-life-action-${action.id}`}
                key={action.id}
              >
                <DesktopLifePreview action={action} />
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h4 className="text-sm font-medium text-strong">
                      {action.preset
                        ? t.actionNames[action.key as keyof typeof t.actionNames] || action.name
                        : action.name}
                    </h4>
                    <p className="mt-1 text-xs text-muted">
                      {action.kind === 'loop' ? t.loop : t.once} · {action.duration_seconds}s ·{' '}
                      {isActionPaused(action) ? t.paused : t.statuses[action.status]}
                    </p>
                  </div>
                  {state?.current?.id === selected.id && state.selected_action_id === action.id && (
                    <span className="text-xs text-accent">{t.playing}</span>
                  )}
                </div>
                <p className="text-xs leading-relaxed text-body">
                  {action.preset
                    ? t.actionDescriptions[action.key as keyof typeof t.actionDescriptions] || action.description
                    : action.description}
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
                    className={isActionPaused(action) ? BTN_PRIMARY : BTN_SUBTLE}
                    disabled={busy || (action.status === 'processing' && !isActionPaused(action))}
                    onClick={() =>
                      void run(() =>
                        generateDesktopVideoAction(action.id, isActionPaused(action) ? undefined : feedback[action.id])
                      )
                    }
                    type="button"
                  >
                    {isActionPaused(action)
                      ? t.resume
                      : action.status === 'ready'
                        ? t.remake
                        : action.status === 'failed'
                          ? t.retry
                          : t.generate}
                  </button>
                  {action.video_url && (
                    <button
                      className={BTN_PRIMARY}
                      disabled={busy || !action.enabled || !currentMatches}
                      onClick={() => void run(() => play(action))}
                      title={currentMatches ? undefined : t.currentOnly}
                      type="button"
                    >
                      {t.play}
                    </button>
                  )}
                  {action.status === 'review_pending' && (
                    <>
                      <button
                        className={BTN_PRIMARY}
                        disabled={busy}
                        onClick={() => void run(() => reviewDesktopVideoAction(action.id, true))}
                        type="button"
                      >
                        {t.accept}
                      </button>
                      <button
                        className={BTN_SUBTLE}
                        disabled={busy}
                        onClick={() => void run(() => reviewDesktopVideoAction(action.id, false))}
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
          {currentMatches && (
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
              onClick={() => setSelectedSetId(item.id)}
              type="button"
            >
              <DesktopLifeCover url={item.actions.find(action => action.poster_url)?.poster_url ?? null} />
              <strong className="text-sm text-strong">{item.title}</strong>
              <span className="text-xs text-muted">
                {new Date(item.created_at).toLocaleDateString()} ·{' '}
                {item.actions.filter(action => action.status === 'ready').length}/{item.actions.length}
              </span>
              {item.is_current && <span className="text-xs text-accent">{t.current}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
