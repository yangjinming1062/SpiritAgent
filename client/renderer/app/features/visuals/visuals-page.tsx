import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { LibraryToolbar } from '@/app/components/asset-library-header'
import { applyAppNavigation } from '@/app/navigation/app-navigation'
import { useDetailFlow } from '@/app/navigation/use-detail-flow'
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
import { usePanelAction } from '@/shared/hooks/use-panel-action'
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
  Pager,
  PanelSelect,
  PolicyToggleRow,
  SettingCard,
  SettingsContent,
  SettingsSectionIntro
} from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
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

/** 影像页：库内预览不产生播放回执。制作面板按需展开，列表是页面主体。 */
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
  const { run, busy } = usePanelAction()

  // 详情随组件生命周期；导航指向影像分区时才消费 visualId 直达，离开的导航不回退详情。
  const { detail: selectedId, openDetail, closeDetail: closeFlow, scrollRef } = useDetailFlow<string | null>({
    select: target =>
      target.page === 'companion' && target.companionSection === 'visuals' ? (target.visualId ?? null) : undefined,
    fallback: null,
    isDetailOpen: value => value !== null
  })

  const authenticated = auth.kind === 'authenticated'
  const detailVisible = selectedId !== null
  const composerOpen = ui.creation?.mode !== null && ui.creation?.mode !== undefined

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

  const detail = detailState?.id === selectedId ? detailState.detail : null
  const currentVisualId = projection?.current_visual?.id ?? null
  const activeJobs = jobs.filter(job => !['succeeded', 'cancelled'].includes(job.state))

  const sourceSummary = (item: VisualItem): string =>
    [item.outfit_name, item.scene_title, item.action_name].filter(Boolean).join(' · ')

  const submitFilters = (): void => {
    updateVisualsUi({ query: draftQuery.trim(), source: draftSource.trim(), page: 0 })
  }

  const openCompanion = (): void => {
    void run(
      async () => {
        const sessionId = await ensureCompanionSession()

        if (sessionId) {
          await applyAppNavigation({ page: 'chat', sessionId })
        }
      },
      { failure: t.contactCompanion }
    )
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
      run(async () => {
        await commandVisualJob(job.id, command)
        await Promise.all([reloadDetail(), refreshVisuals()])
      }, { failure: t.jobCommandFailed })

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

    void run(async () => {
      await regenerateVisual(detail.id, {
        expected_version: detail.version,
        feedback: feedback.trim() || undefined,
        video_feedback: videoFeedback.trim() || undefined
      })
      await Promise.all([reloadDetail(), refreshVisuals()])
    }, { failure: t.redoFailed })
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

      void run(async () => {
        await visualsApi.delete(detail.id)
        clearVisualDetail()
        // 列表随后由筛选与页码变化重新拉取，不在这里再发一次不同筛选的读取。
        closeFlow()
        updateVisualsUi({ page: 0 })
      }, { failure: t.deleteFailed })
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
          current={currentVisualId === selectedId}
          detail={detail}
          detailError={detailError}
          key={selectedId}
          onBack={closeDetail}
          onCommand={jobCommand}
          onDelete={deleteDetail}
          onRedo={redoDetail}
          onRetryLoad={() => void reloadDetail()}
        />
      ) : (
        <>
          <SettingsSectionIntro hint={t.intro} title={t.title} />
          <SettingCard className="mb-3" divided={false}>
            <div className="space-y-3 p-3">
              {composerOpen ? (
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
                />
              ) : null}
              <LibraryToolbar
                createLabel={t.composer.create}
                filters={
                  <>
                    <button
                      className={BTN_SUBTLE}
                      disabled={composerOpen && ui.creation?.mode === 'upload'}
                      onClick={() => openComposer('upload')}
                      type="button"
                    >
                      <ImagePlus aria-hidden="true" className="size-3.5" />
                      {t.composer.upload}
                    </button>
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
                query={draftQuery}
                searchLabel={t.searchAria}
                searchMode="submit"
              />
              {hasFilters ? (
                <div>
                  <button className={BTN_GHOST} onClick={clearFilters} type="button">
                    {t.clearFilters}
                  </button>
                </div>
              ) : null}
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
                  <button className={BTN_SUBTLE} onClick={openCompanion} type="button">
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

          <section className="mt-4">
            <h3 className="mb-2 text-xs font-medium text-muted">{t.prefsTitle}</h3>
            <SettingCard>
              <PolicyToggleRow
                ariaLabel={t.autonomousLabel}
                checked={projection?.autonomous_enabled ?? true}
                description={t.autonomousDesc}
                disabled={busy}
                label={t.autonomousLabel}
                onChange={() =>
                  void run(
                    () => setVisualPreferences({ autonomous_enabled: !(projection?.autonomous_enabled ?? true) }),
                    { failure: t.prefsFailed }
                  )
                }
              />
            </SettingCard>
          </section>
        </>
      )}
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
