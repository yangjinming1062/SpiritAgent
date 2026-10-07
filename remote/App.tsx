import { IconBook2, IconMessageCircle, IconSparkles, IconUser } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'

import { ApiError, errorText, record, RemoteApi, RemoteGateway } from './api'
import { Chat, clearChatState } from './Chat'
import { Companion } from './Companion'
import { Feed } from './Feed'
import type { RemoteSession } from './types'
import { Notice, PageHeader, pauseMedia, useTask } from './ui'

type Tab = 'messages' | 'feed' | 'companion' | 'me'
const api = new RemoteApi()

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
  const [refresh, setRefresh] = useState(0)
  const [unreadRefresh, setUnreadRefresh] = useState(0)
  const [unread, setUnread] = useState({
    messages: false,
    feed: false,
    companion: false
  })
  const tabRef = useRef(tab)
  tabRef.current = tab
  const activeGateway = useRef<RemoteGateway | null>(null)

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
      pauseMedia()
      activeGateway.current?.close()
      activeGateway.current = null
      api.clear()

      if (live) {
        setSession(null)
        setGateway(null)
        setError('手机授权已失效，请在电脑的“远程”页面重新扫码')
      }
    }

    api.onExpired = invalidate

    const load = async () => {
      try {
        const next = await api.request<RemoteSession>(
          pairingToken ? '/api/remote/session/exchange' : '/api/remote/session',
          pairingToken ? 'POST' : 'GET',
          pairingToken
            ? {
                token: pairingToken,
                device_name: /iPhone|iPad/.test(navigator.userAgent)
                  ? 'iPhone / iPad'
                  : /Android/.test(navigator.userAgent)
                    ? 'Android 手机'
                    : '网页设备'
              }
            : undefined
        )

        if (!live) {
          return
        }

        api.authenticate(next)

        try {
          const stored: unknown = JSON.parse(
            localStorage.getItem(`remote:${next.user.id}:${next.device.id}:unread`) ?? '{}'
          )

          if (record(stored)) {
            setUnread({
              messages: stored.messages === true,
              feed: stored.feed === true,
              companion: stored.companion === true
            })
          }
        } catch {
          setUnread({ messages: false, feed: false, companion: false })
        }

        const client = new RemoteGateway(api, invalidate)
        activeGateway.current = client
        client.subscribeState(open => {
          setConnected(open)
          if (open) {
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

          const posts = event.type.startsWith('companion.post')
          const diary = event.type.startsWith('companion.diary')

          if (event.type.startsWith('companion.') || event.type === 'memory.changed') {
            scheduleRefresh(
              (!posts && tabRef.current === 'companion') ||
                (event.type === 'companion.message' && tabRef.current === 'messages'),
              posts || diary
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
      pauseMedia()
      activeGateway.current?.close()
      api.clear()
    }
  }, [])

  useEffect(() => {
    if (session) {
      localStorage.setItem(`remote:${session.user.id}:${session.device.id}:unread`, JSON.stringify(unread))
    }
  }, [session, unread])

  useEffect(() => {
    if (!session || document.visibilityState !== 'visible') {
      return
    }

    let current = true
    void Promise.allSettled([
      api.request<{ has_unread: boolean }>('/api/companion/posts/unread'),
      api.request<{ has_unread: boolean }>('/api/companion/diary/unread')
    ]).then(([posts, diary]) => {
      if (!current) {
        return
      }

      setUnread(previous => ({
        ...previous,
        feed: posts.status === 'fulfilled' ? posts.value.has_unread : previous.feed,
        companion: diary.status === 'fulfilled' ? diary.value.has_unread : previous.companion
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

    return () => {
      document.removeEventListener('visibilitychange', foreground)
      window.removeEventListener('online', foreground)
    }
  }, [])

  useEffect(() => {
    const back = () => setTab('messages')
    window.addEventListener('popstate', back)

    return () => window.removeEventListener('popstate', back)
  }, [])

  const selectTab = (next: Tab) => {
    setTab(next)
    setUnread(previous => ({ ...previous, [next]: false }))
    pauseMedia()
  }

  if (!session || !gateway) {
    return (
      <main className="connect-page">
        <div className="brand-mark">✦</div>
        <p className="eyebrow">SPIRITAGENT · 远程</p>
        <h1>{loading ? '正在连接你的伙伴' : '把陪伴带在身边'}</h1>
        <p className="subtitle">
          在电脑上打开唤生的“远程”页面，
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
    <div className="app" key={`${session.user.id}:${session.device.id}`}>
      <div aria-live="polite" className={`connection ${connected ? '' : 'offline'}`}>
        {connected ? '已连接' : '正在重新连接，已受理的任务会继续运行'}
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
          <Feed api={api} gateway={gateway} refresh={refresh} />
        ) : tab === 'companion' ? (
          <Companion api={api} connected={connected} gateway={gateway} refresh={refresh} />
        ) : (
          <Me
            api={api}
            onLogout={() => {
              clearChatState()
              pauseMedia()
              activeGateway.current?.close()
              api.clear()
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
            { id: 'messages', label: '消息', icon: IconMessageCircle },
            { id: 'feed', label: '动态', icon: IconSparkles },
            { id: 'companion', label: '伙伴', icon: IconBook2 },
            { id: 'me', label: '我', icon: IconUser }
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
              {item.id !== 'me' && unread[item.id] ? <i className="unread-dot" /> : null}
            </span>
            {item.label}
          </button>
        ))}
      </nav>
    </div>
  )
}

function Me({
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
      <PageHeader subtitle="你的账户与手机连接" title="我" />
      <section className="profile-card">
        <div className="avatar">{session.user.username.slice(0, 1)}</div>
        <h2>{session.user.username}</h2>
        <span className="pill">远程已授权</span>
      </section>
      <section className="card">
        <h3>当前设备</h3>
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
        <p className="muted">你可以在电脑上查看和撤销已授权的手机。</p>
      </section>
      {task.error ? <Notice>{task.error}</Notice> : null}
      <button
        className="danger full"
        disabled={task.busy}
        onClick={() =>
          task.run(async () => {
            await api.request('/api/remote/session', 'DELETE')
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
