import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef } from 'react'

import { useNavigationDetail } from '@/app/navigation/navigation-detail'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import { BTN_GHOST, BTN_SUBTLE, CapsuleTabs, ErrorBanner, LoadingBlock, Pager } from '@/ui'

import { lifeStepStatus } from '../../../../../shared/life-trajectory'
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
  $lifeState,
  loadLifeDetail,
  loadLifeEvents,
  loadLifePursuits
} from '../store'

import { LifeNarrative } from './life-narrative'
import { LifeArchiveSearch } from './life-search'
import { $lifeUi, $lifeUiRestoreRevision, updateLifeUi } from './life-state'
import { LifeReadingNotes, LifeResultLinks, LifeTrajectory } from './life-trajectory'
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
  const latest = useStore($lifeState)?.latest_episode
  const locale = useStore($locale)
  const strings = useStrings()
  const t = strings.life
  const root = useRef<HTMLDivElement>(null)
  const pendingScroll = useRef<number | null>(ui.scrollTop)

  const [selectedId, setSelectedId] = useNavigationDetail<string | null>(
    target =>
      target.location.page === 'life'
        ? target.focus.page === 'life'
          ? (target.focus.pursuitId ?? null)
          : null
        : undefined,
    null
  )

  const topicId = selectedId === null ? (ui.topicId ?? null) : null
  const home = selectedId === null && topicId === null && !ui.search.applied
  const entries = home ? events.filter(entry => entry.id !== latest?.id) : events
  const formatter = new Intl.DateTimeFormat(locale, { dateStyle: 'medium' })

  const selectPursuit = (id: string | null): void => {
    setSelectedId(id)
    pendingScroll.current = null
    updateLifeUi({ eventPage: 0, topicId: null, scrollTop: 0 })
    root.current?.scrollTo({ top: 0 })
  }

  const selectTopic = (id: string | null): void => {
    setSelectedId(null)
    pendingScroll.current = null
    updateLifeUi({ topicId: id, eventPage: 0, scrollTop: 0 })
    root.current?.scrollTo({ top: 0 })
  }

  const loadTrajectory = (): Promise<void> =>
    loadLifeEvents(ui.eventPage, selectedId ?? undefined, topicId ?? undefined)

  useEffect(() => {
    if (visible && selectedId === null) {
      void loadLifePursuits(ui.status, pursuitCursor)
    }
  }, [visible, revision, ui.status, pursuitCursor, selectedId])

  useEffect(() => {
    if (visible) {
      void loadLifeDetail(selectedId)
    }
  }, [visible, revision, selectedId])

  useEffect(() => {
    if (visible && !ui.search.applied) {
      void loadLifeEvents(ui.eventPage, selectedId ?? undefined, topicId ?? undefined)
    }
  }, [visible, revision, ui.eventPage, selectedId, topicId, ui.search.applied])

  useLayoutEffect(() => {
    pendingScroll.current = $lifeUi.get().scrollTop
  }, [restoreRevision])

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
      {home && <LifeNarrative onPursuit={selectPursuit} onTopic={selectTopic} />}
      {selectedId !== null && (
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
              <h2>{detail.title || t.pursuitFallback}</h2>
              {detail.narrative && <p>{detail.narrative}</p>}
              <time dateTime={detail.started_at}>{formatter.format(new Date(detail.started_at))}</time>
              {detail.steps.length > 0 && (
                <details className={styles.reading}>
                  <summary>{t.stepsTitle}</summary>
                  {detail.steps.map(step => (
                    <div className={styles.detailField} key={step.id}>
                      <strong>{t.stepStates[lifeStepStatus(step)]}</strong>
                      {step.narrative && step.disposition !== 'waiting' && <p>{step.narrative}</p>}
                      <div className={styles.actions}>
                        <LifeResultLinks reference={step.result_ref} />
                      </div>
                      {step.reading_result && <LifeReadingNotes reading={step.reading_result} />}
                    </div>
                  ))}
                </details>
              )}
            </article>
          ) : null}
        </section>
      )}
      <div className={selectedId === null ? styles.trajectoryLayout : undefined}>
        <LifeArchiveSearch
          fallback={
            <>
              {eventsError && (
                <ErrorBanner
                  message={t.failed}
                  onRetry={() => void loadTrajectory()}
                  retryLabel={strings.common.retry}
                />
              )}
              {eventsLoading && events.length === 0 ? (
                <LoadingBlock label={t.loading} />
              ) : (
                <>
                  <LifeTrajectory entries={entries} onPursuit={selectPursuit} onTopic={selectTopic} topicId={topicId} />
                  {!eventsError && entries.length === 0 && <p className={styles.empty}>{t.emptyEvents}</p>}
                </>
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
          onSelectTopic={selectTopic}
          pursuitId={selectedId}
          revision={revision}
          topicId={topicId}
          visible={visible}
        />
        {selectedId === null && (
          <section className={styles.section}>
            <h2>{t.pursuitsTitle}</h2>
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
                    <strong>{pursuit.title || t.pursuitFallback}</strong>
                    {pursuit.narrative && <p>{pursuit.narrative}</p>}
                  </button>
                ))}
              </div>
            )}
            <Pager
              busy={pursuitsLoading}
              className={styles.pagination}
              hasMore={pursuitsHasMore}
              onPageChange={pursuitPage =>
                updateLifeUi({
                  pursuitPage,
                  pursuitCursors:
                    pursuitPage > ui.pursuitPage && pursuitsNextCursor
                      ? [...ui.pursuitCursors.slice(0, ui.pursuitPage + 1), pursuitsNextCursor]
                      : ui.pursuitCursors
                })
              }
              page={ui.pursuitPage}
            />
          </section>
        )}
      </div>
    </div>
  )
}
