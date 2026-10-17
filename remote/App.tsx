import { IconHeart, IconMessageCircle, IconSettings, IconSparkles } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { EVENT_POLICIES } from '../shared/api.generated'
import type { GatewayNetworkEvent } from '../shared/gateway'

import { ApiError, errorText, record, RemoteApi, RemoteGateway } from './api'
import { Chat, clearChatState } from './Chat'
import { Companion } from './Companion'
import { Feed } from './Feed'
import type { RemoteSession } from './types'
import { Notice, PageHeader, PageStateProvider, pauseMedia, useTask } from './ui'

type Tab = 'messages' | 'feed' | 'companion' | 'me'
const api = new RemoteApi()

function resourceDomain(type: GatewayNetworkEvent['type']): 'companion' | 'diary' | 'posts' | null {
  switch (type) {
    case 'companion.post.created':
    case 'companion.post.comment':
    case 'companion.post.comment.deleted':
    case 'companion.posts.read':
      return 'posts'
    case 'companion.diary.created':
    case 'companion.diary.deleted':
    case 'companion.diary.read':
      return 'diary'
    case 'memory.changed':
    case 'companion.voice.updated':
    case 'companion.scene.updated':
    case 'companion.scene.activated':
    case 'companion.outfit.updated':
    case 'companion.video.ready':
    case 'companion.video.activated':
    case 'companion.video.failed':
    case 'companion.video.progress':
    case 'companion.action.job_updated':
    case 'companion.action.catalog_changed':
    case 'companion.character_card.updated':
    case 'companion.desktop_video.updated':
    case 'companion.topic_feedback.updated':
      return 'companion'
    default:
      return null
  }
}

function takePairingToken(): string | null {
  const hash = location.hash.slice(1)
  const query = hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : hash
  const token = new URLSearchParams(query).get('token')

  if (token) {
    history.replaceState(null, '', location.pathname)
  }

  return token
}

const pairingToken = takePairingToken()

export function App(): ReactElement {
  const [session, setSession] = useState<RemoteSession | null>(null)
  const [gateway, setGateway] = useState<RemoteGateway | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<Tab>('messages')
  const [connected, setConnected] = useState(false)
  const [connectionError, setConnectionError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [unreadRefresh, setUnreadRefresh] = useState(0)
  const [unread, setUnread] = useState({
    messages: false,
    feed: false,
    diary: false
  })
  const tabRef = useRef(tab)
  tabRef.current = tab
  const activeGateway = useRef<RemoteGateway | null>(null)
  const disconnect = useCallback(() => {
    pauseMedia()
    activeGateway.current?.close()
    activeGateway.current = null
    api.clear()
  }, [])

  useEffect(() => {
    document.querySelector('.content')?.scrollTo({ top: 0 })
  }, [tab])

  useEffect(() => {
    let live = true
    let eventTimer: ReturnType<typeof setTimeout> | undefined
    let refreshContent = false
    let refreshUnread = false

    const scheduleRefresh = (content: boolean, unread: boolean) => {
      refreshContent ||= content
      refreshUnread ||= unread

      if (eventTimer === undefined) {
        eventTimer = setTimeout(() => {
          eventTimer = undefined

          if (live && document.visibilityState === 'visible') {
            if (refreshContent) {
              setRefresh(value => value + 1)
            }
            if (refreshUnread) {
              setUnreadRefresh(value => value + 1)
            }
          }

          refreshContent = false
          refreshUnread = false
        }, 200)
      }
    }

    const invalidate = () => {
      clearChatState()
      disconnect()

      if (live) {
        setSession(null)
        setGateway(null)
        setError('手机连接已到期。请在电脑的“设置 → 设备与能力 → 手机连接”重新扫码。')
      }
    }

    api.onExpired = invalidate

    const load = async () => {
      try {
        const next = pairingToken
          ? await api.invoke('remote.exchange_pairing', {
              body: {
                token: pairingToken,
                device_name: /iPhone|iPad/.test(navigator.userAgent)
                  ? 'iPhone / iPad'
                  : /Android/.test(navigator.userAgent)
                    ? 'Android 手机'
                    : '网页设备'
              }
            })
          : await api.invoke('remote.current_remote_session')

        if (!live) {
          return
        }

        api.authenticate(next)

        try {
          const savedTab = sessionStorage.getItem(`remote:${next.user.id}:${next.device.id}:ui:tab`)
          setTab(savedTab === 'feed' || savedTab === 'companion' || savedTab === 'me' ? savedTab : 'messages')
        } catch {
          setTab('messages')
        }

        try {
          const stored: unknown = JSON.parse(
            localStorage.getItem(`remote:${next.user.id}:${next.device.id}:unread`) ?? '{}'
          )

          if (record(stored)) {
            setUnread({
              messages: stored.messages === true,
              feed: stored.feed === true,
              diary: stored.diary === true
            })
          }
        } catch {
          setUnread({ messages: false, feed: false, diary: false })
        }

        const client = new RemoteGateway(api)
        activeGateway.current = client
        client.subscribeState((open, message) => {
          setConnected(open)
          setConnectionError(message)
          if (open && client.lastRecovery?.recovery === 'snapshot_required') {
            setRefresh(value => value + 1)
          }
        })
        client.subscribeEvent(event => {
          if (
            (event.type === 'message.complete' || event.type === 'companion.message') &&
            (tabRef.current !== 'messages' || document.visibilityState !== 'visible')
          ) {
            setUnread(previous => ({ ...previous, messages: true }))
          }

          if (event.type === 'session.snapshot') {
            return
          }
          const domain = resourceDomain(event.type)
          const resource = EVENT_POLICIES[event.type].recovery === 'resource'
          const mood = event.type === 'companion.mood'
          const message = event.type === 'companion.message'
          if ((resource && domain) || mood || message) {
            scheduleRefresh(
              ((domain === 'companion' || mood) && tabRef.current === 'companion') ||
                (domain === 'diary' && tabRef.current === 'feed') ||
                (message && tabRef.current === 'messages'),
              domain === 'posts' || domain === 'diary'
            )
          }
        })
        setSession(next)
        setGateway(client)
        void client.connect()
      } catch (failure) {
        if (live) {
          setError(failure instanceof ApiError && failure.status === 401 ? '' : errorText(failure))
        }
      } finally {
        if (live) {
          setLoading(false)
        }
      }
    }

    void load()

    return () => {
      live = false
      clearTimeout(eventTimer)
      disconnect()
    }
  }, [disconnect])

  useEffect(() => {
    if (session) {
      try {
        localStorage.setItem(`remote:${session.user.id}:${session.device.id}:unread`, JSON.stringify(unread))
      } catch {
        // 浏览器存储不可用时仍保留本次连接的提醒。
      }
    }
  }, [session, unread])

  useEffect(() => {
    if (session) {
      try {
        sessionStorage.setItem(`remote:${session.user.id}:${session.device.id}:ui:tab`, tab)
      } catch {
        // 浏览器关闭存储时仍可正常切换页面。
      }
    }
  }, [session, tab])

  useEffect(() => {
    if (!session || document.visibilityState !== 'visible') {
      return
    }

    let current = true
    void Promise.allSettled([
      api.invoke('companion_posts.get_posts_unread'),
      api.invoke('companion_journal.get_diary_unread')
    ]).then(([posts, diary]) => {
      if (!current) {
        return
      }

      setUnread(previous => ({
        ...previous,
        feed: posts.status === 'fulfilled' ? posts.value.has_unread : previous.feed,
        diary: diary.status === 'fulfilled' ? diary.value.has_unread : previous.diary
      }))

      for (const result of [posts, diary]) {
        if (result.status === 'rejected') {
          console.warn('远程未读状态校准失败', errorText(result.reason))
        }
      }
    })

    return () => {
      current = false
    }
  }, [session, refresh, unreadRefresh])

  useEffect(() => {
    const foreground = () => {
      if (document.visibilityState === 'visible') {
        setRefresh(value => value + 1)
        void activeGateway.current?.connect()
      } else {
        pauseMedia()
      }
    }

    document.addEventListener('visibilitychange', foreground)
    window.addEventListener('online', foreground)
    window.addEventListener('focus', foreground)

    return () => {
      document.removeEventListener('visibilitychange', foreground)
      window.removeEventListener('online', foreground)
      window.removeEventListener('focus', foreground)
    }
  }, [])

  useEffect(() => {
    const back = () => setTab('messages')
    window.addEventListener('popstate', back)

    return () => window.removeEventListener('popstate', back)
  }, [])

  const selectTab = (next: Tab) => {
    setTab(next)
    if (next === 'messages') {
      setUnread(previous => ({ ...previous, messages: false }))
    }
    pauseMedia()
  }

  if (!session || !gateway) {
    return (
      <main className="connect-page">
        <div className="brand-mark">✦</div>
        <p className="eyebrow">SPIRITAGENT · 远程</p>
        <h1>{loading ? '正在连接你的伙伴' : '把陪伴带在身边'}</h1>
        <p className="subtitle">
          在电脑上打开“设置 → 设备与能力 → 手机连接”，
          <br />
          用手机相机扫描二维码即可连接。
        </p>
        {error ? <Notice>{error}</Notice> : null}
        {loading ? (
          <div className="loader" />
        ) : (
          <button className="primary" onClick={() => location.reload()} type="button">
            重新检查连接
          </button>
        )}
        <p className="connection-footnote">专属于你的账户 · 安全扫码授权</p>
      </main>
    )
  }

  return (
    <PageStateProvider
      key={`${session.user.id}:${session.device.id}`}
      scope={`${session.user.id}:${session.device.id}`}
    >
      <div className="app">
        <div aria-live="polite" className={`connection ${connected ? '' : 'offline'}`}>
          {connected ? '已连接' : connectionError || '实时连接正在恢复，已受理的任务会继续运行'}
        </div>
        <main className="content">
          {tab === 'messages' ? (
            <Chat
              api={api}
              connected={connected}
              device={session.device.id}
              gateway={gateway}
              refresh={refresh}
              userId={session.user.id}
            />
          ) : tab === 'feed' ? (
            <Feed
              api={api}
              gateway={gateway}
              onRead={() => setUnreadRefresh(value => value + 1)}
              refresh={refresh}
              unread={{ feed: unread.feed, diary: unread.diary }}
            />
          ) : tab === 'companion' ? (
            <Companion api={api} refresh={refresh} />
          ) : (
            <Settings
              api={api}
              onLogout={() => {
                clearChatState()
                disconnect()
                setSession(null)
                setGateway(null)
                setError('')
              }}
              session={session}
            />
          )}
        </main>
        <nav aria-label="主导航" className="bottom-nav">
          {(
            [
              { id: 'messages', label: '聊天', icon: IconMessageCircle },
              { id: 'companion', label: '伙伴', icon: IconHeart },
              { id: 'feed', label: '动态', icon: IconSparkles },
              { id: 'me', label: '设置', icon: IconSettings }
            ] as const
          ).map(item => (
            <button
              aria-current={tab === item.id ? 'page' : undefined}
              className={tab === item.id ? 'selected' : ''}
              key={item.id}
              onClick={() => selectTab(item.id)}
              type="button"
            >
              <span>
                <item.icon size={23} stroke={1.7} />
                {(item.id === 'messages' && unread.messages) ||
                (item.id === 'feed' && (unread.feed || unread.diary)) ? (
                  <i aria-label="有未读内容" className="unread-dot" />
                ) : null}
              </span>
              {item.label}
            </button>
          ))}
        </nav>
      </div>
    </PageStateProvider>
  )
}

function Settings({
  api,
  session,
  onLogout
}: {
  api: RemoteApi
  session: RemoteSession
  onLogout: () => void
}): ReactElement {
  const task = useTask()

  return (
    <div className="page">
      <PageHeader subtitle="账户与这台手机的连接" title="设置" />
      <section className="profile-card">
        <div className="avatar">{session.user.username.slice(0, 1)}</div>
        <h2>{session.user.username}</h2>
        <span className="pill">手机已连接</span>
      </section>
      <section className="card">
        <h3>手机连接</h3>
        <dl>
          <div>
            <dt>设备</dt>
            <dd>{session.device.name}</dd>
          </div>
          <div>
            <dt>有效期</dt>
            <dd>{new Date(session.device.expires_at).toLocaleDateString('zh-CN')}</dd>
          </div>
        </dl>
        <p className="muted">陪伴、声音和显示偏好可以在电脑的设置中调整。这里退出只断开这台手机。</p>
      </section>
      {task.error ? <Notice>{task.error}</Notice> : null}
      <button
        className="danger full"
        disabled={task.busy}
        onClick={() =>
          task.run(async () => {
            await api.invoke('remote.logout_remote_session')
            onLogout()
          })
        }
        type="button"
      >
        退出手机登录
      </button>
    </div>
  )
}
