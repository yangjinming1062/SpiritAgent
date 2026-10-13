import { IconDeviceMobile } from '@tabler/icons-react'
import { QRCodeSVG } from 'qrcode.react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { useClipboard } from '@/shared/hooks/use-clipboard'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import {
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_SUBTLE,
  ConfirmDialog,
  EmptyState,
  ListRow,
  SettingsContent,
  SettingsSubsection,
  Spinner
} from '@/shared/panel'
import {
  cancelRemotePairing,
  createRemotePairing,
  getRemotePairing,
  listRemoteDevices,
  revokeAllRemoteDevices,
  revokeRemoteDevice
} from '@/shared/spiritagent'
import { notify, notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { RemoteDevice, RemotePairing } from '@protocol'

const PAIRING_POLL_MS = 2000
const DEVICES_REFRESH_MS = 15000

export function RemotePage(): React.JSX.Element {
  const t = useStrings().settings.remote
  const active = usePanelActivity()
  const beginGuard = useAsyncGuard()
  const { status: clipboardStatus, copy } = useClipboard()
  const [devices, setDevices] = useState<RemoteDevice[]>([])
  const [pairing, setPairing] = useState<RemotePairing | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [now, setNow] = useState(Date.now())
  const [revokeTarget, setRevokeTarget] = useState<number | 'all' | null>(null)
  const pairingRevision = useRef(0)
  const devicesRevision = useRef(0)

  const reloadDevices = useCallback(async (): Promise<void> => {
    const isLive = beginGuard()
    const revision = ++devicesRevision.current

    try {
      const result = await listRemoteDevices()

      if (isLive() && revision === devicesRevision.current) {
        setDevices(result.items)
        setLoadError('')
      }
    } catch (cause) {
      if (isLive() && revision === devicesRevision.current) {
        setLoadError(backendDetailMessage(cause, t.loadFailed))
      }
    } finally {
      if (isLive() && revision === devicesRevision.current) {
        setLoading(false)
      }
    }
  }, [beginGuard, t.loadFailed])

  useEffect(() => {
    if (!active) {
      return
    }

    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined

    const refresh = async (): Promise<void> => {
      await reloadDevices()

      if (live) {
        timer = setTimeout(() => void refresh(), DEVICES_REFRESH_MS)
      }
    }

    const onFocus = (): void => {
      void reloadDevices()
    }

    void refresh()
    window.addEventListener('focus', onFocus)

    return () => {
      live = false
      devicesRevision.current += 1
      clearTimeout(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [active, reloadDevices])

  const pairingId = pairing?.id
  const pairingState = pairing?.state
  const pairingExpiresAt = pairing?.expires_at

  useEffect(() => {
    if (!active || busy || pairingId === undefined || pairingState !== 'pending' || !pairingExpiresAt) {
      return
    }

    let live = true
    let failures = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const revision = pairingRevision.current
    const isCurrent = beginGuard()
    const isLive = (): boolean => live && isCurrent() && revision === pairingRevision.current
    const expiry = Date.parse(pairingExpiresAt)

    const tick = window.setInterval(() => {
      if (isLive()) {
        setNow(Date.now())
      }
    }, 1000)

    const poll = async (): Promise<void> => {
      if (!isLive()) {
        return
      }

      if (Date.now() >= expiry) {
        setPairing(current => (current?.id === pairingId ? { ...current, state: 'expired' } : current))

        return
      }

      try {
        const result = await getRemotePairing(pairingId)

        if (!isLive()) {
          return
        }

        failures = 0
        setPairing(current => (current?.id === pairingId ? { ...current, ...result } : current))

        if (result.state === 'paired') {
          notify({ kind: 'success', message: t.paired })
          await reloadDevices()

          return
        }

        if (result.state !== 'pending') {
          return
        }
      } catch (cause) {
        if (!isLive()) {
          return
        }

        failures += 1

        if (failures >= 5) {
          setError(backendDetailMessage(cause, t.pollFailed))

          return
        }
      }

      if (isLive()) {
        timer = setTimeout(() => void poll(), PAIRING_POLL_MS)
      }
    }

    void poll()

    return () => {
      live = false
      clearTimeout(timer)
      window.clearInterval(tick)
    }
  }, [active, beginGuard, busy, pairingExpiresAt, pairingId, pairingState, reloadDevices, t.paired, t.pollFailed])

  async function updatePairing(request: () => Promise<RemotePairing>, failureMessage: string): Promise<void> {
    const isLive = beginGuard()
    const revision = ++pairingRevision.current
    setBusy(true)
    setError('')

    try {
      const result = await request()

      if (isLive() && revision === pairingRevision.current) {
        setPairing(result)
        setNow(Date.now())

        if (result.state === 'paired') {
          await reloadDevices()
        }
      }
    } catch (cause) {
      if (isLive() && revision === pairingRevision.current) {
        setError(backendDetailMessage(cause, failureMessage))
      }
    } finally {
      if (isLive() && revision === pairingRevision.current) {
        setBusy(false)
      }
    }
  }

  function connect(): Promise<void> {
    return updatePairing(createRemotePairing, t.connectFailed)
  }

  async function cancelCode(): Promise<void> {
    if (!pairing) {
      return
    }

    const id = pairing.id
    await updatePairing(async () => {
      await cancelRemotePairing(id)

      return getRemotePairing(id)
    }, t.cancelFailed)
  }

  async function copyLink(): Promise<void> {
    if (!pairing?.url) {
      return
    }

    const isLive = beginGuard()

    try {
      await copy(pairing.url)
    } catch (cause) {
      if (isLive()) {
        notifyError(cause, t.copyFailed)
      }
    }
  }

  async function revoke(): Promise<void> {
    if (revokeTarget === null) {
      return
    }

    const isLive = beginGuard()
    devicesRevision.current += 1

    try {
      if (revokeTarget === 'all') {
        await revokeAllRemoteDevices()
      } else {
        await revokeRemoteDevice(revokeTarget)
      }

      if (isLive()) {
        await reloadDevices()
      }
    } catch (cause) {
      if (isLive()) {
        notifyError(cause, t.revokeFailed)
      }

      throw cause
    }
  }

  const remaining = Math.max(0, Math.ceil(((pairing ? Date.parse(pairing.expires_at) : now) - now) / 1000))
  const pending = pairing?.state === 'pending' && remaining > 0
  const formatTime = (value: string): string => new Date(value).toLocaleString()

  return (
    <SettingsContent>
      <div className="mb-7 flex items-center gap-3">
        <IconDeviceMobile className="size-7 text-accent" />
        <div>
          <h2 className="text-lg font-semibold">{t.heading}</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted">{t.intro}</p>
        </div>
      </div>
      {error || loadError ? (
        <p className="mb-5 text-xs text-danger-fg" role="alert">
          {error || loadError}
        </p>
      ) : null}
      <div className="space-y-7">
        <SettingsSubsection title={t.connect}>
          <div className="flex flex-col items-center gap-4 rounded-2xl border border-line-standard bg-fill-faint p-6">
            {pending && pairing?.url ? (
              <div aria-label={t.connect} className="rounded-xl bg-white p-3">
                <QRCodeSVG bgColor="#ffffff" fgColor="#000000" level="M" size={192} value={pairing.url} />
              </div>
            ) : null}
            <p aria-live="polite" className="text-xs text-muted">
              {pending
                ? t.qrPrompt
                : pairing?.state === 'paired'
                  ? t.paired
                  : pairing?.state === 'cancelled'
                    ? t.cancelled
                    : pairing
                      ? t.expired
                      : t.qrPrompt}
            </p>
            {pending ? <p className="text-xs tabular-nums text-faint">{t.expiry(remaining)}</p> : null}
            <div className="flex flex-wrap justify-center gap-2">
              <button className={BTN_PRIMARY} disabled={busy || !active} onClick={() => void connect()} type="button">
                {busy ? <Spinner /> : <IconDeviceMobile className="size-4" />}
                {pairing ? t.refreshCode : t.connect}
              </button>
              {pending ? (
                <>
                  <button
                    className={BTN_GHOST}
                    disabled={busy || !active}
                    onClick={() => void copyLink()}
                    type="button"
                  >
                    {clipboardStatus === 'copied' ? t.copied : t.copyLink}
                  </button>
                  <button
                    className={BTN_SUBTLE}
                    disabled={busy || !active}
                    onClick={() => void cancelCode()}
                    type="button"
                  >
                    {t.cancelCode}
                  </button>
                </>
              ) : null}
            </div>
          </div>
        </SettingsSubsection>
        <SettingsSubsection intro={t.devicesIntro} title={t.devices}>
          <div className="mb-3 flex justify-end gap-2">
            <button className={BTN_SUBTLE} disabled={!active} onClick={() => void reloadDevices()} type="button">
              {t.refresh}
            </button>
            {devices.length > 0 ? (
              <button className={BTN_GHOST} disabled={!active} onClick={() => setRevokeTarget('all')} type="button">
                {t.revokeAll}
              </button>
            ) : null}
          </div>
          {loading ? (
            <Spinner />
          ) : devices.length === 0 ? (
            <EmptyState title={t.empty} />
          ) : (
            devices.map(device => (
              <ListRow
                action={
                  <button
                    className={BTN_GHOST}
                    disabled={!active}
                    onClick={() => setRevokeTarget(device.id)}
                    type="button"
                  >
                    {t.revoke}
                  </button>
                }
                description={
                  <span>
                    {t.lastSeen}：{formatTime(device.last_seen_at)}
                    <br />
                    {t.expires}：{formatTime(device.expires_at)}
                  </span>
                }
                key={device.id}
                title={device.name}
              />
            ))
          )}
        </SettingsSubsection>
      </div>
      <ConfirmDialog
        confirmLabel={t.revoke}
        description={t.revokeDescription}
        onConfirm={revoke}
        onOpenChange={open => {
          if (!open) {
            setRevokeTarget(null)
          }
        }}
        open={revokeTarget !== null && active}
        title={revokeTarget === 'all' ? t.revokeAllTitle : t.revokeTitle}
        variant="destructive"
      />
    </SettingsContent>
  )
}
