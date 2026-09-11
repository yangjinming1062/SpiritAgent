import { useStore } from '@nanostores/react'
import { type Ref, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { triggerHaptic } from '@/shared/lib/haptics'
import { AlertCircle, AlertTriangle, Check, CheckCircle2, Copy, type IconComponent, Info, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import {
  $notifications,
  type AppNotification,
  clearNotifications,
  dismissNotification,
  type NotificationKind
} from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

// toast 属于瞬时浮层——两个窗口共用同一套玻璃样式（--ui-* token，随主题换肤）。
const tone: Record<NotificationKind, { icon: IconComponent; iconClass: string }> = {
  error: { icon: AlertCircle, iconClass: 'text-danger-fg' },
  warning: { icon: AlertTriangle, iconClass: 'text-amber-400' },
  info: { icon: Info, iconClass: 'text-muted' },
  success: { icon: CheckCircle2, iconClass: 'text-success' }
}

const STACK_SURFACE =
  'pointer-events-auto rounded-xl border border-line-standard bg-glass text-strong shadow-xl backdrop-blur-glass'

// regionRef 把 portal 容器的 DOM 引用交给调用方——精灵透明窗口需要借此把
// toast 矩形注册进交互区域登记处（shared 不得反向依赖 companion，所以经 props 透传）。
export function NotificationStack({ regionRef }: { regionRef?: Ref<HTMLDivElement> }): React.JSX.Element | null {
  const notifications = useStore($notifications)
  const t = useStrings()
  const lastNotificationIdRef = useRef<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const copy = t.notifications

  useEffect(() => {
    if (notifications.length <= 1) {
      setExpanded(false)
    }
  }, [notifications.length])

  useEffect(() => {
    const latest = notifications[0]

    if (!latest || latest.id === lastNotificationIdRef.current) {
      return
    }

    lastNotificationIdRef.current = latest.id

    if (latest.kind === 'success') {
      triggerHaptic('success')
    } else if (latest.kind === 'error') {
      triggerHaptic('error')
    } else if (latest.kind === 'warning') {
      triggerHaptic('warning')
    }
  }, [notifications])

  if (notifications.length === 0) {
    return null
  }

  const [latest, ...olderNotifications] = notifications
  const overflowCount = olderNotifications.length

  // 渲染到 <body>，z-index 高于 Radix 对话框层（overlay z-[120]、content z-[130]）。
  // 不做 portal 时，堆叠上下文留在 React 根子树内，body 级对话框 / overlay 的 portal
  // 会盖在上面——所以在对话框打开时（或任意设置面板上）触发的成功提示
  // 会不可见。titlebar-height 变量只在 app shell 作用域内存在，
  // 在 <body> 上挂载时退回到其常量值（34px）。
  return createPortal(
    <div
      aria-label={copy.region}
      className="pointer-events-none fixed left-1/2 top-[calc(var(--titlebar-height,34px)+0.75rem)] z-[200] flex w-[min(32rem,calc(100%-2rem))] -translate-x-1/2 flex-col gap-2"
      ref={regionRef}
      role="region"
    >
      <NotificationItem notification={latest} />
      {expanded && olderNotifications.map(n => <NotificationItem key={n.id} notification={n} />)}
      {overflowCount > 0 && (
        <div className={cn(STACK_SURFACE, 'flex min-h-8 items-center justify-between px-3 text-xs')}>
          <button
            className="-ml-1.5 rounded-md px-1.5 py-0.5 font-medium text-body transition hover:bg-fill-hover hover:text-strong"
            onClick={() => setExpanded(v => !v)}
            type="button"
          >
            {expanded ? copy.hide : copy.show} {copy.more(overflowCount)}
          </button>
          <button
            className="-mr-1.5 rounded-md px-1.5 py-0.5 text-muted transition hover:bg-fill-hover hover:text-strong"
            onClick={clearNotifications}
            type="button"
          >
            {copy.clearAll}
          </button>
        </div>
      )}
    </div>,
    document.body
  )
}

function NotificationItem({ notification }: { notification: AppNotification }): React.JSX.Element {
  const styles = tone[notification.kind]
  const Icon = styles.icon
  const hasDetail = Boolean(notification.detail && notification.detail !== notification.message)
  const t = useStrings()
  const copy = t.notifications

  return (
    <div
      aria-live={notification.kind === 'error' ? 'assertive' : 'polite'}
      className={cn(
        STACK_SURFACE,
        'grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-x-2.5 px-3.5 py-2.5'
      )}
      role={notification.kind === 'error' ? 'alert' : 'status'}
    >
      <Icon className={cn('mt-0.5 size-4 shrink-0', styles.iconClass)} />
      <div className="col-start-2 min-w-0">
        {notification.title && (
          <div className="text-xs font-medium tracking-tight text-strong">{notification.title}</div>
        )}
        <div className="grid justify-items-start gap-1 text-[11px] leading-relaxed text-muted">
          <p className="m-0">{notification.message}</p>
          {hasDetail && <NotificationDetail detail={notification.detail || ''} />}
          {notification.action && (
            <button
              className="mt-0.5 rounded-md px-1.5 py-0.5 font-medium text-accent transition hover:bg-accent-soft"
              onClick={() => {
                notification.action?.onClick()
                dismissNotification(notification.id)
              }}
              type="button"
            >
              {notification.action.label}
            </button>
          )}
        </div>
      </div>
      <button
        aria-label={copy.dismiss}
        className="col-start-3 mt-0.5 inline-flex size-6 items-center justify-center rounded-md text-faint transition hover:bg-fill-hover hover:text-strong"
        onClick={() => dismissNotification(notification.id)}
        type="button"
      >
        <X className="size-3.5" />
      </button>
    </div>
  )
}

function NotificationDetail({ detail }: { detail: string }): React.JSX.Element {
  const t = useStrings()
  const copy = t.notifications

  return (
    <details className="text-xs text-muted">
      <summary className="select-none font-medium text-muted hover:text-strong">{copy.details}</summary>
      <div className="mt-1 rounded-md bg-fill-faint p-2">
        <pre className="max-h-32 whitespace-pre-wrap wrap-break-word font-mono text-[0.6875rem] leading-relaxed text-muted">
          {detail}
        </pre>
        <CopyDetailButton label={copy.copyDetail} text={detail} />
      </div>
    </details>
  )
}

const COPIED_RESET_MS = 1500

function CopyDetailButton({ label, text }: { label: string; text: string }): React.JSX.Element {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  const t = useStrings()

  const onClick = () => {
    void (async () => {
      try {
        if (window.spiritagent?.writeClipboard) {
          await window.spiritagent.writeClipboard(text)
        } else {
          await navigator.clipboard.writeText(text)
        }

        triggerHaptic('selection')
        setCopied(true)
        window.setTimeout(() => setCopied(false), COPIED_RESET_MS)
      } catch {
        setFailed(true)
        window.setTimeout(() => setFailed(false), COPIED_RESET_MS)
      }
    })()
  }

  return (
    <button
      className="mt-1 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[0.6875rem] text-muted transition hover:bg-fill-hover hover:text-strong"
      onClick={onClick}
      type="button"
    >
      {copied ? <Check className="size-3" /> : failed ? <X className="size-3" /> : <Copy className="size-3" />}
      {copied ? t.common.copied : failed ? t.common.copyFailed : label}
    </button>
  )
}
