import { IconMessageCircle, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { isRecord } from '../shared/gateway'

import type { RemoteApi, RemoteGateway } from './api'
import { ApiError } from './api'
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
        query: { state: 'recoverable', cursor: nextCursor }
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
      } else if (event.type === 'video_gen.completed' || event.type === 'video_gen.failed') {
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
              <strong>{item.title || '视频动态'}</strong>
              <span className="pill">
                {item.video_status === 'pending'
                  ? '制作中'
                  : item.video_status === 'unknown'
                    ? '结果待核对'
                    : item.video_status === 'ready'
                      ? '已就绪'
                      : '制作失败'}
              </span>
              {item.media_url ? (
                <Media
                  refresh={() => {
                    void run(() => loadRecovery())
                  }}
                  type="video"
                  url={item.media_url}
                />
              ) : null}
              {item.error ? <Notice>{item.error}</Notice> : null}
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
  const [selected, setSelected] = usePageState<string | null>('diary.selected', null)
  const [from, setFrom] = usePageState('diary.from', '')
  const [to, setTo] = usePageState('diary.to', '')
  const onReadRef = useRef(onRead)
  onReadRef.current = onRead
  const request = useRef(0)

  const load = useCallback(async () => {
    const revision = ++request.current
    const result = await api.invoke('companion_journal.get_diary', {
      query: { from: from || undefined, to: to || undefined, limit: 100 }
    })

    if (alive() && revision === request.current) {
      setEntries(result.entries)
      setLoaded(true)

      await markReadAfterRender(
        result.unread_diary_ids,
        () => alive() && revision === request.current,
        ids => api.invoke('companion_journal.read_diary', { body: { diary_ids: ids } }),
        () => onReadRef.current()
      )
    }
  }, [api, alive, from, to])

  useEffect(() => {
    void runLatest(load)
  }, [refresh, load, runLatest])
  const entry = entries.find(value => value.id === selected)
  usePageScroll(`diary.${selected ?? 'list'}.${from}.${to}`, loaded)

  return (
    <>
      {!entry ? (
        <details className="filter-details">
          <summary>按日期查找</summary>
          <div className="date-filter">
            <label>
              从
              <input onChange={event => setFrom(event.target.value)} type="date" value={from} />
            </label>
            <label>
              至
              <input onChange={event => setTo(event.target.value)} type="date" value={to} />
            </label>
          </div>
        </details>
      ) : null}
      {error ? <Notice>{error}</Notice> : null}
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
      {entries.length === 100 ? (
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
