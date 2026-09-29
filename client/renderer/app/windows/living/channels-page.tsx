import { IconBrandWechat } from '@tabler/icons-react'
import { QRCodeSVG } from 'qrcode.react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { unwrapIpcErrorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import {
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SUBTLE,
  ConfirmDialog,
  EmptyState,
  ListRow,
  LoadingBlock,
  Pill,
  SettingsContent,
  SettingsSubsection,
  Spinner
} from '@/shared/panel'
import {
  actOnChannelPeer,
  getWeixinLoginState,
  listChannelPeers,
  listChannels,
  logoutChannel,
  startWeixinLogin
} from '@/shared/spiritagent'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type {
  ChannelBindingInfo,
  ChannelLoginState,
  ChannelPeerAction,
  ChannelPeerInfo
} from '@/shared/types/spiritagent'

const WEIXIN_CHANNEL = 'weixin_ilink'
const LOGIN_POLL_INTERVAL_MS = 2000
// 后端扫码登录最长 300 秒（weixin_ilink.py QR_LOGIN_TIMEOUT_SECONDS），另留取码与末次查询的余量。
const LOGIN_POLL_DEADLINE_MS = (300 + 60) * 1000
const LOGIN_POLL_MAX_FAILURES = 5

const RAW_PREFIX_TO_MIME: ReadonlyArray<[RegExp, string]> = [
  [/^iVBORw0KGgo/, 'png'],
  [/^\/9j\//, 'jpeg'],
  [/^PHN2Zy/, 'svg+xml']
]

function isNotFoundIpc(error: unknown): boolean {
  return /^404 /.test(unwrapIpcErrorMessage(error))
}

function isDataImage(content: string): boolean {
  if (/^data:image\//i.test(content)) {
    return true
  }

  return RAW_PREFIX_TO_MIME.some(([re]) => re.test(content))
}

function normalizeDataImage(content: string): string {
  if (/^data:image\//i.test(content)) {
    return content
  }

  for (const [re, mime] of RAW_PREFIX_TO_MIME) {
    if (re.test(content)) {
      return `data:image/${mime};base64,${content}`
    }
  }

  return content
}

function pickStatusPill(args: {
  connected: boolean
  loginFlowActive: boolean
  loginPendingLabel: string
  loginRequired: boolean
  loginRequiredLabel: string
  weixinStatusLabel: string
}): string | null {
  const { connected, loginFlowActive, loginPendingLabel, loginRequired, loginRequiredLabel, weixinStatusLabel } = args

  if (loginFlowActive) {
    return loginPendingLabel
  }

  if (connected) {
    return weixinStatusLabel
  }

  if (loginRequired) {
    return loginRequiredLabel
  }

  return null
}

interface PeerActionDescriptor {
  action: ChannelPeerAction
  className: string
  label: string
}

function peerActionsFor(
  status: ChannelPeerInfo['status'],
  labels: { approve: string; block: string; remove: string }
): PeerActionDescriptor[] {
  const primary: PeerActionDescriptor | null =
    status === 'pending'
      ? { action: 'approve', className: BTN_PRIMARY, label: labels.approve }
      : status === 'allowed'
        ? { action: 'block', className: BTN_GHOST, label: labels.block }
        : status === 'blocked'
          ? { action: 'approve', className: BTN_GHOST, label: labels.approve }
          : null

  const descriptors: PeerActionDescriptor[] = []

  if (primary) {
    descriptors.push(primary)
  }

  descriptors.push({ action: 'delete', className: BTN_GHOST, label: labels.remove })

  return descriptors
}

export function ChannelsPage(): React.JSX.Element {
  const t = useStrings().settings.channels

  const [isLoading, setIsLoading] = useState(true)
  const [weixinBinding, setWeixinBinding] = useState<ChannelBindingInfo | null>(null)
  const [login, setLogin] = useState<ChannelLoginState | null>(null)
  const [loginPolling, setLoginPolling] = useState(false)
  const [loginBusy, setLoginBusy] = useState(false)
  const [confirmLogout, setConfirmLogout] = useState(false)
  const [peers, setPeers] = useState<ChannelPeerInfo[]>([])
  const [peerBusy, setPeerBusy] = useState<string | null>(null)
  const loginDeadlineRef = useRef(0)

  const reload = useCallback(async () => {
    const [channels, peerItems] = await Promise.all([
      listChannels(),
      // 未建立绑定时 peers 端点返回 404，按无对端处理；其他错误须报告，加载失败时保留当前列表。
      listChannelPeers(WEIXIN_CHANNEL).then(
        result => result.items,
        (error: unknown) => {
          if (isNotFoundIpc(error)) {
            return []
          }

          notifyError(error, t.peers.loadFailed)

          return null
        }
      )
    ])

    const weixin = channels.items.find(item => item.channel === WEIXIN_CHANNEL)?.binding ?? null
    setWeixinBinding(weixin)
    setPeers(prev => (weixin ? (peerItems ?? prev) : []))
  }, [t.peers.loadFailed])

  useEffect(() => {
    void (async () => {
      try {
        await reload()
      } catch (error) {
        notifyError(error, t.loadFailed)
      } finally {
        setIsLoading(false)
      }
    })()
  }, [reload, t.loadFailed])

  // 超过登录时限或连续查询失败即停止轮询；刚发起时短暂的 login_required 属正常，继续等待。
  useEffect(() => {
    if (!loginPolling) {
      return
    }

    let active = true
    let failures = 0

    const stopWithTimeout = (): void => {
      setLoginPolling(false)
      setLogin({ state: 'error', error: t.weixin.loginTimeout })
    }

    const timer = window.setInterval(() => {
      if (Date.now() > loginDeadlineRef.current) {
        log.warn('channels', 'weixin login polling timed out')
        stopWithTimeout()

        return
      }
      void (async () => {
        let state: ChannelLoginState

        try {
          state = await getWeixinLoginState()
        } catch (error) {
          failures += 1

          if (active && failures >= LOGIN_POLL_MAX_FAILURES) {
            log.warn('channels', 'weixin login polling failed', error)
            stopWithTimeout()
          }

          return
        }

        if (!active) {
          return
        }

        failures = 0
        setLogin(state)

        if (state.state === 'confirmed') {
          setLoginPolling(false)
          notify({ kind: 'success', message: t.weixin.loginSuccess })

          try {
            await reload()
          } catch (error) {
            notifyError(error, t.loadFailed)
          }
        } else if (state.state === 'expired' || state.state === 'error') {
          setLoginPolling(false)
        }
      })()
    }, LOGIN_POLL_INTERVAL_MS)

    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [loginPolling, reload, t.loadFailed, t.weixin.loginSuccess, t.weixin.loginTimeout])

  const beginLogin = async (): Promise<void> => {
    setLoginBusy(true)

    try {
      const state = await startWeixinLogin()
      loginDeadlineRef.current = Date.now() + LOGIN_POLL_DEADLINE_MS
      setLogin(state)
      setLoginPolling(true)
    } catch (error) {
      notifyError(error, t.weixin.loginStartFailed)
    } finally {
      setLoginBusy(false)
    }
  }

  const doLogout = async (): Promise<void> => {
    try {
      await logoutChannel(WEIXIN_CHANNEL)
      notify({ kind: 'info', message: t.weixin.logoutSuccess })
      setLogin(null)
      await reload()
    } catch (error) {
      notifyError(error, t.weixin.logoutFailed)
    }
  }

  const actOnPeer = async (peerId: string, action: ChannelPeerAction): Promise<void> => {
    setPeerBusy(peerId)

    try {
      setPeers((await actOnChannelPeer(WEIXIN_CHANNEL, peerId, action)).items)
    } catch (error) {
      notifyError(error, t.peers.actionFailed)
    } finally {
      setPeerBusy(null)
    }
  }

  if (isLoading) {
    return <LoadingBlock label={t.heading} />
  }

  const weixinStatus = weixinBinding?.status ?? 'disabled'
  const weixinStatusLabel = t.statusLabels[weixinStatus] ?? weixinStatus
  const connected = weixinStatus === 'connected'
  // 已连接下再次扫码（重登录）也走同一面板：绑定状态要等确认后才翻转，不能拿 connected 判断。
  const loginFlowActive = login !== null && login.state !== 'confirmed'

  const statusPill = pickStatusPill({
    connected,
    loginFlowActive,
    loginRequired: weixinStatus === 'login_required',
    loginPendingLabel: t.statusLabels.login_pending,
    loginRequiredLabel: t.statusLabels.login_required,
    weixinStatusLabel
  })

  const qrImage = login?.qr_image ?? null

  return (
    <SettingsContent>
      <p className="mb-6 text-[11px] leading-relaxed text-faint">{t.intro}</p>
      <div className="space-y-8">
        <SettingsSubsection intro={t.weixin.intro} title={t.weixin.title}>
          <ListRow
            action={
              connected ? (
                <button className={BTN_SUBTLE} onClick={() => setConfirmLogout(true)} type="button">
                  {t.weixin.logoutAction}
                </button>
              ) : (
                <button className={BTN_PRIMARY} disabled={loginBusy} onClick={() => void beginLogin()} type="button">
                  {loginBusy ? <Spinner /> : <IconBrandWechat className="size-4" />}
                  {login?.state === 'expired' || login?.state === 'error' ? t.weixin.retryAction : t.weixin.loginAction}
                </button>
              )
            }
            description={connected ? t.weixin.connectedAs(weixinBinding?.account_name ?? '') : t.weixin.intro}
            title={
              <span className="flex items-center gap-2">
                <IconBrandWechat className="size-4 text-emerald-400" />
                {t.weixin.title}
                {statusPill ? (
                  <Pill tone={connected && !loginFlowActive ? 'primary' : 'muted'}>{statusPill}</Pill>
                ) : null}
              </span>
            }
          />
          {loginFlowActive ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-line-standard bg-fill-faint px-4 py-5">
              {login.state === 'wait' && qrImage ? (
                isDataImage(qrImage) ? (
                  <img
                    alt={t.weixin.qrAlt}
                    className="size-44 rounded-lg bg-white p-2 object-contain"
                    src={normalizeDataImage(qrImage)}
                  />
                ) : (
                  // 二维码必须固定黑白：彩色/半透明前景会显著降低扫码识别率，属业务约束而非主题遗漏。
                  <div className="flex items-center justify-center rounded-lg bg-white p-2.5 shadow-sm">
                    <QRCodeSVG bgColor="#ffffff" fgColor="#000000" level="M" size={160} value={qrImage} />
                  </div>
                )
              ) : null}
              <div className="text-xs text-muted">
                {login.state === 'wait' ? t.weixin.qrPrompt : null}
                {login.state === 'scaned' ? t.weixin.scanedPrompt : null}
                {login.state === 'expired' ? t.weixin.expiredPrompt : null}
                {login.state === 'error' ? `${login.error ?? t.weixin.loginStartFailed}` : null}
                {login.state === 'confirmed' ? t.weixin.loginSuccess : null}
                {login.state === 'login_required' ? t.statusLabels.login_required : null}
              </div>
              {login.state === 'scaned' ? <Spinner /> : null}
            </div>
          ) : null}
        </SettingsSubsection>

        <SettingsSubsection intro={t.peers.intro} title={t.peers.title}>
          {weixinBinding && peers.length > 0 ? (
            peers.map(peer => (
              <ListRow
                action={
                  <div className="flex items-center gap-2">
                    {peerActionsFor(peer.status, t.peers).map(descriptor => (
                      <button
                        className={descriptor.className}
                        disabled={peerBusy === peer.peer_id}
                        key={descriptor.action}
                        onClick={() => void actOnPeer(peer.peer_id, descriptor.action)}
                        type="button"
                      >
                        {descriptor.label}
                      </button>
                    ))}
                  </div>
                }
                description={peer.peer_name && peer.peer_name !== peer.peer_id ? peer.peer_id : undefined}
                key={peer.peer_id}
                title={
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate">{peer.peer_name || peer.peer_id}</span>
                    <Pill tone={peer.status === 'allowed' ? 'primary' : 'muted'}>
                      {peer.status === 'pending'
                        ? t.peers.pendingLabel
                        : peer.status === 'allowed'
                          ? t.peers.allowedLabel
                          : t.peers.blockedLabel}
                    </Pill>
                  </span>
                }
              />
            ))
          ) : (
            <EmptyState description={t.peers.intro} title={t.peers.empty} />
          )}
        </SettingsSubsection>
      </div>

      <ConfirmDialog
        confirmLabel={t.weixin.logoutAction}
        description={t.weixin.logoutConfirmDescription}
        onConfirm={() => void doLogout()}
        onOpenChange={setConfirmLogout}
        open={confirmLogout}
        title={t.weixin.logoutConfirmTitle}
        variant="destructive"
      />
    </SettingsContent>
  )
}
