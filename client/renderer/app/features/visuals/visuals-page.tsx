import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { applyAppNavigation } from '@/app/navigation/app-navigation'
import { $outfits, hydrateWardrobe } from '@/modules/character'
import { ensureCompanionSession } from '@/modules/conversation'
import { MediaPreview, useResolvedMediaSrc } from '@/modules/media'
import { $sceneLibrary, loadSceneLibrary } from '@/modules/scene'
import {
  $actions,
  $visualsDetail,
  $visualsDetailError,
  $visualsError,
  $visualsItems,
  $visualsJobs,
  $visualsLoading,
  $visualsNextCursor,
  $visualsPresentation,
  $visualsTotal,
  clearVisualDetail,
  commandVisualJob,
  loadVisualDetail,
  refreshActions,
  refreshVisualPresentation,
  refreshVisuals,
  regenerateVisual,
  setVisualPreferences,
  type VisualDetail as VisualDetailData,
  type VisualItem,
  type VisualJob,
  visualsApi
} from '@/modules/visuals'
import { ImagePlus, MessageCircle, Sparkles } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import {
  Badge,
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SUBTLE,
  confirmAction,
  EmptyState,
  ErrorBanner,
  FIELD_LABEL,
  INPUT_CLASS,
  LoadingBlock,
  PanelSelect,
  SearchField,
  SettingCard,
  SettingRow,
  SettingsContent,
  SettingsSectionIntro,
  Toggle
} from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { emptyVisualCreation, visualJobCommands } from '../../../../../shared/visuals'

import { VisualCreatePanel } from './visual-create-panel'
import { VisualImportAnalysis } from './visual-import-analysis'
import { $visualsUi, updateVisualsUi } from './visuals-state'

const PAGE_SIZE = 12

function formatDuration(durationMs: number | null | undefined, seconds: (value: number) => string): string | null {
  return durationMs && durationMs > 0 ? seconds(Math.round(durationMs / 1000)) : null
}

function assetKindLabel(
  assetKind: VisualItem['asset_kind'],
  labels: ReturnType<typeof useStrings>['visuals']['assetKindLabels']
): string {
  return labels[assetKind]
}

/** 影像页：浏览与预览组合成品、查看与操作制作工作项、重做；库内预览不产生播放回执。 */
export function VisualsPage(): React.JSX.Element {
  const dict = useStrings()
  const t = dict.visuals
  const auth = useStore($auth)
  const ui = useStore($visualsUi)
  const items = useStore($visualsItems)
  const jobs = useStore($visualsJobs)
  const total = useStore($visualsTotal)
  const nextCursor = useStore($visualsNextCursor)
  const loading = useStore($visualsLoading)
  const error = useStore($visualsError)
  const detailState = useStore($visualsDetail)
  const detailError = useStore($visualsDetailError)
  const projection = useStore($visualsPresentation)
  const outfits = useStore($outfits)
  const scenes = useStore($sceneLibrary)
  const actions = useStore($actions)
  const [draftQuery, setDraftQuery] = useState(ui.query)
  const [draftSource, setDraftSource] = useState(ui.source)
  const [busy, setBusy] = useState(false)

  const authenticated = auth.kind === 'authenticated'

  useEffect(() => {
    if (!authenticated) {
      return
    }

    void refreshVisuals({
      query: ui.query || undefined,
      media_kind: ui.mediaKind === 'all' ? undefined : ui.mediaKind,
      source: ui.source || undefined,
      cursor: ui.cursors[ui.page] ?? undefined,
      include_total: true,
      limit: PAGE_SIZE
    })
    void refreshVisualPresentation()
  }, [authenticated, ui.query, ui.mediaKind, ui.source, ui.page, ui.cursors])

  // 素材统计只为就绪提示服务：仅挂载时拉一次，且不覆盖环境页的搜索与页码。
  useEffect(() => {
    if (!authenticated) {
      return
    }

    void refreshActions('')
    void hydrateWardrobe()

    if ($sceneLibrary.get().length === 0) {
      void loadSceneLibrary()
    }
  }, [authenticated])

  useEffect(() => {
    if (ui.detailOpen && ui.selectedId !== null) {
      void loadVisualDetail(ui.selectedId).catch(error_ => log.warn('visuals', 'Detail load failed', error_))
    }
  }, [ui.detailOpen, ui.selectedId])

  const detail = detailState?.id === ui.selectedId ? detailState.detail : null
  const currentVisualId = projection?.current_visual?.id ?? null
  const activeJobs = jobs.filter(job => !['succeeded', 'cancelled'].includes(job.state))
  const jobCommandLabels = { pause: t.jobPause, resume: t.jobResume, cancel: t.jobCancel, retry: t.jobRetry }

  const sourceSummary = (item: VisualItem): string =>
    [item.outfit_name, item.scene_title, item.action_name].filter(Boolean).join(' · ')

  const jobLabel = (job: VisualJob): string => {
    if (job.preparation) {
      return job.action_name ? `${t.preparationJob} · ${job.action_name}` : t.preparationJob
    }

    if (!job.action_name) {
      return job.target_kind === 'video' ? t.jobVideo : t.jobImage
    }

    return `${job.action_name} · ${job.target_kind === 'video' ? t.mediaKindVideo : t.mediaKindImage}`
  }

  const run = (operation: () => Promise<void>, failure: string): void => {
    setBusy(true)

    void operation()
      .catch(error_ => notifyError(error_, failure))
      .finally(() => setBusy(false))
  }

  const submitFilters = (): void => {
    updateVisualsUi({ query: draftQuery.trim(), source: draftSource.trim(), page: 0 })
  }

  const openCompanion = async (): Promise<void> => {
    try {
      const sessionId = await ensureCompanionSession()

      if (sessionId) {
        await applyAppNavigation({ page: 'chat', sessionId })
      }
    } catch (error_) {
      notifyError(error_, t.contactCompanion)
    }
  }

  const jobCommand = (job: VisualJob, command: 'pause' | 'resume' | 'cancel' | 'retry'): void => {
    if (command === 'cancel') {
      void confirmAction({
        title: t.cancelTitle,
        description: jobLabel(job),
        confirmLabel: t.jobCancelConfirm,
        variant: 'destructive'
      }).then(confirmed => {
        if (confirmed) {
          run(() => commandVisualJob(job.id, command), t.jobCommandFailed)
        }
      })

      return
    }

    run(() => commandVisualJob(job.id, command), t.jobCommandFailed)
  }

  const readiness = t.materialReady(
    outfits.filter(item => item.status === 'ready').length,
    scenes.length,
    actions.length
  )

  const hasFilters = Boolean(ui.query || ui.source || ui.mediaKind !== 'all')

  const clearFilters = (): void => {
    setDraftQuery('')
    setDraftSource('')
    updateVisualsUi({ query: '', source: '', mediaKind: 'all', page: 0 })
  }

  const openComposer = (mode: 'create' | 'upload'): void => {
    updateVisualsUi({ creation: { ...(ui.creation ?? emptyVisualCreation), mode } })
  }

  return (
    <SettingsContent>
      <SettingsSectionIntro hint={t.intro} title={t.title} />
      {!ui.detailOpen && (
        <VisualCreatePanel
          onDone={async visualId => {
            setDraftQuery('')
            setDraftSource('')
            updateVisualsUi({
              query: '',
              source: '',
              mediaKind: 'all',
              page: 0,
              ...(visualId ? { selectedId: visualId, detailOpen: true } : {})
            })
            await refreshVisuals({ limit: PAGE_SIZE, include_total: true })
          }}
        />
      )}
      <SettingCard className="mb-3" divided={false}>
        <div className="space-y-3 p-3">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div>
              <h3 className="text-xs font-semibold text-strong">{t.libraryTitle}</h3>
              <p className="mt-1 text-[11px] leading-relaxed text-muted">{t.libraryHint}</p>
            </div>
            {total !== null && <span className="text-[11px] text-faint">{t.total(total)}</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SearchField
              ariaLabel={t.searchAria}
              className="min-w-[200px] flex-1"
              onChange={setDraftQuery}
              onKeyDown={event => {
                if (event.key === 'Enter') {
                  submitFilters()
                }
              }}
              placeholder={t.searchPlaceholder}
              value={draftQuery}
            />
            <PanelSelect
              ariaLabel={t.mediaKindLabel}
              onChange={value => updateVisualsUi({ mediaKind: value, page: 0 })}
              options={[
                { value: 'all', label: t.mediaKindAll },
                { value: 'image', label: t.mediaKindImage },
                { value: 'video', label: t.mediaKindVideo }
              ]}
              value={ui.mediaKind}
            />
            <SearchField
              ariaLabel={t.sourceLabel}
              className="min-w-[200px] flex-1"
              onChange={setDraftSource}
              onKeyDown={event => {
                if (event.key === 'Enter') {
                  submitFilters()
                }
              }}
              placeholder={t.sourcePlaceholder}
              value={draftSource}
            />
            {hasFilters && (
              <button className={BTN_GHOST} onClick={clearFilters} type="button">
                {t.clearFilters}
              </button>
            )}
          </div>
        </div>
      </SettingCard>
      {error && (
        <ErrorBanner message={t.loadFailed} onRetry={() => void refreshVisuals({})} retryLabel={dict.common.retry} />
      )}

      {activeJobs.length > 0 && (
        <section className="mb-3">
          <h3 className="mb-2 text-xs font-medium text-muted">{t.jobsTitle}</h3>
          <SettingCard className="border border-accent-line bg-accent-soft/10" divided>
            {activeJobs.map(job => (
              <SettingRow
                description={`${t.jobState[job.state] ?? job.state}${job.error ? ` · ${job.error}` : ''}`}
                key={job.id}
                label={jobLabel(job)}
              >
                <div className="flex gap-2">
                  {visualJobCommands(job.state, job.failure_reason).map(command => (
                    <button
                      className={BTN_SUBTLE}
                      disabled={busy}
                      key={command}
                      onClick={() => jobCommand(job, command)}
                      type="button"
                    >
                      {command === 'resume' && job.failure_reason === 'result_download_failed'
                        ? t.jobReceive
                        : jobCommandLabels[command]}
                    </button>
                  ))}
                </div>
              </SettingRow>
            ))}
          </SettingCard>
        </section>
      )}

      {ui.detailOpen && ui.selectedId !== null ? (
        <div className="flex flex-col gap-3">
          <button
            className={BTN_GHOST}
            onClick={() => {
              clearVisualDetail()
              updateVisualsUi({ detailOpen: false })
            }}
            type="button"
          >
            {t.backToList}
          </button>
          {detailError && <ErrorBanner message={t.detailLoadFailed} />}
          {!detail && !detailError ? (
            <LoadingBlock label={t.loading} />
          ) : detail ? (
            <VisualDetailPanel
              busy={busy}
              current={currentVisualId === detail.id}
              detail={detail}
              onDelete={() => {
                void confirmAction({
                  title: t.deleteTitle,
                  description: detail.title,
                  confirmLabel: t.delete,
                  variant: 'destructive'
                }).then(confirmed => {
                  if (!confirmed) {
                    return
                  }

                  run(async () => {
                    await visualsApi.delete(detail.id)
                    clearVisualDetail()
                    updateVisualsUi({ detailOpen: false, selectedId: null, page: 0 })
                    await refreshVisuals({ limit: PAGE_SIZE, include_total: true })
                  }, t.deleteFailed)
                })
              }}
              onRedo={(feedback, videoFeedback) =>
                run(
                  () =>
                    regenerateVisual(detail.id, {
                      expected_version: detail.version,
                      feedback: feedback.trim() || undefined,
                      video_feedback: videoFeedback.trim() || undefined
                    }).then(() => undefined),
                  t.redoFailed
                )
              }
              t={t}
            />
          ) : null}
        </div>
      ) : loading && items.length === 0 ? (
        <LoadingBlock label={t.loading} />
      ) : items.length === 0 ? (
        <EmptyState
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <button className={BTN_PRIMARY} onClick={() => openComposer('upload')} type="button">
                <ImagePlus aria-hidden="true" className="size-3.5" />
                {t.composer.upload}
              </button>
              <button className={BTN_SUBTLE} onClick={() => openComposer('create')} type="button">
                <Sparkles aria-hidden="true" className="size-3.5" />
                {t.composer.create}
              </button>
              <button className={BTN_SUBTLE} onClick={() => void openCompanion()} type="button">
                <MessageCircle aria-hidden="true" className="size-3.5" />
                {t.contactCompanion}
              </button>
            </div>
          }
          description={`${t.emptyHint}${readiness ? ` ${readiness}` : ''}`}
          title={ui.query || ui.source || ui.mediaKind !== 'all' ? t.emptySearch : t.emptyTitle}
        />
      ) : (
        <>
          <SettingCard className="mb-3" divided={false}>
            <div className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {items.map(item => (
                <VisualCard
                  current={currentVisualId === item.id}
                  item={item}
                  key={item.id}
                  onSelect={() => updateVisualsUi({ selectedId: item.id, detailOpen: true })}
                  sourceSummary={sourceSummary(item)}
                  t={t}
                />
              ))}
            </div>
          </SettingCard>
          <div className="flex items-center justify-between">
            <button
              className={BTN_SUBTLE}
              disabled={busy || ui.page === 0}
              onClick={() => updateVisualsUi({ page: Math.max(0, ui.page - 1) })}
              type="button"
            >
              {t.prev}
            </button>
            <span className="text-[11px] text-faint">{total !== null ? t.total(total) : null}</span>
            <button
              className={BTN_SUBTLE}
              disabled={busy || !nextCursor}
              onClick={() =>
                nextCursor &&
                updateVisualsUi({ page: ui.page + 1, cursors: [...ui.cursors.slice(0, ui.page + 1), nextCursor] })
              }
              type="button"
            >
              {t.next}
            </button>
          </div>
        </>
      )}

      <section className="mt-4">
        <h3 className="mb-2 text-xs font-medium text-muted">{t.prefsTitle}</h3>
        <SettingCard>
          <SettingRow description={t.autonomousDesc} label={t.autonomousLabel}>
            <Toggle
              ariaLabel={t.autonomousLabel}
              checked={projection?.autonomous_enabled ?? true}
              onChange={next =>
                run(() => setVisualPreferences({ autonomous_enabled: next }).then(() => undefined), t.prefsFailed)
              }
            />
          </SettingRow>
        </SettingCard>
      </section>
    </SettingsContent>
  )
}

function VisualCard({
  item,
  current,
  sourceSummary,
  onSelect,
  t
}: {
  item: VisualItem
  current: boolean
  sourceSummary: string
  onSelect: () => void
  t: ReturnType<typeof useStrings>['visuals']
}): React.JSX.Element {
  const cover = useResolvedMediaSrc({
    type: 'image',
    url: item.poster_url || (item.media_kind === 'image' ? item.media_url : '')
  })

  const analysisPending = ['queued', 'running', 'paused'].includes(item.analysis_state ?? '')

  return (
    <button
      aria-label={item.title}
      className={cn(
        'group flex flex-col gap-2 rounded-2xl border p-2.5 text-left transition',
        'focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2',
        current
          ? 'border-accent-line bg-accent-soft/15 shadow-sm'
          : 'border-line-hairline bg-fill-faint/50 hover:border-line-strong hover:bg-fill-hover'
      )}
      onClick={onSelect}
      type="button"
    >
      <div className="relative aspect-video overflow-hidden rounded-xl bg-fill-trough">
        {cover.status === 'ready' ? (
          <img
            alt={t.coverAlt(item.title)}
            className="h-full w-full object-contain transition duration-200 group-hover:scale-[1.02]"
            src={cover.src}
          />
        ) : null}
        <div className="absolute left-2 top-2 flex flex-wrap gap-1">
          <Badge>{item.media_kind === 'video' ? t.mediaKindVideo : t.mediaKindImage}</Badge>
          {current && <Badge tone="accent">{t.currentMark}</Badge>}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <span className="min-w-0 flex-1 truncate text-xs font-semibold text-strong">{item.title}</span>
        <Badge>{assetKindLabel(item.asset_kind, t.assetKindLabels)}</Badge>
        {item.analysis_state && <Badge>{t.analysis.states[item.analysis_state]}</Badge>}
        {!analysisPending && !item.auto_playable && <Badge>{t.autoPlayOff}</Badge>}
      </div>
      {sourceSummary && <p className="truncate text-[11px] text-faint">{sourceSummary}</p>}
    </button>
  )
}

function VisualDetailPanel({
  detail,
  current,
  busy,
  onDelete,
  onRedo,
  t
}: {
  detail: VisualDetailData
  current: boolean
  busy: boolean
  onDelete: () => void
  onRedo: (feedback: string, videoFeedback: string) => void
  t: ReturnType<typeof useStrings>['visuals']
}): React.JSX.Element {
  const [feedback, setFeedback] = useState('')
  const [videoFeedback, setVideoFeedback] = useState('')
  const [showOriginal, setShowOriginal] = useState(false)
  const dict = useStrings()
  const deleted = t.frozenDeleted
  const original = showOriginal && detail.original_url ? detail.snapshot.source : null
  const size = `${original?.original_width ?? detail.width} × ${original?.original_height ?? detail.height}`
  const duration = formatDuration(original?.original_duration_ms ?? detail.duration_ms, t.seconds)
  const analysisPending = ['queued', 'running', 'paused'].includes(detail.analysis?.state ?? '')

  // 素材被删除后 ID 置空，冻结来源的名称与描述继续可读。
  const materialLabel = (materialId: string | null, frozenName: string): string =>
    materialId === null ? `${frozenName}（${deleted}）` : frozenName

  return (
    <div className="flex flex-col gap-3">
      <SettingCard divided={false}>
        <div className="grid gap-5 p-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(260px,0.75fr)]">
          <div className="min-w-0 space-y-3">
            <div className="overflow-hidden rounded-2xl border border-line-hairline bg-fill-trough">
              <MediaPreview
                type={detail.media_kind}
                url={showOriginal && detail.original_url ? detail.original_url : detail.media_url}
              >
                {({ failed, onError, onRetry, src, status }) =>
                  failed ? (
                    <ErrorBanner
                      message={dict.creation.previewFailed}
                      onRetry={onRetry}
                      retryLabel={dict.creation.retryLoad}
                    />
                  ) : status === 'loading' ? (
                    <LoadingBlock label={t.loading} />
                  ) : detail.media_kind === 'video' ? (
                    <video
                      className="max-h-[min(62vh,620px)] w-full"
                      controls
                      onError={onError}
                      preload="metadata"
                      src={src}
                    />
                  ) : (
                    <img
                      alt={detail.title}
                      className="max-h-[min(62vh,620px)] w-full object-contain"
                      onError={onError}
                      src={src}
                    />
                  )
                }
              </MediaPreview>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[11px] text-faint">{t.previewNote}</p>
              {detail.original_url && detail.original_url !== detail.media_url && (
                <button className={BTN_SUBTLE} onClick={() => setShowOriginal(value => !value)} type="button">
                  {showOriginal ? t.composer.processed : t.composer.original}
                </button>
              )}
            </div>
          </div>
          <div className="min-w-0 space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="min-w-0 flex-1 text-sm font-semibold text-strong">{detail.title}</h3>
              <Badge>{detail.media_kind === 'video' ? t.mediaKindVideo : t.mediaKindImage}</Badge>
              <Badge>{assetKindLabel(detail.asset_kind, t.assetKindLabels)}</Badge>
              {current && <Badge tone="accent">{t.currentMark}</Badge>}
              {!analysisPending && !detail.auto_playable && <Badge>{t.autoPlayOff}</Badge>}
            </div>
            {!analysisPending && !detail.auto_playable && (
              <div className="rounded-xl border border-line-hairline bg-fill-faint p-3 text-[11px] leading-relaxed text-faint">
                <p>{t.autoPlayHint}</p>
                {detail.snapshot.review_reason && (
                  <p className="mt-1">
                    {t.autoPlayReview}：{detail.snapshot.review_reason}
                  </p>
                )}
              </div>
            )}
            {detail.description && (
              <p className="whitespace-pre-wrap text-xs leading-relaxed text-muted">{detail.description}</p>
            )}
            <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted">
              <span>
                {t.sizeLabel}：{size}
              </span>
              {duration && (
                <span>
                  {t.durationLabel}：{duration}
                </span>
              )}
              {detail.jobs.length > 0 && (
                <span>
                  {t.statusLabel}：{detail.jobs.map(job => t.jobState[job.state] ?? job.state).join('、')}
                </span>
              )}
            </div>
            <div>
              <h4 className="mb-1 text-xs font-medium text-strong">{t.sourceTitle}</h4>
              {(() => {
                const source = detail.snapshot.source

                if (source.kind === 'analysis') {
                  return (
                    <div className="text-xs leading-relaxed text-muted">
                      <p>
                        {t.composer.original}：{source.original_filename}
                      </p>
                      <p>{t.composer.uploadHint}</p>
                    </div>
                  )
                }

                if (source.kind === 'scene') {
                  return (
                    <p className="text-[11px] text-muted">
                      {t.sourceScene}：{materialLabel(detail.scene_id, source.scene.title || '环境媒体')}
                    </p>
                  )
                }

                return (
                  <>
                    <p className="text-[11px] text-muted">
                      {t.sourceOutfit}：{materialLabel(detail.outfit_id, source.outfit.name)}
                    </p>
                    {source.kind === 'cinematic' && (
                      <p className="text-[11px] text-muted">
                        {t.sourceScene}：{materialLabel(detail.scene_id, source.scene.title)}
                      </p>
                    )}
                    <p className="text-[11px] text-muted">
                      {t.sourceAction}：{materialLabel(detail.action_id, source.action.name)}
                    </p>
                    <p className="whitespace-pre-wrap text-[11px] text-faint">{source.action.motion_description}</p>
                  </>
                )
              })()}
            </div>
            <div className="flex flex-wrap gap-2">
              {detail.original_url && (
                <button className={BTN_SUBTLE} disabled={busy} onClick={onDelete} type="button">
                  {t.delete}
                </button>
              )}
            </div>
            {detail.snapshot.source.kind !== 'analysis' && (
              <div className="space-y-2 border-t border-line-hairline pt-3">
                <p className="text-[11px] text-faint">{t.redoHint}</p>
                <label className="block">
                  <span className={FIELD_LABEL}>{t.feedbackLabel}</span>
                  <textarea
                    className={`${INPUT_CLASS} min-h-16`}
                    onChange={event => setFeedback(event.currentTarget.value)}
                    value={feedback}
                  />
                </label>
                {detail.media_kind === 'video' && (
                  <label className="block">
                    <span className={FIELD_LABEL}>{t.videoFeedbackLabel}</span>
                    <textarea
                      className={`${INPUT_CLASS} min-h-16`}
                      onChange={event => setVideoFeedback(event.currentTarget.value)}
                      value={videoFeedback}
                    />
                  </label>
                )}
                <button
                  className={BTN_PRIMARY}
                  disabled={busy}
                  onClick={() => onRedo(feedback, videoFeedback)}
                  type="button"
                >
                  {t.redoSubmit}
                </button>
              </div>
            )}
          </div>
        </div>
      </SettingCard>
      {detail.analysis && <VisualImportAnalysis visual={detail} />}
    </div>
  )
}
