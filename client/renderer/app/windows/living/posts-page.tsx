import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { $persona } from '@/modules/character'
import { InlineMedia } from '@/modules/media'
import {
  $posts,
  $postsHasMore,
  $postsLoading,
  $postsLoadingMore,
  commentPost,
  deletePostComment,
  hydratePost,
  hydratePosts,
  loadMorePosts,
  markPostsRead,
  type PostCommentEntry,
  retryPostReply
} from '@/modules/posts'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { currentClearEpoch } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import { BTN_SUBTLE } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { notify } from '@/shared/store/notifications'
import { $surfaceOpen, $surfaceOpenVisible, $surfaceScreenLocked } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import styles from './posts.module.css'

function formatDate(formatter: Intl.DateTimeFormat, iso: string): string {
  try {
    return formatter.format(new Date(iso))
  } catch {
    return iso
  }
}

export function PostsPage(): React.JSX.Element {
  const posts = useStore($posts)
  const loading = useStore($postsLoading)
  const hasMore = useStore($postsHasMore)
  const loadingMore = useStore($postsLoadingMore)
  const persona = useStore($persona)
  const authKind = useStore($auth).kind
  const surfaceOpen = useStore($surfaceOpen)
  const surfaceVisible = useStore($surfaceOpenVisible)
  const screenLocked = useStore($surfaceScreenLocked)
  const locale = useStore($locale)
  const strings = useStrings()
  const t = strings.living.posts
  const [expandedId, setExpandedId] = useState<null | string>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [readSnapshot, setReadSnapshot] = useState<string[] | null>(null)

  const [documentActive, setDocumentActive] = useState(
    () => document.visibilityState === 'visible' && document.hasFocus()
  )

  const [readBlocked, setReadBlocked] = useState(false)
  const [confirmedReadIds, setConfirmedReadIds] = useState(new Set<string>())
  const readInFlight = useRef(false)
  const beginAsync = useAsyncGuard()
  const foreground = surfaceOpen === 'living' && surfaceVisible && !screenLocked && documentActive
  const wasForeground = useRef(foreground)

  useEffect(() => {
    const update = (): void => {
      setDocumentActive(document.visibilityState === 'visible' && document.hasFocus())
    }

    update()
    window.addEventListener('focus', update)
    window.addEventListener('blur', update)
    document.addEventListener('visibilitychange', update)

    return () => {
      window.removeEventListener('focus', update)
      window.removeEventListener('blur', update)
      document.removeEventListener('visibilitychange', update)
    }
  }, [])

  // 冷启动可能先挂载动态页，鉴权完成后再水合。
  useEffect(() => {
    if (authKind !== 'authenticated') {
      return
    }

    let cancelled = false
    setLoadFailed(false)
    setReadSnapshot(null)

    void hydratePosts().then(snapshot => {
      if (!cancelled) {
        setLoadFailed(snapshot === null)
        setReadSnapshot(snapshot)
      }
    })

    return () => {
      cancelled = true
    }
  }, [authKind, reloadKey])

  useEffect(() => {
    return $gatewayState.listen(state => {
      if (state === 'open') {
        setReloadKey(key => key + 1)
        setReadBlocked(false)
      }
    })
  }, [])

  useEffect(() => {
    const restored = foreground && !wasForeground.current
    wasForeground.current = foreground

    if (restored) {
      setReadBlocked(false)

      if (loadFailed) {
        setReloadKey(key => key + 1)
      }
    }
  }, [foreground, loadFailed])

  // 首屏快照包括较早未读内容；后续事件只确认已进入本次渲染的动态。
  useEffect(() => {
    if (authKind !== 'authenticated' || !foreground || readSnapshot === null || readBlocked || readInFlight.current) {
      return
    }

    // 浏览器焦点事件可能已到达，而 React 的可见性状态尚未更新。
    if (document.visibilityState !== 'visible' || !document.hasFocus()) {
      return
    }

    const ids = [...new Set([...readSnapshot, ...posts.map(post => post.id)])].filter(id => !confirmedReadIds.has(id))

    if (ids.length === 0) {
      return
    }

    const isLive = beginAsync()
    readInFlight.current = true

    void markPostsRead(ids).then(ok => {
      if (!isLive()) {
        return
      }

      readInFlight.current = false

      if (ok) {
        setConfirmedReadIds(current => new Set([...current, ...ids]))
      } else {
        setReadBlocked(true)
      }
    })
  }, [authKind, foreground, readSnapshot, readBlocked, posts, confirmedReadIds, beginAsync])

  const formattedPosts = useMemo(() => {
    const formatter = new Intl.DateTimeFormat(locale)

    return posts.map(m => ({
      ...m,
      displayDate: formatDate(formatter, m.publishedAt)
    }))
  }, [posts, locale])

  const getContentTypeLabel = (kind: string): string => t.contentTypeLabels[kind] ?? t.contentTypeFallback

  if (loading && posts.length === 0) {
    return <p className={styles.empty}>{t.loading}</p>
  }

  if (posts.length === 0) {
    return loadFailed ? (
      <div className={styles.empty}>
        <p>{t.loadFailed}</p>
        <button className={cn(BTN_SUBTLE, 'mt-3')} onClick={() => setReloadKey(key => key + 1)} type="button">
          {strings.common.retry}
        </button>
      </div>
    ) : (
      <p className={styles.empty}>{t.empty}</p>
    )
  }

  const companionName = persona?.name || strings.living.rail.companionFallback

  return (
    <div className={styles.list}>
      {formattedPosts.map(m => {
        const expanded = expandedId === m.id

        return (
          <article className={styles.card} key={m.id}>
            <button className={styles.cardToggle} onClick={() => setExpandedId(expanded ? null : m.id)} type="button">
              <div className={styles.cardHeader}>
                <span className={styles.contentTypeBadge}>{getContentTypeLabel(m.contentType)}</span>
                <time className={styles.date} dateTime={m.publishedAt}>
                  {m.displayDate}
                </time>
              </div>
              <h3 className={styles.title}>{m.title ?? t.noTitle}</h3>
              {m.body && <p className={cn(styles.body, expanded ? styles.bodyExpanded : styles.bodyClamp)}>{m.body}</p>}
            </button>
            <button
              className={styles.commentSend}
              onClick={() => {
                void hydratePost(m.id)
                document.getElementById(`post-comments-${m.id}`)?.scrollIntoView({ block: 'nearest' })
                document.getElementById(`post-input-${m.id}`)?.focus()
              }}
              type="button"
            >
              {t.commentOpen} ({m.comments.length})
            </button>
            {m.mediaUrl ? (
              <InlineMedia
                alt={m.title ?? ''}
                audioUrl={m.audioUrl}
                mediaType={m.contentType === 'text' ? '' : m.contentType}
                url={m.mediaUrl}
              />
            ) : null}
            <PostComments comments={m.comments} companionName={companionName} postId={m.id} />
          </article>
        )
      })}
      {hasMore && (
        <button
          className={styles.commentSend}
          disabled={loadingMore}
          onClick={() => {
            void loadMorePosts().then(ok => {
              if (!ok) {
                notify({ kind: 'error', message: t.loadFailed })
              }
            })
          }}
          type="button"
        >
          {loadingMore ? t.loading : t.loadMore}
        </button>
      )}
    </div>
  )
}

function PostComments(props: {
  comments: PostCommentEntry[]
  companionName: string
  postId: string
}): React.JSX.Element {
  const { comments, companionName, postId } = props
  const t = useStrings().living.posts
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)

  const submit = async (): Promise<void> => {
    const content = draft.trim()

    if (!content || sending) {
      return
    }

    setSending(true)

    const epoch = currentClearEpoch()
    const ok = await commentPost(postId, content)

    setSending(false)

    if (ok) {
      setDraft('')
    } else if (epoch === currentClearEpoch()) {
      notify({ kind: 'error', message: t.commentFailed })
    }
  }

  const remove = async (commentId: string): Promise<void> => {
    const epoch = currentClearEpoch()

    if (!(await deletePostComment(postId, commentId)) && epoch === currentClearEpoch()) {
      notify({ kind: 'error', message: t.commentDeleteFailed })
    }
  }

  return (
    <div className={styles.comments} id={`post-comments-${postId}`}>
      {comments.map(c => (
        <CommentRow
          comment={c}
          companionName={companionName}
          key={c.id}
          onRemove={remove}
          onRetry={async commentId => {
            if (!(await retryPostReply(postId, commentId))) {
              notify({ kind: 'error', message: t.replyRetryFailed })
            }
          }}
        />
      ))}
      <div className={styles.commentInputRow}>
        <input
          aria-label={t.commentPlaceholder}
          className={styles.commentInput}
          disabled={sending}
          id={`post-input-${postId}`}
          maxLength={500}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              void submit()
            }
          }}
          placeholder={t.commentPlaceholder}
          type="text"
          value={draft}
        />
        <button
          className={styles.commentSend}
          disabled={sending || draft.trim().length === 0}
          onClick={() => {
            void submit()
          }}
          type="button"
        >
          {sending ? t.commentSending : t.commentSend}
        </button>
      </div>
    </div>
  )
}

function CommentRow(props: {
  comment: PostCommentEntry
  companionName: string
  onRetry: (commentId: string) => Promise<void>
  onRemove: (commentId: string) => Promise<void>
}): React.JSX.Element {
  const { comment, companionName, onRemove, onRetry } = props
  const [retrying, setRetrying] = useState(false)
  const isCompanion = comment.role !== 'user'
  const t = useStrings().living.posts

  return (
    <div className={cn(styles.commentRow, isCompanion && styles.commentCompanion)}>
      <span className={styles.commentAuthor}>{isCompanion ? companionName : t.userLabel}</span>
      <span className={styles.commentContent}>{comment.content}</span>
      {!isCompanion && (comment.replyStatus === 'pending' || comment.replyStatus === 'running') && (
        <span className={styles.commentAuthor}>{t.replyPending}</span>
      )}
      {!isCompanion && comment.replyStatus === 'failed' && (
        <button
          className={styles.commentSend}
          disabled={retrying}
          onClick={() => {
            setRetrying(true)
            void onRetry(comment.id).finally(() => setRetrying(false))
          }}
          type="button"
        >
          {t.replyRetry}
        </button>
      )}
      {!isCompanion && (
        <button
          aria-label={t.commentDelete}
          className={styles.commentDelete}
          onClick={() => {
            void onRemove(comment.id)
          }}
          title={t.commentDelete}
          type="button"
        >
          ×
        </button>
      )}
    </div>
  )
}
