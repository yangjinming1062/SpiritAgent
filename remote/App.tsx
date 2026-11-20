import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'

import { ApiError, errorText, RemoteApi, RemoteGateway } from './api'
import { clearChatState } from './chat-state'
import { PageStateProvider } from './page-state'
import type { RemoteSession } from './types'
import { Notice, pauseMedia } from './ui'
import { Workspace } from './Workspace'

type LoginState =
  | { kind: 'loading' }
  | { kind: 'disconnected'; error: string }
  | { kind: 'connected'; account: RemoteSession; api: RemoteApi; gateway: RemoteGateway; logout: () => void }

export function App({ pairingToken }: { pairingToken: string | null }): ReactElement {
  const [state, setState] = useState<LoginState>({ kind: 'loading' })
  useEffect(() => {
    const login = new RemoteApi()
    let current = true
    let release = () => {}
    const connect = async () => {
      try {
        const account = pairingToken
          ? await login.invoke('remote.exchange_pairing', {
              body: {
                token: pairingToken,
                device_name: /iPhone|iPad/.test(navigator.userAgent)
                  ? 'iPhone / iPad'
                  : /Android/.test(navigator.userAgent)
                    ? 'Android 手机'
                    : '网页设备'
              }
            })
          : await login.invoke('remote.current_remote_session')
        if (!current) {
          return
        }

        const end = (error: string) => {
          release()
          clearChatState(account)
          setState({ kind: 'disconnected', error })
        }
        const api = new RemoteApi(account.csrf_token, () =>
          end('手机连接已到期。请在电脑的“设置 → 手机访问”重新扫码。')
        )
        const gateway = new RemoteGateway(api)
        release = () => {
          api.close()
          gateway.close()
          pauseMedia()
        }
        setState({ kind: 'connected', account, api, gateway, logout: () => end('') })
      } catch (failure) {
        if (current) {
          setState({
            kind: 'disconnected',
            error: !pairingToken && failure instanceof ApiError && failure.isLoginExpired ? '' : errorText(failure)
          })
        }
      }
    }
    void connect()
    return () => {
      current = false
      login.close()
      release()
    }
  }, [pairingToken])

  if (state.kind !== 'connected') {
    return (
      <main className="connect-page">
        <div className="brand-mark">✦</div>
        <p className="eyebrow">SPIRITAGENT · 远程</p>
        <h1>{state.kind === 'loading' ? '正在连接你的伙伴' : '把陪伴带在身边'}</h1>
        <p className="subtitle">
          在电脑上打开“设置 → 手机访问”，
          <br />
          用手机相机扫描二维码即可连接。
        </p>
        {state.kind === 'disconnected' && state.error && <Notice>{state.error}</Notice>}
        {state.kind === 'loading' ? (
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

  const { account, api, gateway, logout } = state
  const scope = `${account.user.id}:${account.device.id}`
  return (
    <PageStateProvider key={scope} scope={scope}>
      <Workspace api={api} gateway={gateway} onLogout={logout} session={account} />
    </PageStateProvider>
  )
}
