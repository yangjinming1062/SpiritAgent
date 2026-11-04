import { IconMessageCircle, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  archiveSearchCopy,
  type ArchiveSearchFilters,
  archiveSearchQuery,
  emptyArchiveSearch
} from '../shared/archive-search'
import { isRecord } from '../shared/gateway'

import type { RemoteApi, RemoteGateway } from './api'
import { ApiError } from './api'
import { ArchiveSearchPanel } from './ArchiveSearch'
import { MediaReviewButton } from './MediaReviewButton'
import type { Diary, Post, Recovery } from './types'
import {
  Empty,
  Media,
  Notice,
  PageHeader,
  pauseMedia,
  timestamp,
  useAlive,
  usePageScroll,
  usePageState,
  useTask
} from './ui'

// 没有游标与 has_more：整页返回表示可能还有更早的日记。
const DIARY_PAGE_SIZE = 100

async function markReadAfterRender(
  unreadIds: string[],
  isCurrent: () => boolean,
  markRead: (ids: string[]) => Promise<unknown>,
  onRead: () => void
): Promise<void> {
  await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
  if (!isCurrent() || !unreadIds.length || document.visibilityState !== 'visible' || !document.hasFocus()) {
    return
  }

  await markRead(unreadIds)
  if (isCurrent()) {
    onRead()
  }
}

export function Feed({
  api,
  gateway,
  onRead,
  refresh,
  unread
}: {
  api: RemoteApi
  gateway: RemoteGateway
  onRead: () => void
  refresh: number
  unread: { feed: boolean; diary: boolean }
}): ReactElement {
  const [section, setSection] = usePageState<'feed' | 'diary'>('feed.section', 'feed')

  useEffect(() => {
    document.querySelector('.content')?.scrollTo({ top: 0 })
  }, [section])

  return (
    <div className="page">
      <PageHeader subtitle="伙伴的日常，和一起走过的每一天" title="动态" />
      <div aria-label="动态与日记" className="section-tabs feed-tabs">
        {(
          [
            { id: 'feed', label: '动态' },
            { id: 'diary', label: '日记' }
          ] as const
        ).map(item => (
          <button
            aria-pressed={section === item.id}
            className={section === item.id ? 'selected' : ''}
            key={item.id}
            onClick={() => {
              pauseMedia()
              setSection(item.id)
            }}
            type="button"
          >
            {item.label}
            {unread[item.id] ? <i aria-label="有未读内容" className="tab-unread" /> : null}
          </button>
        ))}
      </div>
      {section === 'feed' ? (
        <Posts api={api} gateway={gateway} onRead={onRead} refresh={refresh} />
      ) : (
        <DiaryView api={api} onRead={onRead} refresh={refresh} />
      )}
    </div>
  )
}

function Posts({
  api,
  gateway,
  onRead,
  refresh
}: {
  api: RemoteApi
  gateway: RemoteGateway
  onRead: () => void
  refresh: number
}): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [posts, setPosts] = useState<Post[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [recoveries, setRecoveries] = useState<Recovery[]>([])
  const [recoveryCursor, setRecoveryCursor] = useState<string | null>(null)
  const revision = useRef(0)
  const initialized = useRef(false)
  const postReads = useRef(new Map<string, number>())
  const pendingRefresh = useRef({ posts: new Set<string>(), head: false, recovery: false })
  const postsRef = useRef(posts)
  postsRef.current = posts
  const onReadRef = useRef(onRead)
  onReadRef.current = onRead
  usePageScroll('posts', initialized.current)

  const load = useCallback(
    async (nextCursor?: string) => {
      const current = ++revision.current
      const more = nextCursor !== undefined

      const result = await api.invoke('companion_posts.get_posts', {
        query: { cursor: nextCursor }
      })

      if (!alive() || current !== revision.current) {
        return
      }

      setPosts(previous => {
        const incoming = new Set(result.posts.map(post => post.id))

        return more
          ? [...previous, ...result.posts.filter(post => !previous.some(old => old.id === post.id))]
          : [...result.posts, ...previous.filter(post => !incoming.has(post.id))]
      })

      if (more || !initialized.current) {
        setCursor(result.next_cursor)
      }

      initialized.current = true

      await markReadAfterRender(
        result.unread_post_ids,
        () => alive() && current === revision.current,
        ids => api.invoke('companion_posts.read_posts', { body: { post_ids: ids } }),
        () => onReadRef.current()
      )
    },
    [api, alive]
  )

  const loadRecovery = useCallback(
    async (nextCursor?: string) => {
      const result = await api.invoke('companion_posts.get_publication_recoveries', {
        query: { cursor: nextCursor }
      })

      if (alive()) {
        setRecoveries(previous =>
          nextCursor === undefined
            ? result.items
            : [
                ...previous,
                ...result.items.filter(item => !previous.some(old => old.publication_id === item.publication_id))
              ]
        )
        setRecoveryCursor(result.next_cursor)
      }
    },
    [alive, api]
  )

  const refreshPost = useCallback(
    async (id: string) => {
      revision.current++
      const current = (postReads.current.get(id) ?? 0) + 1
      postReads.current.set(id, current)

      try {
        const post = await api.invoke('companion_posts.get_post_detail', { path: { post_id: id } })

        if (alive() && postReads.current.get(id) === current) {
          setPosts(previous => previous.map(item => (item.id === id ? post : item)))
        }
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 404) {
          if (alive() && postReads.current.get(id) === current) {
            setPosts(previous => previous.filter(item => item.id !== id))
          }
        } else {
          throw failure
        }
      }
    },
    [api, alive]
  )

  const reconcile = useCallback(async () => {
    const pending = pendingRefresh.current
    const tasks = [...pending.posts].map(id => () => refreshPost(id))

    if (pending.head) {
      tasks.push(() => load())
    }
    if (pending.recovery) {
      tasks.push(() => loadRecovery())
    }
    pending.posts.clear()
    pending.head = false
    pending.recovery = false
    const results = await Promise.allSettled(tasks.map(task => task()))
    const failed = results.find(result => result.status === 'rejected')

    if (failed?.status === 'rejected') {
      throw failed.reason
    }
  }, [load, loadRecovery, refreshPost])

  useEffect(() => {
    pendingRefresh.current.head = true
    pendingRefresh.current.recovery = true
    void runLatest(reconcile)
  }, [refresh, reconcile, runLatest])

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const unsubscribe = gateway.subscribeEvent(event => {
      if (event.type === 'companion.post.created') {
        pendingRefresh.current.head = true
      } else if (event.type === 'companion.post.comment' || event.type === 'companion.post.comment.deleted') {
        const id = isRecord(event.payload) ? event.payload.post_id : undefined

        if (typeof id !== 'string' || !postsRef.current.some(post => post.id === id)) {
          return
        }
        revision.current++
        pendingRefresh.current.posts.add(id)
      } else if (
        event.type === 'video_gen.completed' ||
        event.type === 'video_gen.failed' ||
        event.type === 'media.review.updated'
      ) {
        pendingRefresh.current.recovery = true
      } else {
        return
      }

      if (timer === undefined) {
        timer = setTimeout(() => {
          timer = undefined
          void runLatest(reconcile)
        }, 200)
      }
    })

    return () => {
      clearTimeout(timer)
      unsubscribe()
    }
  }, [gateway, reconcile, runLatest])

  return (
    <>
      <div className="feed-tools">
        <button
          aria-label="刷新动态"
          className="icon-button"
          disabled={busy}
          onClick={() => run(() => load())}
          type="button"
        >
          <IconRefresh size={21} />
        </button>
      </div>
      {error ? <Notice>{error}</Notice> : null}
      {recoveries.length > 0 ? (
        <details className="card recovery">
          <summary>待处理的制作 · {recoveries.length}</summary>
          {recoveries.map(item => (
            <section className="recovery-item" key={item.publication_id}>
              <strong>{item.title || '媒体动态'}</strong>
              <span className="pill">
                {item.media_status === 'review_pending'
                  ? '等待选择候选'
                  : item.media_status === 'pending'
                    ? '制作中'
                    : item.media_status === 'unknown'
                      ? '结果待核对'
                      : item.media_status === 'ready'
                        ? '已就绪'
                        : '制作失败'}
              </span>
              {item.media_url ? (
                <Media
                  refresh={() => {
                    void run(() => loadRecovery())
                  }}
                  type={item.media_kind}
                  url={item.media_url}
                />
              ) : null}
              {item.error ? <Notice>{item.error}</Notice> : null}
              {(item.media_status === 'review_pending' || item.media_status === 'failed') && item.media_job_id ? (
                <MediaReviewButton api={api} jobId={item.media_job_id} onChanged={() => loadRecovery()} />
              ) : null}
              {item.can_publish ? (
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      const job = await api.invoke('media.get_media_job', { path: { job_id: item.publication_id } })
                      await api.invoke('media.continue_media_business', {
                        path: { job_id: item.publication_id },
                        body: { action: 'publish_post', expected_revision: job.revision }
                      })
                      await loadRecovery()
                      await load()
                    })
                  }
                  type="button"
                >
                  继续发布
                </button>
              ) : null}
              <div className="button-row">
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      const next = await api.invoke('companion_posts.query_publication_recovery', {
                        path: { publication_id: item.publication_id }
                      })

                      if (alive()) {
                        const closed = ['published', 'partial', 'failed', 'blocked'].includes(next.status)
                        setRecoveries(previous =>
                          previous.flatMap(old =>
                            old.publication_id === next.publication_id ? (closed ? [] : [next]) : [old]
                          )
                        )
                        if (['published', 'partial'].includes(next.status)) {
                          await load()
                        }
                      }
                    })
                  }
                  type="button"
                >
                  查看最新进度
                </button>
                {item.can_discard ? (
                  <button
                    className="danger"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.invoke('companion_posts.discard_publication', {
                          path: { publication_id: item.publication_id }
                        })
                        await loadRecovery()
                      })
                    }
                    type="button"
                  >
                    放弃
                  </button>
                ) : null}
              </div>
            </section>
          ))}
          {recoveryCursor !== null ? (
            <button
              className="load-more"
              disabled={busy}
              onClick={() => run(() => loadRecovery(recoveryCursor))}
              type="button"
            >
              更多制作记录
            </button>
          ) : null}
        </details>
      ) : null}
      {posts.map(post => (
        <PostCard api={api} key={post.id} post={post} refresh={() => refreshPost(post.id)} />
      ))}
      {posts.length === 0 && !busy ? <Empty title="这里会留下伙伴的日常">新的动态发布后，会出现在这里。</Empty> : null}
      {cursor ? (
        <button className="load-more" disabled={busy} onClick={() => run(() => load(cursor))} type="button">
          更多动态
        </button>
      ) : null}
    </>
  )
}

function DiaryView({ api, onRead, refresh }: { api: RemoteApi; onRead: () => void; refresh: number }): ReactElement {
  const alive = useAlive()
  const { runLatest, error } = useTask()
  const [entries, setEntries] = useState<Diary[]>([])
  const [loaded, setLoaded] = useState(false)
  // 日记详情随组件生命周期，切 tab 即回列表；日期筛选保留在 usePageState。
  const [selected, setSelected] = useState<string | null>(null)
  const [to, setTo] = usePageState('diary.to', '')
  const [search, setSearch] = usePageState('diary.search', emptyArchiveSearch())
  const tSearch = archiveSearchCopy()
  const searchEntries = useCallback(
    (filters: ArchiveSearchFilters) =>
      api.invoke('companion_journal.search_diary_entries', { query: archiveSearchQuery(filters) }),
    [api]
  )
  const request = useRef(0)

  const load = useCallback(async () => {
    const revision = ++request.current
    const result = await api.invoke('companion_journal.get_diary', {
      query: { to: to || undefined, limit: DIARY_PAGE_SIZE }
    })

    if (alive() && revision === request.current) {
      setEntries(result.entries)
      setLoaded(true)
    }
  }, [api, alive, to])

  useEffect(() => {
    if (!search.applied) {
      void runLatest(load)
    }
  }, [refresh, load, runLatest, search.applied])
  const entry = entries.find(value => value.id === selected)
  const reading = useDiaryRead(api, search.applied ? null : (entry?.id ?? null), onRead)
  usePageScroll(`diary.${selected ?? 'list'}.${to}`, loaded && !search.applied)

  return (
    <>
      <ArchiveSearchPanel
        count={result => result.entries.length}
        label={tSearch.diary}
        load={searchEntries}
        onChange={setSearch}
        refresh={refresh}
        value={search}
      >
        {result => (
          <DiarySearchResults
            api={api}
            entries={result.entries}
            key={JSON.stringify(search.applied)}
            onRead={onRead}
            scrollKey={`diary.search.${JSON.stringify(search.applied)}`}
          />
        )}
      </ArchiveSearchPanel>
      {!search.applied && (
        <>
          {error ? <Notice>{error}</Notice> : null}
          {reading.error && (
            <Notice>
              {reading.error}
              <button onClick={reading.retry} type="button">
                重试同步已读
              </button>
            </Notice>
          )}
          {entry ? (
            <article className="diary-entry">
              <button className="subtle" onClick={() => setSelected(null)} type="button">
                ‹ 返回日记
              </button>
              <p className="eyebrow">{entry.entry_date}</p>
              <h2>{entry.title}</h2>
              <div className="diary-body">{entry.body}</div>
            </article>
          ) : entries.length ? (
            <div className="diary-list">
              {entries.map(value => (
                <button className="diary-preview" key={value.id} onClick={() => setSelected(value.id)} type="button">
                  <time>{value.entry_date}</time>
                  <h3>{value.title}</h3>
                  <p>{value.body}</p>
                </button>
              ))}
            </div>
          ) : (
            <Empty title="日记还没写到这里">伙伴的第一人称日记会保存在这里。</Empty>
          )}
          {entries.length === DIARY_PAGE_SIZE ? (
            <button
              className="load-more"
              onClick={() => {
                const earliest = entries.at(-1)?.entry_date

                if (earliest) {
                  const date = new Date(`${earliest}T12:00:00`)
                  date.setDate(date.getDate() - 1)
                  setTo(
                    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
                  )
                  setSelected(null)
                }
              }}
              type="button"
            >
              查看更早的日记
            </button>
          ) : null}
        </>
      )}
    </>
  )
}

function useDiaryRead(api: RemoteApi, id: string | null, onRead: () => void) {
  const alive = useAlive()
  const { runLatest, error } = useTask()
  const callback = useRef(onRead)
  callback.current = onRead
  const [retryKey, setRetryKey] = useState(0)
  useEffect(() => {
    let current = true
    let done = false
    const read = (): void => {
      if (!id || done || document.visibilityState !== 'visible' || !document.hasFocus()) {
        return
      }
      void runLatest(() =>
        markReadAfterRender(
          [id],
          () => current && alive(),
          ids => api.invoke('companion_journal.read_diary', { body: { diary_ids: ids } }),
          () => {
            done = true
            callback.current()
          }
        )
      )
    }
    read()
    window.addEventListener('focus', read)
    document.addEventListener('visibilitychange', read)
    return () => {
      current = false
      window.removeEventListener('focus', read)
      document.removeEventListener('visibilitychange', read)
    }
  }, [api, id, alive, runLatest, retryKey])
  return { error, retry: () => setRetryKey(value => value + 1) }
}

function DiarySearchResults({
  api,
  entries,
  onRead,
  scrollKey
}: {
  api: RemoteApi
  entries: Diary[]
  onRead: () => void
  scrollKey: string
}): ReactElement {
  const [selected, setSelected] = useState<string | null>(null)
  const entry = entries.find(item => item.id === selected)
  usePageScroll(`${scrollKey}.${selected ?? 'list'}`, true)
  const reading = useDiaryRead(api, entry?.id ?? null, onRead)
  return (
    <>
      {reading.error && (
        <Notice>
          {reading.error}
          <button onClick={reading.retry} type="button">
            重试同步已读
          </button>
        </Notice>
      )}
      {entry ? (
        <article className="diary-entry">
          <button className="subtle" onClick={() => setSelected(null)} type="button">
            ‹ 返回搜索结果
          </button>
          <time>{entry.entry_date}</time>
          <h2>{entry.title}</h2>
          <div className="diary-body">{entry.body}</div>
        </article>
      ) : (
        <div className="diary-list">
          {entries.map(item => (
            <button className="diary-preview" key={item.id} onClick={() => setSelected(item.id)} type="button">
              <time>{item.entry_date}</time>
              <h3>{item.title}</h3>
              <p>
                {item.body.slice(0, 160)}
                {item.body.length > 160 ? '…' : ''}
              </p>
            </button>
          ))}
        </div>
      )}
    </>
  )
}

function PostCard({ api, post, refresh }: { api: RemoteApi; post: Post; refresh: () => Promise<void> }): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [comment, setComment] = usePageState(`post.${post.id}.comment`, '')
  const [loadedComments, setLoadedComments] = useState<Post['comments']>([])
  const [commentsLoaded, setCommentsLoaded] = useState(false)
  const [commentsCursor, setCommentsCursor] = useState<string | null>(null)
  const commentsRevision = useRef(0)
  useEffect(() => {
    if (post.comments_loaded) {
      commentsRevision.current++
      setLoadedComments(post.comments)
      setCommentsLoaded(true)
      setCommentsCursor(post.comments_next_cursor)
    }
  }, [post])
  const loadComments = async (): Promise<void> => {
    const revision = commentsRevision.current
    const page = await api.invoke('companion_posts.get_comments', {
      path: { post_id: post.id },
      query: { cursor: commentsLoaded ? commentsCursor : undefined }
    })
    if (!alive() || revision !== commentsRevision.current) {
      return
    }
    setLoadedComments(previous => [...new Map([...previous, ...page.comments].map(item => [item.id, item])).values()])
    setCommentsLoaded(true)
    setCommentsCursor(page.next_cursor)
  }

  const refreshMedia = () => {
    void run(refresh)
  }

  return (
    <article className="post-card">
      <header>
        <span className="avatar small">✦</span>
        <div>
          <strong>伙伴</strong>
          <time>{timestamp(post.published_at)}</time>
        </div>
      </header>
      {post.title ? <h2>{post.title}</h2> : null}
      <p className="post-body">{post.body}</p>
      {post.media_url ? (
        <Media
          refresh={refreshMedia}
          type={post.content_type === 'video' ? 'video' : post.content_type === 'audio' ? 'audio' : 'image'}
          url={post.media_url}
        />
      ) : null}
      {post.audio_url ? <Media refresh={refreshMedia} type="audio" url={post.audio_url} /> : null}
      <div className="post-comments">
        {loadedComments.map(item => (
          <div className={`comment ${item.role}`} key={item.id}>
            <p>
              <strong>{item.role === 'user' ? '我' : '伙伴'}</strong> {item.content}
            </p>
            {item.role === 'user' ? (
              <div className="comment-actions">
                {item.reply_status === 'pending' || item.reply_status === 'running' ? <span>伙伴正在回复</span> : null}
                {item.reply_status === 'failed' ? (
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.invoke('companion_posts.retry_comment_reply', {
                          path: { post_id: post.id, comment_id: item.id }
                        })
                        await refresh()
                      })
                    }
                    type="button"
                  >
                    重试回复
                  </button>
                ) : null}
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api.invoke('companion_posts.remove_comment', {
                        path: { post_id: post.id, comment_id: item.id }
                      })
                      await refresh()
                    })
                  }
                  type="button"
                >
                  删除
                </button>
              </div>
            ) : null}
            {item.reply_error ? <Notice>{item.reply_error}</Notice> : null}
          </div>
        ))}
      </div>
      {(!commentsLoaded && post.comment_count > 0) || commentsCursor ? (
        <button disabled={busy} onClick={() => run(loadComments)} type="button">
          {commentsLoaded ? '更多评论' : `查看评论 (${post.comment_count})`}
        </button>
      ) : null}
      {error ? <Notice>{error}</Notice> : null}
      <form
        className="comment-form"
        onSubmit={event => {
          event.preventDefault()
          void run(async () => {
            await api.invoke('companion_posts.post_comment', {
              path: { post_id: post.id },
              body: { content: comment.trim() }
            })

            if (alive()) {
              setComment('')
            }

            await refresh()
          })
        }}
      >
        <IconMessageCircle size={19} />
        <input
          aria-label="写评论"
          maxLength={500}
          onChange={event => setComment(event.target.value)}
          placeholder="写下你的回应…"
          value={comment}
        />
        <button disabled={busy || !comment.trim()} type="submit">
          发送
        </button>
      </form>
    </article>
  )
}
