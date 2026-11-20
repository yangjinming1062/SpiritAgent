import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import { useDetailFlow } from '@/app/navigation/use-detail-flow'
import { useResolvedMediaSrc } from '@/features/media'
import { usePanelAction } from '@/shared/hooks/use-panel-action'
import { ImagePlus, Sparkles } from '@/shared/lib/icons'
import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'
import {
  Badge,
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SUBTLE,
  CapsuleTabs,
  confirmAction,
  EmptyState,
  LibraryStatusBanner,
  LibraryToolbar,
  LoadingBlock,
  Pager,
  SettingsContent,
  StatusToggle
} from '@/ui'

import { showVisualJobActivity } from '../../../../../shared/visuals'
import { $actions, refreshActions } from '../actions'
import { $sceneLibrary, loadSceneLibrary } from '../environment'
import { $outfits, hydrateWardrobe } from '../wardrobe'

import { visualsApi } from './api'
import {
  clearVisualDetail,
  commandVisualJob,
  loadVisualDetail,
  refreshVisualPresentation,
  refreshVisuals,
  regenerateVisual,
  setVisualPreferences
} from './library'
import {
  $visualsDetail,
  $visualsDetailError,
  $visualsError,
  $visualsItems,
  $visualsJobs,
  $visualsLoading,
  $visualsNextCursor,
  $visualsPresentation,
  $visualsTotal
} from './state'
import { type VisualItem, type VisualJob, type VisualJobCommand } from './types'
import { VisualCreatePanel } from './visual-create-panel'
import { VisualDetailView } from './visual-detail-view'
import { VisualJobRow } from './visual-job-row'
import type { VisualsCopy } from './visual-labels'
import { isPendingState, visualJobLabel } from './visual-labels'
import { PlayBadge } from './visual-play-badge'
import { $visualsUi, updateStudio, updateVisualsUi } from './visuals-state'

const PAGE_SIZE = 12

/**
 * 影像页：库视图是页面主体（命令栏 → 状态条 → 网格 → 分页），制作与上传是专注视图，
 * 详情单独成视图。库内预览不产生播放回执。
 */
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
  const [queueOpen, setQueueOpen] = useState(false)
  const { run, busy } = usePanelAction()

  // 详情随组件生命周期；导航指向影像分区时才消费 visualId 直达，离开的导航不回退详情。
  const {
    detail: selectedId,
    openDetail,
    closeDetail: closeFlow,
    scrollRef
  } = useDetailFlow<string | null>({
    select: target =>
      target.location.page === 'creation' && target.location.section === 'visuals'
        ? target.focus.page === 'creation'
          ? (target.focus.visualId ?? null)
          : null
        : undefined,
    fallback: null,
    isDetailOpen: value => value !== null
  })

  const authenticated = auth.kind === 'authenticated'
  const detailVisible = selectedId !== null
  const composerOpen = (ui.studio?.mode ?? 'idle') !== 'idle'

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
    if (detailVisible && selectedId !== null) {
      void loadVisualDetail(selectedId).catch(error_ => log.warn('visuals', 'Detail load failed', error_))
    }
  }, [detailVisible, selectedId])

  // 库视图与制作视图共用同一个滚动容器：进制作前记住列表位置，退出制作回库时恢复。
  const libraryScroll = useRef(0)
  const previousComposerOpen = useRef(false)

  useLayoutEffect(() => {
    if (composerOpen === previousComposerOpen.current) {
      return
    }

    previousComposerOpen.current = composerOpen

    if (composerOpen) {
      libraryScroll.current = scrollRef.current?.scrollTop ?? 0
      scrollRef.current?.scrollTo({ top: 0 })

      return
    }

    // 制作完成会直接进详情，此时滚动位置归详情视图管，不回退列表位置。
    if (!detailVisible) {
      scrollRef.current?.scrollTo({ top: libraryScroll.current })
    }
  }, [composerOpen, detailVisible, scrollRef])

  const detail = detailState?.id === selectedId ? detailState.detail : null
  const currentVisualId = projection?.current_visual?.id ?? null
  const defaultVisualId = projection?.default_wallpaper?.visual.id ?? null
  const activeJobs = jobs.filter(showVisualJobActivity)

  const attentionJobs = activeJobs.filter(job => job.state === 'failed' || job.state === 'unknown')
  const workingJobs = activeJobs.filter(job => job.stage !== 'review_pending' && !attentionJobs.includes(job))

  const submitFilters = (): void => {
    updateVisualsUi({ query: draftQuery.trim(), source: draftSource.trim(), page: 0 })
  }

  const closeDetail = (): void => {
    clearVisualDetail()
    closeFlow()
  }

  const reloadDetail = async (): Promise<void> => {
    if (selectedId !== null) {
      await loadVisualDetail(selectedId).catch(error_ => log.warn('visuals', 'Detail reload failed', error_))
    }
  }

  const jobCommand = (job: VisualJob, command: VisualJobCommand): void => {
    const apply = (): Promise<void> =>
      run(
        async () => {
          await commandVisualJob(job.id, command)
          await Promise.all([reloadDetail(), refreshVisuals()])
        },
        { failure: t.jobCommandFailed }
      )

    if (command === 'cancel') {
      void confirmAction({
        title: t.cancelTitle,
        description: visualJobLabel(job, t),
        confirmLabel: t.jobCancelConfirm,
        variant: 'destructive'
      }).then(confirmed => {
        if (confirmed) {
          void apply()
        }
      })

      return
    }

    void apply()
  }

  const redoDetail = (feedback: string, videoFeedback: string): void => {
    if (!detail) {
      return
    }

    void run(
      async () => {
        await regenerateVisual(detail.id, {
          expected_version: detail.version,
          feedback: feedback.trim() || undefined,
          video_feedback: videoFeedback.trim() || undefined
        })
        await Promise.all([reloadDetail(), refreshVisuals()])
      },
      { failure: t.redoFailed }
    )
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

      void run(
        async () => {
          await visualsApi.delete(detail.id)
          clearVisualDetail()
          // 列表随后由筛选与页码变化重新拉取，不在这里再发一次不同筛选的读取。
          closeFlow()
          updateVisualsUi({ page: 0 })
        },
        { failure: t.deleteFailed }
      )
    })
  }

  const setDefaultWallpaper = (): void => {
    if (!detail) {
      return
    }

    const clearing = defaultVisualId === detail.id

    void run(() => setVisualPreferences({ default_visual_id: clearing ? null : detail.id }), {
      failure: t.defaultWallpaperFailed,
      success: clearing ? t.defaultWallpaperCleared : t.defaultWallpaperSet
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
    updateStudio({ mode: mode === 'upload' ? 'upload' : 'describe' })
  }

  if (detailVisible) {
    return (
      <SettingsContent scrollRef={scrollRef}>
        <VisualDetailView
          busy={busy}
          current={currentVisualId === selectedId}
          defaultWallpaper={defaultVisualId === selectedId}
          detail={detail}
          detailError={detailError}
          key={selectedId}
          onBack={closeDetail}
          onCommand={jobCommand}
          onDelete={deleteDetail}
          onRedo={redoDetail}
          onRetryLoad={() => void reloadDetail()}
          onSetDefaultWallpaper={setDefaultWallpaper}
        />
      </SettingsContent>
    )
  }

  if (composerOpen) {
    return (
      <SettingsContent scrollRef={scrollRef}>
        <VisualCreatePanel
          onDone={async visualId => {
            setDraftQuery('')
            setDraftSource('')
            updateVisualsUi({
              query: '',
              source: '',
              mediaKind: 'all',
              page: 0
            })

            if (visualId) {
              openDetail(visualId)
            }

            await refreshVisuals({ limit: PAGE_SIZE, include_total: true })
          }}
          onExit={() => updateStudio({ mode: 'idle' })}
        />
      </SettingsContent>
    )
  }

  return (
    <SettingsContent scrollRef={scrollRef}>
      <div className="space-y-3">
        <LibraryToolbar
          createLabel={t.composer.create}
          filters={
            <>
              <CapsuleTabs
                ariaLabel={t.mediaKindLabel}
                onChange={value => updateVisualsUi({ mediaKind: value, page: 0 })}
                options={[
                  { value: 'all' as const, label: t.mediaKindAll },
                  { value: 'image' as const, label: t.mediaKindImage },
                  { value: 'video' as const, label: t.mediaKindVideo }
                ]}
                size="sm"
                value={ui.mediaKind}
                variant="segmented"
              />
              <SearchSourceField
                label={t.sourceLabel}
                onChange={setDraftSource}
                onSubmit={submitFilters}
                placeholder={t.sourcePlaceholder}
                value={draftSource}
              />
            </>
          }
          onCreate={() => openComposer('create')}
          onQueryChange={setDraftQuery}
          onSearch={submitFilters}
          preference={
            <StatusToggle
              checked={projection?.autonomous_enabled ?? true}
              disabled={busy}
              label={t.autonomousShort}
              onClick={() =>
                void run(
                  () => setVisualPreferences({ autonomous_enabled: !(projection?.autonomous_enabled ?? true) }),
                  { failure: t.prefsFailed }
                )
              }
              title={`${t.autonomousLabel}\n${t.autonomousDesc}`}
            />
          }
          query={draftQuery}
          searchLabel={t.searchAria}
          searchMode="submit"
          secondary={
            <>
              <button className={BTN_SUBTLE} onClick={() => openComposer('upload')} type="button">
                <ImagePlus aria-hidden="true" className="size-3.5" />
                {t.composer.upload}
              </button>
              {activeJobs.length > 0 ? (
                <button
                  aria-expanded={queueOpen}
                  className={BTN_GHOST}
                  onClick={() => setQueueOpen(value => !value)}
                  type="button"
                >
                  {queueOpen ? t.jobsHide : t.jobsShow}
                </button>
              ) : null}
              {hasFilters ? (
                <button className={BTN_GHOST} onClick={clearFilters} type="button">
                  {t.clearFilters}
                </button>
              ) : null}
            </>
          }
        />

        <LibraryStatusBanner
          candidates={[
            error
              ? {
                  state: 'error',
                  title: t.loadFailed,
                  actions: (
                    <button className={BTN_SUBTLE} onClick={() => void refreshVisuals({})} type="button">
                      {dict.common.retry}
                    </button>
                  )
                }
              : null,
            attentionJobs.length > 0
              ? {
                  state: 'error',
                  title: t.jobsTitle,
                  description: attentionJobs.map(job => visualJobLabel(job, t)).join(' · ')
                }
              : null,
            workingJobs.length > 0
              ? {
                  state: workingJobs.some(job => job.state === 'paused') ? 'paused' : 'working',
                  title: t.jobsTitle,
                  description: workingJobs.map(job => visualJobLabel(job, t)).join(' · ')
                }
              : null
          ]}
        />

        {queueOpen && activeJobs.length > 0 && (
          <section className="rounded-xl border border-line-hairline bg-fill-faint/40">
            {activeJobs.map(job => (
              <VisualJobRow busy={busy} job={job} key={job.id} onCommand={command => jobCommand(job, command)} t={t} />
            ))}
          </section>
        )}

        {loading && items.length === 0 ? (
          <LoadingBlock label={t.loading} />
        ) : items.length === 0 ? (
          <EmptyState
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <button className={BTN_PRIMARY} onClick={() => openComposer('create')} type="button">
                  <Sparkles aria-hidden="true" className="size-3.5" />
                  {t.composer.create}
                </button>
                <button className={BTN_SUBTLE} onClick={() => openComposer('upload')} type="button">
                  <ImagePlus aria-hidden="true" className="size-3.5" />
                  {t.composer.upload}
                </button>
              </div>
            }
            description={`${t.emptyHint}${readiness ? ` ${readiness}` : ''}`}
            title={hasFilters ? t.emptySearch : t.emptyTitle}
          />
        ) : (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {items.map(item => (
                <VisualCard
                  current={currentVisualId === item.id}
                  defaultWallpaper={defaultVisualId === item.id}
                  item={item}
                  key={item.id}
                  onSelect={() => openDetail(item.id)}
                  t={t}
                />
              ))}
            </div>
            <Pager
              busy={busy}
              hasMore={Boolean(nextCursor)}
              onPageChange={next =>
                next > ui.page &&
                nextCursor &&
                updateVisualsUi({
                  page: next,
                  cursors: [...ui.cursors.slice(0, ui.page + 1), nextCursor]
                })
              }
              page={ui.page}
              total={total}
            />
          </>
        )}
      </div>
    </SettingsContent>
  )
}

/** 来源筛选是提交式的次级条件，用小输入框呈现，不与主搜索争宽度。 */
function SearchSourceField({
  label,
  placeholder,
  value,
  onChange,
  onSubmit
}: {
  label: string
  placeholder?: string
  value: string
  onChange: (next: string) => void
  onSubmit: () => void
}): React.JSX.Element {
  return (
    <input
      aria-label={label}
      className="h-8 w-36 rounded-lg border border-line-standard bg-fill-faint px-3 text-xs text-strong transition placeholder:text-faint hover:bg-fill-hover focus-visible:outline-2 focus-visible:outline-accent"
      onBlur={onSubmit}
      onChange={event => onChange(event.currentTarget.value)}
      onKeyDown={event => {
        if (event.key === 'Enter') {
          onSubmit()
        }
      }}
      placeholder={placeholder}
      type="search"
      value={value}
    />
  )
}

/**
 * 库卡片只承载浏览决策：封面 + 标题 + 至多两个状态标记；
 * 媒体类型、资产类型、版本与来源事实都在详情里看。
 */
function VisualCard({
  item,
  current,
  defaultWallpaper,
  onSelect,
  t
}: {
  item: VisualItem
  current: boolean
  defaultWallpaper: boolean
  onSelect: () => void
  t: VisualsCopy
}): React.JSX.Element {
  const cover = useResolvedMediaSrc({
    type: 'image',
    url: item.poster_url || (item.media_kind === 'image' ? item.media_url : '')
  })

  const analysisPending = isPendingState(item.analysis_state)
  const marks: React.JSX.Element[] = []

  if (current) {
    marks.push(<Badge tone="accent">{t.currentMark}</Badge>)
  }

  if (defaultWallpaper) {
    marks.push(<Badge tone="accent">{t.defaultWallpaperMark}</Badge>)
  }

  if (marks.length < 2 && analysisPending) {
    marks.push(<Badge>{t.analysis.states[item.analysis_state ?? 'queued']}</Badge>)
  }

  if (marks.length < 2 && !analysisPending && !item.auto_playable) {
    marks.push(<PlayBadge label={t.autoPlayOff} playable={false} />)
  }

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
      <div
        className={cn(
          'relative overflow-hidden rounded-xl bg-fill-trough',
          item.height > item.width ? 'aspect-[4/5]' : 'aspect-video'
        )}
      >
        {cover.status === 'ready' ? (
          <img
            alt={t.coverAlt(item.title)}
            className="h-full w-full object-contain transition duration-200 group-hover:scale-[1.02]"
            src={cover.src}
          />
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-1">
        <span className="min-w-0 flex-1 truncate text-xs font-semibold text-strong">{item.title}</span>
        {marks.slice(0, 2)}
      </div>
    </button>
  )
}
