import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'

import { ApiError, errorText, RemoteApi, RemoteGateway } from './api'
import { clearChatState } from './chat-state'
import { PageStateProvider } from './page-state'
import type { RemoteSession } from './types'
import { Notice, pauseMedia } from './ui'
import { Workspace } from './Workspace'

type Connection = {
  account: RemoteSession
  api: RemoteApi
  gateway: RemoteGateway
  logout: () => Promise<void>
}

function takePairingToken(): string | null {
  const token = new URLSearchParams(location.hash.slice(1)).get('token')
  if (token !== null) {
    history.replaceState(null, '', location.pathname + location.search)
  }
  return token
}

export function App(): ReactElement {
  const [connection, setConnection] = useState<Connection | null>()
  const [error, setError] = useState('')
  useEffect(() => {
    const login = new RemoteApi()
    let active: Connection | null = null
    let mounted = true
    let pending = Promise.resolve()

    // 兑换和退出都会改写同一 Cookie，按提交顺序等待服务端回执。
    function enqueue(change: () => Promise<void>): Promise<void> {
      const next = pending.then(() => {
        if (mounted) {
          return change()
        }
      })
      pending = next.catch(() => {})
      return next
    }

    function release(): void {
      if (active) {
        const { api, gateway } = active
        active = null
        api.close()
        gateway.close()
        pauseMedia()
      }
    }

    function open(account: RemoteSession): Connection {
      function end(message: string): void {
        if (active?.api === api) {
          show(undefined, message)
        }
      }
      const api = new RemoteApi(account.csrf_token, () => end('手机连接已到期。请在电脑的“设置 → 手机访问”重新扫码。'))
      const gateway = new RemoteGateway(api)
      const logout = () =>
        enqueue(async () => {
          if (active?.api === api) {
            await api.invoke('remote.logout_remote_session')
            end('')
          }
        })
      return { account, api, gateway, logout }
    }

    function show(account?: RemoteSession, message = '', previous?: RemoteSession): void {
      if (!mounted) {
        return
      }
      const old = active?.account ?? previous
      if (old && old.device.id !== account?.device.id) {
        clearChatState(old)
      }
      if (active?.account.device.id !== account?.device.id) {
        release()
        if (account) {
          active = open(account)
        }
      }
      setConnection(active)
      setError(message)
    }

    async function pair(token: string, previous?: RemoteSession): Promise<void> {
      try {
        const next = await login.invoke('remote.exchange_pairing', {
          body: {
            token,
            device_name: /iPhone|iPad/.test(navigator.userAgent)
              ? 'iPhone / iPad'
              : /Android/.test(navigator.userAgent)
                ? 'Android 手机'
                : '网页设备'
          }
        })
        show(next, '', previous)
      } catch (failure) {
        if (!mounted) {
          return
        }
        let account = active?.account ?? previous
        if (
          failure instanceof ApiError &&
          (failure.isNetwork || failure.isServerError || failure.code === 'invalid_response')
        ) {
          // 响应中断时 Cookie 可能已经生效，沿当前授权读回结果。
          try {
            account = await login.invoke('remote.current_remote_session')
          } catch (check) {
            account = check instanceof ApiError && check.isLoginExpired ? undefined : (active?.account ?? previous)
          }
        }
        show(account, errorText(failure), previous)
      }
    }

    const initial = takePairingToken()
    void enqueue(async () => {
      let previous: RemoteSession | undefined
      try {
        previous = await login.invoke('remote.current_remote_session')
        if (initial === null) {
          show(previous)
        }
      } catch (failure) {
        if (mounted && initial === null) {
          show(undefined, failure instanceof ApiError && failure.isLoginExpired ? '' : errorText(failure))
        }
      }
      if (mounted && initial !== null) {
        await pair(initial, previous)
      }
    })
    const onHashChange = () => {
      const token = takePairingToken()
      if (token !== null) {
        void enqueue(() => pair(token))
      }
    }
    window.addEventListener('hashchange', onHashChange)
    return () => {
      mounted = false
      window.removeEventListener('hashchange', onHashChange)
      login.close()
      release()
    }
  }, [])

  if (!connection) {
    return (
      <main className="connect-page">
        <div className="brand-mark">✦</div>
        <p className="eyebrow">SPIRITAGENT · 远程</p>
        <h1>{connection === undefined ? '正在连接你的伙伴' : '把陪伴带在身边'}</h1>
        <p className="subtitle">
          在电脑上打开“设置 → 手机访问”，
          <br />
          用手机相机扫描二维码即可连接。
        </p>
        {error && <Notice>{error}</Notice>}
        {connection === undefined ? (
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

  const { account, api, gateway, logout } = connection
  const scope = `${account.user.id}:${account.device.id}`
  return (
    <PageStateProvider key={scope} scope={scope}>
      <Workspace api={api} gateway={gateway} onLogout={logout} pairingError={error} session={account} />
    </PageStateProvider>
  )
}
