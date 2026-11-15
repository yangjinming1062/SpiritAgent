import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef } from 'react'

import { openCreation, openLife } from '@/app/navigation/app-navigation'
import { useNavigationDetail } from '@/app/navigation/navigation-detail'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import {
  BTN_GHOST,
  BTN_SUBTLE,
  CapsuleTabs,
  ErrorBanner,
  LoadingBlock,
  OverflowMenu,
  OverflowMenuAction,
  Pager
} from '@/ui'

import { isResourceId } from '../../../../../shared/resource-id'
import styles from '../life.module.css'
import { lifeSearch } from '../search'
import {
  $lifeDetail,
  $lifeDetailError,
  $lifeDetailLoading,
  $lifeEvents,
  $lifeEventsError,
  $lifeEventsHasMore,
  $lifeEventsLoading,
  $lifePursuits,
  $lifePursuitsError,
  $lifePursuitsHasMore,
  $lifePursuitsLoading,
  $lifePursuitsNextCursor,
  loadLifeDetail,
  loadLifeEvents,
  loadLifePursuits
} from '../store'
import { lifeTimeline } from '../timeline'

import { LifeNarrative } from './life-narrative'
import { LifeArchiveSearch } from './life-search'
import { $lifeUi, $lifeUiRestoreRevision, updateLifeUi } from './life-state'
import { useLife } from './use-life'

export function LifeOverviewPage({ visible = true }: { visible?: boolean }): React.JSX.Element {
  const revision = useLife(visible)
  const ui = useStore($lifeUi)
  const restoreRevision = useStore($lifeUiRestoreRevision)
  const pursuitCursor = ui.pursuitCursors[ui.pursuitPage]
  const pursuits = useStore($lifePursuits)
  const pursuitsHasMore = useStore($lifePursuitsHasMore)
  const pursuitsNextCursor = useStore($lifePursuitsNextCursor)
  const pursuitsLoading = useStore($lifePursuitsLoading)
  const pursuitsError = useStore($lifePursuitsError)
  const searchResult = useStore(lifeSearch.$data)
  const searchLoading = useStore(lifeSearch.$loading)
  const searchError = useStore(lifeSearch.$error)
  const events = useStore($lifeEvents)
  const eventsHasMore = useStore($lifeEventsHasMore)
  const eventsLoading = useStore($lifeEventsLoading)
  const eventsError = useStore($lifeEventsError)
  const detail = useStore($lifeDetail)
  const detailLoading = useStore($lifeDetailLoading)
  const detailError = useStore($lifeDetailError)
  const locale = useStore($locale)
  const strings = useStrings()
  const t = strings.life
  const root = useRef<HTMLDivElement>(null)
  const pendingScroll = useRef<number | null>(ui.scrollTop)
  const timeline = lifeTimeline(events)
  const formatter = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' })

  // 事项详情随组件生命周期；导航指向生活页时才消费 pursuitId 直达，离开本页的导航不回退详情。
  const [selectedId, setSelectedId] = useNavigationDetail<string | null>(
    target =>
      target.location.page === 'life'
        ? target.focus.page === 'life'
          ? (target.focus.pursuitId ?? null)
          : null
        : undefined,
    null
  )

  const selectPursuit = (id: string | null): void => {
    setSelectedId(id)
    pendingScroll.current = null
    updateLifeUi({ eventPage: 0, scrollTop: 0 })
    root.current?.scrollTo({ top: 0 })
  }

  const formatDate = (value: string): string =>
    Number.isNaN(Date.parse(value)) ? '' : formatter.format(new Date(value))

  const openResult = (kind: 'visual' | 'outfit' | 'scene' | 'action', id: unknown): void => {
    if (!isResourceId(id)) {
      return
    }

    void openCreation(
      kind === 'visual' ? 'visuals' : kind === 'outfit' ? 'wardrobe' : kind === 'scene' ? 'environment' : 'actions',
      kind === 'visual' ? { visualId: id } : kind === 'outfit' ? { outfitId: id } : { sceneId: id }
    )
  }

  useEffect(() => {
    if (visible) {
      void loadLifePursuits(ui.status, pursuitCursor)
    }
  }, [visible, revision, ui.status, pursuitCursor])

  useEffect(() => {
    if (visible) {
      void loadLifeDetail(selectedId)
    }
  }, [visible, revision, selectedId])

  useEffect(() => {
    if (visible && !ui.search.applied) {
      void loadLifeEvents(ui.eventPage, selectedId ?? undefined)
    }
  }, [visible, revision, ui.eventPage, selectedId, ui.search.applied])

  useLayoutEffect(() => {
    pendingScroll.current = $lifeUi.get().scrollTop
  }, [restoreRevision])

  // 请求 effect 先更新 loading；导入不同筛选时等待新内容，普通后台刷新不接管用户滚动。
  useEffect(() => {
    if (
      visible &&
      root.current &&
      pendingScroll.current !== null &&
      (!ui.search.applied || (!searchLoading && (searchResult !== null || searchError))) &&
      !$lifePursuitsLoading.get() &&
      !$lifeEventsLoading.get() &&
      !$lifeDetailLoading.get() &&
      !$lifePursuitsError.get() &&
      !$lifeEventsError.get() &&
      !$lifeDetailError.get()
    ) {
      root.current.scrollTop = pendingScroll.current
      pendingScroll.current = null
    }
  }, [
    visible,
    restoreRevision,
    pursuitsLoading,
    eventsLoading,
    searchLoading,
    searchError,
    searchResult,
    ui.search.applied,
    detailLoading,
    pursuitsError,
    eventsError,
    detailError,
    pursuits,
    events,
    detail
  ])

  const pagination = (
    page: number,
    hasMore: boolean,
    busy: boolean,
    onChange: (value: number) => void
  ): React.ReactNode => (
    <Pager busy={busy} className={styles.pagination} hasMore={hasMore} onPageChange={onChange} page={page} />
  )

  // 记录行内只留一个主要动作，其余收进溢出菜单；各入口的跳转语义不变。
  const timelineActions = (entry: (typeof timeline)[number]): Array<{ label: string; onClick: () => void }> => {
    const actions: Array<{ label: string; onClick: () => void }> = []

    if (entry.pursuitId && entry.pursuitId !== selectedId) {
      actions.push({ label: t.details, onClick: () => selectPursuit(entry.pursuitId) })
    }

    if (entry.visualId) {
      actions.push({ label: t.openVisual, onClick: () => openResult('visual', entry.visualId) })
    }

    if (entry.postId) {
      actions.push({ label: t.openPosts, onClick: () => void openLife('posts') })
    }

    if (entry.diaryId) {
      actions.push({ label: t.openDiary, onClick: () => void openLife('diary') })
    }

    return actions
  }

  return (
    <div
      className={styles.page}
      onScroll={event => {
        if (visible && pendingScroll.current === null) {
          updateLifeUi({ scrollTop: event.currentTarget.scrollTop })
        }
      }}
      ref={root}
    >
      <header className={styles.pageHeading}>
        <h1>{t.now}</h1>
        <p>{t.intro}</p>
      </header>
      <LifeNarrative />
      {selectedId !== null ? (
        <section className={styles.section}>
          <button className={BTN_GHOST} onClick={() => selectPursuit(null)} type="button">
            {t.back}
          </button>
          {detailError && (
            <ErrorBanner
              message={t.failed}
              onRetry={() => void loadLifeDetail(selectedId)}
              retryLabel={strings.common.retry}
            />
          )}
          {detailLoading && !detail ? (
            <LoadingBlock label={t.loading} />
          ) : detail?.id === selectedId ? (
            <article className={styles.detail}>
              <span className={styles.eyebrow}>{t.statuses[detail.status]}</span>
              <h2>{detail.narrative || t.pursuitFallback}</h2>
              <time dateTime={detail.started_at}>{formatDate(detail.started_at)}</time>
              {detail.status_changed_at && (
                <time dateTime={detail.status_changed_at}>
                  {t.statuses[detail.status]} · {formatDate(detail.status_changed_at)}
                </time>
              )}
              <h3>{t.stepsTitle}</h3>
              {detail.steps.map(step => (
                <div className={styles.detailField} key={step.id}>
                  <strong>{t.stepStates[step.state]}</strong>
                  {step.narrative && <p>{step.narrative}</p>}
                  {(
                    [
                      ['visual', step.result_ref.visual_id, t.openVisual],
                      ['outfit', step.result_ref.outfit_id, t.openOutfit],
                      ['scene', step.result_ref.scene_id, t.openScene],
                      ['action', step.result_ref.action_id, t.openAction]
                    ] as const
                  ).map(([kind, id, label]) =>
                    isResourceId(id) ? (
                      <button className={BTN_SUBTLE} key={kind} onClick={() => openResult(kind, id)} type="button">
                        {label}
                      </button>
                    ) : null
                  )}
                  {step.started_at && <time dateTime={step.started_at}>{formatDate(step.started_at)}</time>}
                  {step.reading_result && (
                    <details>
                      <summary>{t.readingNotes}</summary>
                      <p>{step.reading_result.notes}</p>
                      <ul>
                        {step.reading_result.sources.map(source => (
                          <li key={source.url}>
                            <a href={source.url} rel="noreferrer" target="_blank">
                              {source.title || source.url}
                            </a>{' '}
                            ·{' '}
                            {source.content_kind === 'summary'
                              ? t.sourceSummary
                              : source.excerpted
                                ? t.sourceExcerpt
                                : t.sourceText}
                            <time dateTime={source.retrieved_at}> · {formatDate(source.retrieved_at)}</time>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              ))}
            </article>
          ) : null}
        </section>
      ) : (
        <>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <CapsuleTabs
              ariaLabel={t.pursuitsTitle}
              onChange={status => updateLifeUi({ status, pursuitPage: 0, pursuitCursors: [null] })}
              options={(['active', 'paused', 'completed', 'abandoned', 'all'] as const).map(value => ({
                value,
                label: t.statuses[value]
              }))}
              size="sm"
              value={ui.status}
              variant="segmented"
            />
          </div>
          <section className={styles.section}>
            <h2>{t.pursuitsTitle}</h2>
            {pursuitsError && (
              <ErrorBanner
                message={t.failed}
                onRetry={() => void loadLifePursuits(ui.status, pursuitCursor)}
                retryLabel={strings.common.retry}
              />
            )}
            {pursuitsLoading && pursuits.length === 0 ? (
              <LoadingBlock label={t.loading} />
            ) : pursuits.length === 0 ? (
              !pursuitsError && <p className={styles.empty}>{t.emptyPursuits}</p>
            ) : (
              <div className={styles.pursuits}>
                {pursuits.map(pursuit => (
                  <button
                    className={styles.pursuit}
                    key={pursuit.id}
                    onClick={() => selectPursuit(pursuit.id)}
                    type="button"
                  >
                    <span className={styles.eyebrow}>{t.statuses[pursuit.status]}</span>
                    <strong>{pursuit.narrative || t.pursuitFallback}</strong>
                    <time dateTime={pursuit.updated_at}>{formatDate(pursuit.updated_at)}</time>
                  </button>
                ))}
              </div>
            )}
            {pagination(ui.pursuitPage, pursuitsHasMore, pursuitsLoading, pursuitPage =>
              updateLifeUi({
                pursuitPage,
                pursuitCursors:
                  pursuitPage > ui.pursuitPage && pursuitsNextCursor
                    ? [...ui.pursuitCursors.slice(0, ui.pursuitPage + 1), pursuitsNextCursor]
                    : ui.pursuitCursors
              })
            )}
          </section>
        </>
      )}
      <LifeArchiveSearch
        fallback={
          <>
            {eventsError && (
              <ErrorBanner
                message={t.failed}
                onRetry={() => void loadLifeEvents(ui.eventPage, selectedId ?? undefined)}
                retryLabel={strings.common.retry}
              />
            )}
            {eventsLoading && events.length === 0 ? (
              <LoadingBlock label={t.loading} />
            ) : (
              <ol className={styles.timeline}>
                {timeline.map(entry => {
                  const actions = timelineActions(entry)

                  return (
                    <li key={entry.id}>
                      <time dateTime={entry.occurredAt}>{formatDate(entry.occurredAt)}</time>
                      <div className={styles.eventBody}>
                        <span className={styles.eyebrow}>{t.eventLabels[entry.kind]}</span>
                        {entry.text && <p>{entry.text}</p>}
                        {actions.length > 0 && (
                          <div className={styles.actions}>
                            {actions.slice(0, 1).map(action => (
                              <button className={BTN_SUBTLE} key={action.label} onClick={action.onClick} type="button">
                                {action.label}
                              </button>
                            ))}
                            {actions.length > 1 && (
                              <OverflowMenu label={t.moreActions}>
                                {actions.slice(1).map(action => (
                                  <OverflowMenuAction
                                    key={action.label}
                                    label={action.label}
                                    onClick={action.onClick}
                                  />
                                ))}
                              </OverflowMenu>
                            )}
                          </div>
                        )}
                      </div>
                    </li>
                  )
                })}
                {!eventsLoading && !eventsError && timeline.length === 0 && (
                  <li className={styles.empty}>{t.emptyEvents}</li>
                )}
              </ol>
            )}
            {eventsHasMore && (
              <button
                className={BTN_SUBTLE}
                disabled={eventsLoading}
                onClick={() => updateLifeUi({ eventPage: ui.eventPage + 1 })}
                type="button"
              >
                {t.moreEvents}
              </button>
            )}
          </>
        }
        onSelectPursuit={selectPursuit}
        pursuitId={selectedId}
        revision={revision}
        visible={visible}
      />
    </div>
  )
}
