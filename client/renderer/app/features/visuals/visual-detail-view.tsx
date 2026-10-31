import type React from 'react'
import { useEffect, useState } from 'react'

import { MediaPreview, useResolvedMediaSrc } from '@/modules/media'
import { loadVisualDetail, type VisualDetail, type VisualJob, type VisualJobCommand } from '@/modules/visuals'
import { PortraitLightbox } from '@/shared'
import { ArrowLeft, ZoomIn } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import {
  Badge,
  BTN_PRIMARY,
  BTN_SUBTLE,
  Disclosure,
  ErrorBanner,
  ExpandableText,
  FIELD_LABEL,
  INPUT_CLASS,
  LoadingBlock,
  SettingCard
} from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { VisualImportAnalysis } from './visual-import-analysis'
import { VisualJobRow } from './visual-job-row'
import { formatDuration, isAdvancingState, isPendingState } from './visual-labels'
import { PlayBadge } from './visual-play-badge'
import { VISUAL_BODY, VISUAL_SECTION } from './visual-text'

export function VisualDetailView({
  busy,
  current,
  detail,
  detailError,
  onBack,
  onCommand,
  onDelete,
  onRedo,
  onRetryLoad
}: {
  busy: boolean
  current: boolean
  detail: VisualDetail | null
  detailError: string | null
  onBack: () => void
  onCommand: (job: VisualJob, command: VisualJobCommand) => void
  onDelete: () => void
  onRedo: (feedback: string, videoFeedback: string) => void
  onRetryLoad: () => void
}): React.JSX.Element {
  const dict = useStrings()
  const t = dict.visuals
  const visualId = detail?.id ?? null

  const shouldPoll = Boolean(
    detail && (detail.jobs.some(job => isAdvancingState(job.state)) || isAdvancingState(detail.analysis?.state))
  )

  // 制作或分析在推进时按可见性轮询详情；同一 ID 的在途读取由 store 合并，失败只保留上一份详情。
  useEffect(() => {
    if (!visualId || !shouldPoll) {
      return
    }

    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadVisualDetail(visualId).catch(error_ => log.warn('visuals', 'Detail poll failed', error_))
      }
    }, 3000)

    return () => window.clearInterval(timer)
  }, [shouldPoll, visualId])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <button className={BTN_SUBTLE} onClick={onBack} type="button">
          <ArrowLeft aria-hidden="true" className="size-3.5" />
          <span>{t.backToList}</span>
        </button>
        {detail?.original_url ? (
          <button className={cn(BTN_SUBTLE, 'ml-auto text-danger-fg')} disabled={busy} onClick={onDelete} type="button">
            {t.delete}
          </button>
        ) : null}
      </div>
      {/* 已读到的详情优先于错误提示：轮询或后台刷新失败不打断阅读。 */}
      {detail ? (
        <VisualDetailContent busy={busy} current={current} detail={detail} onCommand={onCommand} onRedo={onRedo} />
      ) : detailError ? (
        <ErrorBanner message={t.detailLoadFailed} onRetry={onRetryLoad} retryLabel={dict.common.retry} />
      ) : (
        <LoadingBlock label={t.loading} />
      )}
    </div>
  )
}

function VisualDetailContent({
  busy,
  current,
  detail,
  onCommand,
  onRedo
}: {
  busy: boolean
  current: boolean
  detail: VisualDetail
  onCommand: (job: VisualJob, command: VisualJobCommand) => void
  onRedo: (feedback: string, videoFeedback: string) => void
}): React.JSX.Element {
  const dict = useStrings()
  const t = dict.visuals
  const [feedback, setFeedback] = useState('')
  const [videoFeedback, setVideoFeedback] = useState('')
  const [showOriginal, setShowOriginal] = useState(false)
  const [zoom, setZoom] = useState(false)

  const source = detail.snapshot.source
  const analysisPending = isPendingState(detail.analysis?.state)
  const canCompare = Boolean(detail.original_url && detail.original_url !== detail.media_url)
  const originalSource = showOriginal && detail.original_url ? source : null
  const mediaUrl = showOriginal && detail.original_url ? detail.original_url : detail.media_url

  const preview = useResolvedMediaSrc({
    type: 'image',
    url: detail.media_kind === 'image' ? mediaUrl : ''
  })

  const poster = useResolvedMediaSrc({ type: 'image', url: detail.poster_url ?? '' })
  const size = `${originalSource?.original_width ?? detail.width} × ${originalSource?.original_height ?? detail.height}`
  const duration = formatDuration(originalSource?.original_duration_ms ?? detail.duration_ms, t.seconds)

  const targetDiffers = detail.snapshot.target_width !== detail.width || detail.snapshot.target_height !== detail.height

  const meta = [
    detail.media_kind === 'video' ? t.mediaKindVideo : t.mediaKindImage,
    t.assetKindLabels[detail.asset_kind],
    size,
    duration,
    t.versionMark(detail.version),
    targetDiffers ? t.targetSize(detail.snapshot.target_width, detail.snapshot.target_height) : null
  ]
    .filter(Boolean)
    .join(' · ')

  const actionMeta =
    source.kind === 'actor' || source.kind === 'cinematic'
      ? `${source.action.clip_kind === 'loop' ? t.clipLoop : t.clipOnce} · ${t.seconds(source.action.duration_seconds)}`
      : null

  // 素材被删除后 ID 置空，冻结来源的名称继续可读，只补一个已删除标记。
  const materialValue = (materialId: string | null, frozenName: string): React.ReactNode =>
    materialId === null ? (
      <>
        {frozenName}
        <Badge className="ml-1.5 align-middle">{t.deletedMark}</Badge>
      </>
    ) : (
      frozenName
    )

  return (
    <>
      <div>
        <div className="flex items-start gap-2">
          <h2 className="min-w-0 break-words text-[15px] font-semibold leading-6 text-strong">{detail.title}</h2>
          {current ? (
            <Badge className="mt-1 shrink-0" tone="accent">
              {t.currentMark}
            </Badge>
          ) : null}
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-muted">{meta}</p>
      </div>

      <SettingCard divided={false}>
        <div className="relative bg-fill-trough">
          <MediaPreview type={detail.media_kind} url={mediaUrl}>
            {({ failed, onError, onRetry, src, status }) =>
              failed ? (
                <div className="px-4 py-6">
                  <ErrorBanner
                    message={dict.creation.previewFailed}
                    onRetry={onRetry}
                    retryLabel={dict.creation.retryLoad}
                  />
                </div>
              ) : status === 'loading' ? (
                <div className="grid min-h-[220px] place-items-center">
                  <LoadingBlock label={t.loading} />
                </div>
              ) : detail.media_kind === 'video' ? (
                <video
                  className="mx-auto block max-h-[min(62vh,640px)] w-full"
                  controls
                  onError={onError}
                  poster={poster.status === 'ready' ? poster.src : undefined}
                  preload="metadata"
                  src={src}
                />
              ) : (
                <button
                  aria-label={t.zoomImage}
                  className="group block w-full cursor-zoom-in"
                  onClick={() => {
                    if (preview.status === 'ready') {
                      setZoom(true)
                    }
                  }}
                  type="button"
                >
                  <span className="relative mx-auto block w-fit max-w-full">
                    <img
                      alt={detail.title}
                      className="block max-h-[min(62vh,640px)] w-auto max-w-full object-contain"
                      onError={onError}
                      src={src}
                    />
                    <span className="absolute bottom-2 right-2 rounded-full bg-black/50 p-1.5 text-white opacity-80 transition group-hover:opacity-100">
                      <ZoomIn aria-hidden="true" className="size-4" />
                    </span>
                  </span>
                </button>
              )
            }
          </MediaPreview>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-hairline px-4 py-2.5">
          <p className="text-[11px] text-faint">{t.previewNote}</p>
          {canCompare ? (
            <button
              aria-pressed={showOriginal}
              className={BTN_SUBTLE}
              onClick={() => setShowOriginal(value => !value)}
              type="button"
            >
              {showOriginal ? t.showProcessed : t.showOriginal}
            </button>
          ) : null}
        </div>
      </SettingCard>

      <SettingCard divided={false}>
        <div className="space-y-5 p-4">
          {detail.description ? (
            <FactSection title={t.descriptionTitle}>
              <ExpandableText
                action="button"
                className={VISUAL_BODY}
                collapseLabel={t.collapse}
                expandLabel={t.expand}
                lines={4}
                text={detail.description}
              />
            </FactSection>
          ) : null}
          <div className={cn('grid gap-5', !analysisPending && 'md:grid-cols-2')}>
            {!analysisPending ? (
              <FactSection title={t.eligibilityTitle}>
                <div className="space-y-2">
                  <PlayBadge
                    label={detail.auto_playable ? t.autoPlayOn : t.autoPlayOff}
                    playable={detail.auto_playable}
                  />
                  <p className={VISUAL_BODY}>{detail.auto_playable ? t.analysis.approved : t.autoPlayHint}</p>
                  {!detail.auto_playable && detail.snapshot.review_reason ? (
                    <Disclosure chevron title={t.autoPlayReview} variant="inline">
                      <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-muted">
                        {detail.snapshot.review_reason}
                      </p>
                    </Disclosure>
                  ) : null}
                </div>
              </FactSection>
            ) : null}
            <FactSection title={t.sourceTitle}>
              <div className="rounded-xl border border-line-hairline bg-fill-faint p-3">
                {source.kind === 'analysis' ? (
                  <dl className="space-y-2">
                    <SourceRow label={t.composer.original} value={source.original_filename} />
                  </dl>
                ) : source.kind === 'scene' ? (
                  <dl className="space-y-2">
                    <SourceRow
                      label={t.sourceScene}
                      value={materialValue(detail.scene_id, source.scene.title || t.untitledScene)}
                    />
                  </dl>
                ) : (
                  <>
                    <dl className="space-y-2">
                      <SourceRow label={t.sourceOutfit} value={materialValue(detail.outfit_id, source.outfit.name)} />
                      {source.kind === 'cinematic' ? (
                        <SourceRow
                          label={t.sourceScene}
                          value={materialValue(detail.scene_id, source.scene.title || t.untitledScene)}
                        />
                      ) : null}
                      <SourceRow
                        label={t.sourceAction}
                        value={
                          <>
                            {materialValue(detail.action_id, source.action.name)}
                            {actionMeta ? <span className="ml-1.5 text-[11px] text-faint">{actionMeta}</span> : null}
                          </>
                        }
                      />
                    </dl>
                    {source.action.motion_description ? (
                      <p className="mt-3 whitespace-pre-wrap border-t border-line-hairline pt-2 text-[12px] leading-relaxed text-faint">
                        {source.action.motion_description}
                      </p>
                    ) : null}
                  </>
                )}
              </div>
            </FactSection>
          </div>
        </div>
      </SettingCard>

      {detail.jobs.length > 0 ? (
        <FactSection title={t.jobsHistoryTitle}>
          <SettingCard divided>
            {detail.jobs.map(job => (
              <VisualJobRow busy={busy} job={job} key={job.id} onCommand={command => onCommand(job, command)} t={t} />
            ))}
          </SettingCard>
        </FactSection>
      ) : null}

      {source.kind !== 'analysis' ? (
        <Disclosure hint={t.redoHint} title={t.redoTitle} variant="panel">
          <div className="space-y-2">
            <label className="block">
              <span className={FIELD_LABEL}>{t.feedbackLabel}</span>
              <textarea
                className={`${INPUT_CLASS} min-h-16`}
                onChange={event => setFeedback(event.currentTarget.value)}
                value={feedback}
              />
            </label>
            {detail.media_kind === 'video' ? (
              <label className="block">
                <span className={FIELD_LABEL}>{t.videoFeedbackLabel}</span>
                <textarea
                  className={`${INPUT_CLASS} min-h-16`}
                  onChange={event => setVideoFeedback(event.currentTarget.value)}
                  value={videoFeedback}
                />
              </label>
            ) : null}
            <button
              className={BTN_PRIMARY}
              disabled={busy}
              onClick={() => onRedo(feedback, videoFeedback)}
              type="button"
            >
              {t.redoSubmit}
            </button>
          </div>
        </Disclosure>
      ) : null}

      {detail.analysis ? <VisualImportAnalysis visual={detail} /> : null}

      {zoom && preview.status === 'ready' ? (
        <PortraitLightbox name={detail.title} onClose={() => setZoom(false)} url={preview.src} />
      ) : null}
    </>
  )
}

function FactSection({ title, children }: { title: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <section className="min-w-0">
      <div className="flex items-center gap-2 border-b border-line-hairline pb-1.5">
        <span aria-hidden="true" className="h-3.5 w-0.5 rounded-full bg-accent" />
        <h3 className={VISUAL_SECTION}>{title}</h3>
      </div>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function SourceRow({ label, value }: { label: string; value: React.ReactNode }): React.JSX.Element {
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-3">
      <dt className="w-20 shrink-0 text-[12px] text-faint">{label}</dt>
      <dd className="min-w-0 flex-1 break-words text-[12px] leading-relaxed text-strong">{value}</dd>
    </div>
  )
}
