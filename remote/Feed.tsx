import { IconMessageCircle, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { RemoteApi, RemoteGateway } from './api'
import { ApiError, record } from './api'
import type { Post, Recovery } from './types'
import { Empty, Media, Notice, PageHeader, timestamp, useAlive, useTask } from './ui'

interface PostPage {
  posts: Post[]
  next_cursor: string | null
  unread_post_ids: string[]
}

export function Feed({
  api,
  gateway,
  refresh
}: {
  api: RemoteApi
  gateway: RemoteGateway
  refresh: number
}): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [posts, setPosts] = useState<Post[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [recoveries, setRecoveries] = useState<Recovery[]>([])
  const [recoveryOffset, setRecoveryOffset] = useState<number | null>(null)
  const revision = useRef(0)
  const initialized = useRef(false)
  const postReads = useRef(new Map<string, number>())
  const pendingRefresh = useRef({ posts: new Set<string>(), head: false, recovery: false })
  const postsRef = useRef(posts)
  postsRef.current = posts

  const load = useCallback(
    async (more = false, nextCursor?: string) => {
      const current = ++revision.current

      const result = await api.request<PostPage>(
        `/api/companion/posts${more && nextCursor ? `?cursor=${encodeURIComponent(nextCursor)}` : ''}`
      )

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

      if (result.unread_post_ids.length > 0 && document.visibilityState === 'visible') {
        await api.request('/api/companion/posts/read', 'POST', {
          post_ids: result.unread_post_ids
        })
      }
    },
    [api, alive]
  )

  const loadRecovery = useCallback(
    async (offset = 0) => {
      const result = await api.request<{
        items: Recovery[]
        next_offset: number | null
      }>(`/api/companion/posts/publications/recovery?offset=${offset}`)

      if (alive()) {
        setRecoveries(previous =>
          offset === 0
            ? result.items
            : [
                ...previous,
                ...result.items.filter(item => !previous.some(old => old.publication_id === item.publication_id))
              ]
        )
        setRecoveryOffset(result.next_offset)
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
        const post = await api.request<Post>(`/api/companion/posts/${id}`)

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
        const id = record(event.payload) ? event.payload.post_id : undefined

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
    <div className="page">
      <PageHeader
        action={
          <button
            aria-label="刷新动态"
            className="icon-button"
            disabled={busy}
            onClick={() => run(() => load())}
            type="button"
          >
            <IconRefresh size={21} />
          </button>
        }
        subtitle="伙伴的小发现，和你的每一句回应"
        title="动态"
      />
      {error ? <Notice>{error}</Notice> : null}
      {recoveries.length > 0 ? (
        <details className="card recovery">
          <summary>待确认的制作 · {recoveries.length}</summary>
          {recoveries.map(item => (
            <section className="recovery-item" key={item.publication_id}>
              <strong>{item.title || '视频动态'}</strong>
              <span className="pill">
                {item.video_status === 'pending'
                  ? '制作中'
                  : item.video_status === 'unknown'
                    ? '等待确认'
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
              {item.error ? <p className="muted">{item.error}</p> : null}
              <div className="button-row">
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      const next = await api.request<Recovery>(
                        `/api/companion/posts/publications/${item.publication_id}/query`,
                        'POST',
                        {}
                      )

                      if (alive()) {
                        setRecoveries(previous =>
                          previous.map(old => (old.publication_id === next.publication_id ? next : old))
                        )
                      }
                    })
                  }
                  type="button"
                >
                  查询结果
                </button>
                {item.can_adopt ? (
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.request(`/api/companion/posts/publications/${item.publication_id}/adopt`, 'POST', {})
                        await Promise.all([load(), loadRecovery()])
                      })
                    }
                    type="button"
                  >
                    采用
                  </button>
                ) : null}
                {item.can_discard ? (
                  <button
                    className="danger"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.request(
                          `/api/companion/posts/publications/${item.publication_id}/discard`,
                          'POST',
                          {}
                        )
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
          {recoveryOffset !== null ? (
            <button
              className="load-more"
              disabled={busy}
              onClick={() => run(() => loadRecovery(recoveryOffset))}
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
        <button className="load-more" disabled={busy} onClick={() => run(() => load(true, cursor))} type="button">
          更多动态
        </button>
      ) : null}
    </div>
  )
}

function PostCard({ api, post, refresh }: { api: RemoteApi; post: Post; refresh: () => Promise<void> }): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [comment, setComment] = useState('')

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
          refresh={() => {
            void run(refresh)
          }}
          type={post.content_type === 'video' ? 'video' : post.content_type === 'audio' ? 'audio' : 'image'}
          url={post.media_url}
        />
      ) : null}
      {post.audio_url ? (
        <Media
          refresh={() => {
            void run(refresh)
          }}
          type="audio"
          url={post.audio_url}
        />
      ) : null}
      <div className="post-comments">
        {post.comments.map(item => (
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
                        await api.request(`/api/companion/posts/${post.id}/comments/${item.id}/reply/retry`, 'POST', {})
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
                      await api.request(`/api/companion/posts/${post.id}/comments/${item.id}`, 'DELETE')
                      await refresh()
                    })
                  }
                  type="button"
                >
                  删除
                </button>
              </div>
            ) : null}
            {item.reply_error ? <p className="muted">{item.reply_error}</p> : null}
          </div>
        ))}
      </div>
      {error ? <Notice>{error}</Notice> : null}
      <form
        className="comment-form"
        onSubmit={event => {
          event.preventDefault()
          void run(async () => {
            await api.request(`/api/companion/posts/${post.id}/comments`, 'POST', { content: comment.trim() })

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
