import { useStore } from '@nanostores/react'
import type React from 'react'
import type { RefObject } from 'react'
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { ReadingPage } from '@/app/components/reading-page'
import { $persona } from '@/modules/character'
import { InlineMedia, MediaViewerOverlay, openMediaViewer } from '@/modules/media'
import {
  $posts,
  $postsHasMore,
  $postsLoading,
  $postsLoadingMore,
  commentPost,
  deletePostComment,
  hydratePosts,
  loadMorePosts,
  markPostsRead,
  type PostCommentEntry,
  retryPostReply
} from '@/modules/posts'
import { ChevronDown, Sparkles, Trash2 } from '@/shared/lib/icons'
import { currentClearEpoch } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import { BTN_ICON, BTN_PRIMARY, BTN_SUBTLE, ErrorBanner, INPUT_CLASS, LoadingBlock } from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { $locale } from '@/shared/store/locale'
import { notify } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { useReadingForeground } from '../use-reading-foreground'
import { useReadingReceipt } from '../use-reading-receipt'

import { PostVideoRecovery } from './post-video-recovery'
import styles from './posts.module.css'

function formatDate(formatter: Intl.DateTimeFormat, iso: string): string {
  try {
    return formatter.format(new Date(iso))
  } catch {
    return iso
  }
}

export function PostsPage({
  containerRef,
  foreground,
  mediaViewId
}: {
  containerRef?: RefObject<HTMLElement | null>
  foreground?: boolean
  mediaViewId?: string
} = {}): React.JSX.Element {
  const strings = useStrings()

  return (
    <ReadingPage hint={strings.living.posts.intro} title={strings.living.rail.posts}>
      <PostsContent foreground={foreground} mediaViewId={mediaViewId} />
      <MediaViewerOverlay containerRef={containerRef} viewId={mediaViewId} windowId={1} />
    </ReadingPage>
  )
}

function PostsContent({
  foreground: suppliedForeground,
  mediaViewId
}: {
  foreground?: boolean
  mediaViewId?: string
} = {}): React.JSX.Element {
  const posts = useStore($posts)
  const loading = useStore($postsLoading)
  const hasMore = useStore($postsHasMore)
  const loadingMore = useStore($postsLoadingMore)
  const persona = useStore($persona)
  const authState = useStore($auth)
  const authKind = authState.kind
  const sessionId = authState.kind === 'authenticated' ? authState.snapshot.sessionId : null
  const locale = useStore($locale)
  const strings = useStrings()
  const t = strings.living.posts
  const [expandedId, setExpandedId] = useState<null | string>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [readSnapshot, setReadSnapshot] = useState<string[] | null>(null)

  const foreground = useReadingForeground(suppliedForeground)
  const wasForeground = useRef(foreground)

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
  }, [authKind, sessionId, reloadKey])

  useEffect(() => {
    return $gatewayState.listen(state => {
      if (state === 'open') {
        setReloadKey(key => key + 1)
      }
    })
  }, [])

  useEffect(() => {
    const restored = foreground && !wasForeground.current
    wasForeground.current = foreground

    if (restored) {
      setReloadKey(key => key + 1)
    }
  }, [foreground])

  useReadingReceipt({
    scope: sessionId ?? authKind,
    refreshKey: reloadKey,
    foreground,
    snapshot: readSnapshot,
    displayedIds: posts.map(post => post.id),
    ready: authKind === 'authenticated',
    markRead: markPostsRead
  })

  const formattedPosts = useMemo(() => {
    const formatter = new Intl.DateTimeFormat(locale)

    return posts.map(m => ({
      ...m,
      displayDate: formatDate(formatter, m.publishedAt)
    }))
  }, [posts, locale])

  const getContentTypeLabel = (kind: string): string => t.contentTypeLabels[kind] ?? t.contentTypeFallback

  const recovery =
    authState.kind === 'authenticated' ? (
      <PostVideoRecovery key={`${authState.snapshot.sessionId}:${reloadKey}`} />
    ) : null

  if (posts.length === 0) {
    return (
      <div className={styles.list}>
        {recovery}
        <div className={cn('liquid-glass-card', styles.empty)} role={loading || loadFailed ? 'status' : undefined}>
          {loading ? (
            <LoadingBlock label={t.loading} />
          ) : loadFailed ? (
            <ErrorBanner
              message={t.loadFailed}
              onRetry={() => setReloadKey(key => key + 1)}
              retryLabel={strings.common.retry}
              tone="danger"
            />
          ) : (
            <>
              <Sparkles aria-hidden="true" className={styles.emptyIcon} size={28} />
              <p>{t.empty}</p>
            </>
          )}
        </div>
      </div>
    )
  }

  const companionName = persona?.name || strings.living.rail.companionFallback

  return (
    <div className={styles.list}>
      {recovery}
      {formattedPosts.map(m => {
        const expanded = expandedId === m.id

        return (
          <article className={cn('liquid-glass-card', styles.card)} key={m.id}>
            <div className={styles.cardHeader}>
              <span className={styles.contentTypeBadge}>{getContentTypeLabel(m.contentType)}</span>
              <time className={styles.date} dateTime={m.publishedAt}>
                {m.displayDate}
              </time>
            </div>
            <h3 className={styles.title}>{m.title ?? t.noTitle}</h3>
            {m.body && (
              <PostBody body={m.body} expanded={expanded} onToggle={() => setExpandedId(expanded ? null : m.id)} />
            )}
            {m.mediaUrl ? (
              <InlineMedia
                alt={m.title ?? ''}
                audioUrl={m.audioUrl}
                mediaType={m.contentType === 'text' ? '' : m.contentType}
                onImageClick={
                  m.contentType === 'image'
                    ? () => openMediaViewer({ type: 'image', url: m.mediaUrl ?? '' }, mediaViewId)
                    : undefined
                }
                url={m.mediaUrl}
              />
            ) : null}
            <PostComments comments={m.comments} companionName={companionName} postId={m.id} />
          </article>
        )
      })}
      {hasMore && (
        <button
          className={cn(BTN_SUBTLE, styles.loadMore)}
          disabled={loadingMore}
          onClick={() => {
            void loadMorePosts().then(ok => {
              if (!ok) {
                notify({ kind: 'error', message: t.loadFailed, title: t.loadFailed })
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

function PostBody({
  body,
  expanded,
  onToggle
}: {
  body: string
  expanded: boolean
  onToggle: () => void
}): React.JSX.Element {
  const textRef = useRef<HTMLParagraphElement>(null)
  const textId = useId()
  const [clipped, setClipped] = useState(false)
  const t = useStrings().living.posts

  useLayoutEffect(() => {
    const element = textRef.current

    if (!element || expanded) {
      return
    }

    const update = (): void => setClipped(element.scrollHeight > element.clientHeight + 1)
    const observer = new ResizeObserver(update)
    observer.observe(element)
    update()

    return () => observer.disconnect()
  }, [body, expanded])

  return (
    <>
      <p className={cn(styles.body, !expanded && styles.bodyClamp)} id={textId} ref={textRef}>
        {body}
      </p>
      {(expanded || clipped) && (
        <button
          aria-controls={textId}
          aria-expanded={expanded}
          className={styles.cardToggle}
          onClick={onToggle}
          type="button"
        >
          {expanded ? t.collapse : t.expand}
          <ChevronDown aria-hidden="true" className={expanded ? styles.chevronExpanded : undefined} size={14} />
        </button>
      )}
    </>
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
      notify({ kind: 'error', message: t.commentFailed, title: t.commentFailed })
    }
  }

  const remove = async (commentId: string): Promise<void> => {
    const epoch = currentClearEpoch()

    if (!(await deletePostComment(postId, commentId)) && epoch === currentClearEpoch()) {
      notify({ kind: 'error', message: t.commentDeleteFailed, title: t.commentDeleteFailed })
    }
  }

  return (
    <div className={styles.comments}>
      {comments.map(c => (
        <CommentRow
          comment={c}
          companionName={companionName}
          key={c.id}
          onRemove={remove}
          onRetry={async commentId => {
            if (!(await retryPostReply(postId, commentId))) {
              notify({ kind: 'error', message: t.replyRetryFailed, title: t.replyRetryFailed })
            }
          }}
        />
      ))}
      <div className={styles.commentInputRow}>
        <input
          aria-label={t.commentPlaceholder}
          className={cn(INPUT_CLASS, styles.commentInput)}
          disabled={sending}
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
          className={BTN_PRIMARY}
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
          className={BTN_SUBTLE}
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
          className={cn(BTN_ICON, styles.commentDelete)}
          onClick={() => {
            void onRemove(comment.id)
          }}
          title={t.commentDelete}
          type="button"
        >
          <Trash2 aria-hidden="true" size={14} />
        </button>
      )}
    </div>
  )
}
