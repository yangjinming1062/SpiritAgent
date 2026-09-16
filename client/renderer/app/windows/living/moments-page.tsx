// 片刻页：精灵主导的朋友圈式时间线，新在上；用户可就单条片刻评论与精灵互动。
// 后端直连；精灵回复经 WS `companion.moment.comment` 增量推送；空态文案人格化。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useState } from 'react'

import { $persona } from '@/modules/character'
import { InlineMedia } from '@/modules/media'
import {
  $moments,
  $momentsLoading,
  commentMoment,
  deleteMomentComment,
  hydrateMoments,
  type MomentCommentEntry
} from '@/modules/memory'
import { $auth } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

import styles from './moments.module.css'

const DATE_FORMATTER = new Intl.DateTimeFormat('zh-CN')

function formatDate(iso: string): string {
  try {
    return DATE_FORMATTER.format(new Date(iso))
  } catch {
    return iso
  }
}

export function MomentsPage(): React.JSX.Element {
  const moments = useStore($moments)
  const loading = useStore($momentsLoading)
  const persona = useStore($persona)
  const authKind = useStore($auth).kind
  const strings = useStrings()
  const t = strings.living.moments
  const [expandedId, setExpandedId] = useState<null | string>(null)

  // 冷启动默认视图可能是片刻（hash/localStorage 持久化），hydrateAuth 的 IPC 往返
  // 尚未完成时 authedApi 会以 unauth 静默跳过——等 auth 就绪再水合。
  useEffect(() => {
    if (authKind === 'authenticated') {
      void hydrateMoments()
    }
  }, [authKind])

  const formattedMoments = useMemo(
    () =>
      moments.map(m => ({
        ...m,
        displayDate: formatDate(m.createdAt)
      })),
    [moments]
  )

  const getKindLabel = (kind: string): string => t.kindLabels[kind] ?? t.kindFallback

  if (loading && moments.length === 0) {
    return <p className={styles.empty}>{t.loading}</p>
  }

  if (moments.length === 0) {
    return <p className={styles.empty}>{t.empty}</p>
  }

  const companionName = persona?.name ?? strings.living.rail.companionFallback

  return (
    <div className={styles.list}>
      {formattedMoments.map(m => {
        const expanded = expandedId === m.id

        return (
          <article className={styles.card} key={m.id}>
            <button className={styles.cardToggle} onClick={() => setExpandedId(expanded ? null : m.id)} type="button">
              <div className={styles.cardHeader}>
                <span className={styles.kindBadge}>{getKindLabel(m.kind)}</span>
                <time className={styles.date} dateTime={m.createdAt}>
                  {m.displayDate}
                </time>
              </div>
              <h3 className={styles.title}>{m.title ?? t.noTitle}</h3>
              {m.body && (
                <p className={`${styles.body} ${expanded ? styles.bodyExpanded : styles.bodyClamp}`}>{m.body}</p>
              )}
            </button>
            {m.mediaUrl ? (
              <InlineMedia alt={m.title ?? ''} audioUrl={m.audioUrl} mediaType={m.mediaType} url={m.mediaUrl} />
            ) : null}
            <MomentComments companionName={companionName} momentId={m.id} />
          </article>
        )
      })}
    </div>
  )
}

function MomentComments(props: { companionName: string; momentId: string }): React.JSX.Element {
  const { companionName, momentId } = props
  const moments = useStore($moments)
  const t = useStrings().living.moments
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)

  const comments = moments.find(m => m.id === momentId)?.comments ?? []

  const submit = async (): Promise<void> => {
    const content = draft.trim()

    if (!content || sending) {
      return
    }

    setSending(true)

    const ok = await commentMoment(momentId, content)

    setSending(false)

    if (ok) {
      setDraft('')
    }
  }

  const remove = async (commentId: string): Promise<void> => {
    await deleteMomentComment(momentId, commentId)
  }

  return (
    <div className={styles.comments}>
      {comments.map(c => (
        <CommentRow comment={c} companionName={companionName} key={c.id} onRemove={remove} />
      ))}
      <div className={styles.commentInputRow}>
        <input
          aria-label={t.commentPlaceholder}
          className={styles.commentInput}
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
  comment: MomentCommentEntry
  companionName: string
  onRemove: (commentId: string) => Promise<void>
}): React.JSX.Element {
  const { comment, companionName, onRemove } = props
  const isCompanion = comment.role !== 'user'
  const t = useStrings().living.moments

  return (
    <div className={`${styles.commentRow} ${isCompanion ? styles.commentCompanion : ''}`}>
      <span className={styles.commentAuthor}>{isCompanion ? companionName : t.userLabel}</span>
      <span className={styles.commentContent}>{comment.content}</span>
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
