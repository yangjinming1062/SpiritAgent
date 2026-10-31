import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { applyAppNavigation } from '@/app/navigation/app-navigation'
import { $outfits, hydrateWardrobe } from '@/modules/character'
import { ensureCompanionSession } from '@/modules/conversation'
import { useResolvedMediaSrc } from '@/modules/media'
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
  type VisualItem,
  type VisualJob,
  type VisualJobCommand,
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

import { emptyVisualCreation } from '../../../../../shared/visuals'

import { VisualCreatePanel } from './visual-create-panel'
import { VisualDetailView } from './visual-detail-view'
import { VisualJobRow } from './visual-job-row'
import type { VisualsCopy } from './visual-labels'
import { isPendingState, visualJobLabel } from './visual-labels'
import { PlayBadge } from './visual-play-badge'
import { $visualsUi, updateVisualsUi } from './visuals-state'

const PAGE_SIZE = 12

/** 影像页：库内预览不产生播放回执。 */
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
  const scrollRef = useRef<HTMLDivElement>(null)
  const listScrollTop = useRef(0)

  const authenticated = auth.kind === 'authenticated'
  const detailVisible = ui.detailOpen && ui.selectedId !== null

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

  // 详情是独立页：打开时回到顶部，返回列表时恢复离开前的滚动位置。
  useLayoutEffect(() => {
    const element = scrollRef.current

    if (!element) {
      return
    }

    element.scrollTop = detailVisible ? 0 : listScrollTop.current
  }, [detailVisible, ui.selectedId])

  const detail = detailState?.id === ui.selectedId ? detailState.detail : null
  const currentVisualId = projection?.current_visual?.id ?? null
  const activeJobs = jobs.filter(job => !['succeeded', 'cancelled'].includes(job.state))

  const sourceSummary = (item: VisualItem): string =>
    [item.outfit_name, item.scene_title, item.action_name].filter(Boolean).join(' · ')

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

  const rememberListScroll = (): void => {
    listScrollTop.current = scrollRef.current?.scrollTop ?? 0
  }

  const openDetail = (visualId: string): void => {
    rememberListScroll()
    updateVisualsUi({ selectedId: visualId, detailOpen: true })
  }

  const closeDetail = (): void => {
    clearVisualDetail()
    updateVisualsUi({ detailOpen: false })
  }

  const reloadDetail = async (): Promise<void> => {
    if (ui.selectedId !== null) {
      await loadVisualDetail(ui.selectedId).catch(error_ => log.warn('visuals', 'Detail reload failed', error_))
    }
  }

  const jobCommand = (job: VisualJob, command: VisualJobCommand): void => {
    const apply = async (): Promise<void> => {
      await commandVisualJob(job.id, command)
      await Promise.all([reloadDetail(), refreshVisuals()])
    }

    if (command === 'cancel') {
      void confirmAction({
        title: t.cancelTitle,
        description: visualJobLabel(job, t),
        confirmLabel: t.jobCancelConfirm,
        variant: 'destructive'
      }).then(confirmed => {
        if (confirmed) {
          run(apply, t.jobCommandFailed)
        }
      })

      return
    }

    run(apply, t.jobCommandFailed)
  }

  const redoDetail = (feedback: string, videoFeedback: string): void => {
    if (!detail) {
      return
    }

    run(async () => {
      await regenerateVisual(detail.id, {
        expected_version: detail.version,
        feedback: feedback.trim() || undefined,
        video_feedback: videoFeedback.trim() || undefined
      })
      await Promise.all([reloadDetail(), refreshVisuals()])
    }, t.redoFailed)
  }

  const deleteDetail = (): void => {
    if (!detail) {
      return
    }

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
        // 列表随后由筛选与页码变化重新拉取，不在这里再发一次不同筛选的读取。
        updateVisualsUi({ detailOpen: false, selectedId: null, page: 0 })
      }, t.deleteFailed)
    })
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
    <SettingsContent scrollRef={scrollRef}>
      {detailVisible ? (
        <VisualDetailView
          busy={busy}
          current={currentVisualId === ui.selectedId}
          detail={detail}
          detailError={detailError}
          key={ui.selectedId}
          onBack={closeDetail}
          onCommand={jobCommand}
          onDelete={deleteDetail}
          onRedo={redoDetail}
          onRetryLoad={() => void reloadDetail()}
        />
      ) : (
        <>
          <SettingsSectionIntro hint={t.intro} title={t.title} />
          <VisualCreatePanel
            onDone={async visualId => {
              rememberListScroll()
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
          <SettingCard className="mb-3" divided={false}>
            <div className="space-y-3 p-3">
              <div>
                <h3 className="text-xs font-semibold text-strong">{t.libraryTitle}</h3>
                <p className="mt-1 text-[11px] leading-relaxed text-muted">{t.libraryHint}</p>
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
            <ErrorBanner
              message={t.loadFailed}
              onRetry={() => void refreshVisuals({})}
              retryLabel={dict.common.retry}
            />
          )}

          {activeJobs.length > 0 && (
            <section className="mb-3">
              <h3 className="mb-2 text-xs font-medium text-muted">{t.jobsTitle}</h3>
              <SettingCard className="border border-accent-line bg-accent-soft/10" divided>
                {activeJobs.map(job => (
                  <VisualJobRow
                    busy={busy}
                    job={job}
                    key={job.id}
                    onCommand={command => jobCommand(job, command)}
                    t={t}
                  />
                ))}
              </SettingCard>
            </section>
          )}

          {loading && items.length === 0 ? (
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
              title={hasFilters ? t.emptySearch : t.emptyTitle}
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
                      onSelect={() => openDetail(item.id)}
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
                    updateVisualsUi({
                      page: ui.page + 1,
                      cursors: [...ui.cursors.slice(0, ui.page + 1), nextCursor]
                    })
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
        </>
      )}
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
  t: VisualsCopy
}): React.JSX.Element {
  const cover = useResolvedMediaSrc({
    type: 'image',
    url: item.poster_url || (item.media_kind === 'image' ? item.media_url : '')
  })

  const analysisPending = isPendingState(item.analysis_state)

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
        <Badge>{t.assetKindLabels[item.asset_kind]}</Badge>
        {item.analysis_state && <Badge>{t.analysis.states[item.analysis_state]}</Badge>}
        {!analysisPending && !item.auto_playable && <PlayBadge label={t.autoPlayOff} playable={false} />}
      </div>
      {sourceSummary && <p className="truncate text-[11px] text-faint">{sourceSummary}</p>}
    </button>
  )
}
