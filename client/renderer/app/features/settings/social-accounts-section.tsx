import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { usePanelActivity } from '@/shared/context/panel-activity'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { failureText, ipcErrorStatus } from '@/shared/lib/ipc-error'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import {
  BTN_DANGER,
  BTN_PRIMARY,
  BTN_SUBTLE,
  BusyButton,
  ErrorBanner,
  FIELD_LABEL,
  HINT_TEXT,
  INPUT_CLASS,
  Pill,
  SECTION_TITLE,
  SettingCard,
  SETTINGS_ROW_TITLE,
  Spinner
} from '@/shared/panel'
import { $auth } from '@/shared/store/auth'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'

import type { ApiResponse } from '../../../../../shared/api'
import { SOCIAL_PLATFORMS } from '../../../../../shared/api.generated'

type SocialAccount = ApiResponse<'social_accounts.list_social_accounts'>['accounts'][number]
type SocialLogin = ApiResponse<'social_accounts.get_social_login'>
type SourceAction = 'save' | 'clear' | 'login' | 'check' | 'verify' | 'cancel'

export function SocialAccountsSection(): React.JSX.Element | null {
  const auth = useStore($auth)
  const active = usePanelActivity()

  return active && auth.kind === 'authenticated' ? <SocialAccountsSession key={auth.snapshot.sessionId} /> : null
}

function SocialAccountsSession(): React.JSX.Element {
  const t = useStrings().settings.socialAccounts
  const begin = useAsyncGuard()
  const loadRevision = useRef(0)
  const [accounts, setAccounts] = useState<SocialAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async (): Promise<void> => {
    const isLive = begin()
    const revision = ++loadRevision.current
    setLoading(true)
    setError('')

    try {
      const result = await requestResource('social_accounts.list_social_accounts')

      if (isLive() && revision === loadRevision.current) {
        setAccounts(result.accounts)
      }
    } catch (err) {
      if (isLive() && revision === loadRevision.current) {
        setError(failureText(err, t.loadFailed))
      }
    } finally {
      if (isLive() && revision === loadRevision.current) {
        setLoading(false)
      }
    }
  }, [begin, t.loadFailed])

  useEffect(() => {
    void load()
  }, [load])

  const update = useCallback((account: SocialAccount): void => {
    loadRevision.current += 1
    setLoading(false)
    setError('')
    setAccounts(current => current.map(previous => (previous.platform === account.platform ? account : previous)))
  }, [])

  return (
    <section className="space-y-2">
      <h3 className={SECTION_TITLE}>{t.title}</h3>
      <p className={cn(HINT_TEXT, 'max-w-xl')}>{t.hint}</p>
      {loading && !accounts.length ? <Spinner /> : null}
      <ErrorBanner message={error} onRetry={() => void load()} retryLabel={t.retry} />
      {accounts.map(account => (
        <SocialAccountCard account={account} key={account.platform} onReload={load} onUpdate={update} />
      ))}
    </section>
  )
}

function SocialAccountCard({
  account,
  onReload,
  onUpdate
}: {
  account: SocialAccount
  onReload: () => Promise<void>
  onUpdate: (account: SocialAccount) => void
}): React.JSX.Element {
  const t = useStrings().settings.socialAccounts
  const locale = useStore($locale)
  const begin = useAsyncGuard()
  const id = useId()
  const submitting = useRef(false)
  const operationRevision = useRef(0)
  const [busy, setBusy] = useState<SourceAction | null>(null)
  const [error, setError] = useState('')
  const [credentials, setCredentials] = useState('')
  const [code, setCode] = useState('')
  const [qrImage, setQrImage] = useState<string | null>(null)
  const [login, setLogin] = useState<SocialLogin | null>(account.login)
  const [now, setNow] = useState(Date.now)
  const [visible, setVisible] = useState(document.visibilityState === 'visible')
  const [pollingStopped, setPollingStopped] = useState(false)
  const sessionId = login?.session_id
  const expiresAt = login?.expires_at ? Date.parse(login.expires_at) : null
  const loggedIn = login?.state === 'logged_in'
  const expired = login?.state === 'expired' || (expiresAt !== null && expiresAt <= now && !loggedIn)

  const pending = Boolean(
    sessionId && login?.state && ['awaiting_scan', 'sms_required', 'verifying'].includes(login.state)
  )

  const waiting = pending && !expired
  const platformName = SOCIAL_PLATFORMS[account.platform].labels[locale]

  useEffect(() => {
    const updateVisibility = (): void => setVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', updateVisibility)

    return () => document.removeEventListener('visibilitychange', updateVisibility)
  }, [])

  useEffect(() => {
    if (!sessionId || expiresAt === null || loggedIn) {
      return
    }

    const timer = window.setInterval(() => {
      const current = Date.now()
      setNow(current)

      if (current >= expiresAt) {
        setCode('')
        window.clearInterval(timer)
      }
    }, 1000)

    return () => window.clearInterval(timer)
  }, [expiresAt, loggedIn, sessionId])

  const applyLogin = useCallback(
    (result: SocialLogin): void => {
      setLogin(result)
      setPollingStopped(false)

      if (result.state !== 'sms_required') {
        setCode('')
      }

      if (result.state === 'logged_in') {
        setQrImage(null)
        void onReload()
      }
    },
    [onReload]
  )

  useEffect(() => {
    if (!pending || !visible || busy || pollingStopped || !sessionId) {
      return
    }

    const isLive = begin()
    const revision = operationRevision.current
    let stopped = false
    let failures = 0
    let timer: number | undefined

    const poll = async (): Promise<void> => {
      try {
        const result = await requestResource('social_accounts.get_social_login', {
          path: { platform: account.platform, session_id: sessionId }
        })

        if (stopped || !isLive() || operationRevision.current !== revision) {
          return
        }

        failures = 0
        applyLogin(result)

        if (result.state === 'logged_in' || result.state === 'expired' || result.state === 'verification_required') {
          return
        }
      } catch (err) {
        if (stopped || !isLive() || operationRevision.current !== revision) {
          return
        }

        failures += 1

        if (failures >= 3 || ipcErrorStatus(err) === 409 || ipcErrorStatus(err) === 410) {
          setError(failureText(err, t.operationFailed))
          setPollingStopped(true)

          return
        }
      }

      if (!stopped && isLive()) {
        timer = window.setTimeout(() => void poll(), 2000)
      }
    }

    timer = window.setTimeout(() => void poll(), 2000)

    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [account.platform, applyLogin, begin, busy, pending, pollingStopped, sessionId, t.operationFailed, visible])

  const runAction = async <T,>(
    action: SourceAction,
    request: () => Promise<T>,
    apply: (value: T) => void
  ): Promise<void> => {
    if (submitting.current) {
      return
    }

    submitting.current = true
    operationRevision.current += 1
    const isLive = begin()
    setBusy(action)
    setError('')

    try {
      const result = await request()

      if (isLive()) {
        apply(result)
      }
    } catch (err) {
      if (isLive()) {
        setError(failureText(err, t.operationFailed))
      }
    } finally {
      submitting.current = false

      if (isLive()) {
        setBusy(null)
      }
    }
  }

  const applyAccount = (result: SocialAccount): void => {
    setCredentials('')
    setCode('')
    setQrImage(null)
    setLogin(result.login)
    setPollingStopped(false)
    onUpdate(result)
  }

  const saveCredentials = (): Promise<void> =>
    runAction(
      'save',
      () =>
        requestResource('social_accounts.put_social_credentials', {
          path: { platform: account.platform },
          body: { credentials }
        }),
      applyAccount
    )

  const clearConnection = (): Promise<void> =>
    runAction(
      'clear',
      () => requestResource('social_accounts.clear_social_account', { path: { platform: account.platform } }),
      applyAccount
    )

  const startLogin = (): Promise<void> =>
    runAction(
      'login',
      () => requestResource('social_accounts.start_social_login', { path: { platform: account.platform } }),
      result => {
        setCode('')
        setPollingStopped(false)
        setQrImage(result.image)
        setNow(Date.now())
        setLogin({
          session_id: result.session_id,
          state: 'awaiting_scan',
          expires_at: result.expires_at,
          verification_error: null
        })
      }
    )

  const checkLogin = (): Promise<void> =>
    sessionId
      ? runAction(
          'check',
          () =>
            requestResource('social_accounts.get_social_login', {
              path: { platform: account.platform, session_id: sessionId }
            }),
          applyLogin
        )
      : Promise.resolve()

  const cancelLogin = (): Promise<void> =>
    sessionId
      ? runAction(
          'cancel',
          () =>
            requestResource('social_accounts.cancel_social_login', {
              path: { platform: account.platform, session_id: sessionId }
            }),
          applyAccount
        )
      : Promise.resolve()

  const verifyLogin = (): Promise<void> => {
    if (!sessionId || !/^[0-9]{6}$/.test(code)) {
      return Promise.resolve()
    }

    const submittedCode = code
    setCode('')

    return runAction(
      'verify',
      () =>
        requestResource('social_accounts.verify_social_login', {
          path: { platform: account.platform, session_id: sessionId },
          body: { code: submittedCode }
        }),
      applyLogin
    )
  }

  return (
    <SettingCard divided={false}>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className={SETTINGS_ROW_TITLE}>{platformName}</h4>
          <Pill tone={account.state === 'login_required' ? 'warning' : account.configured ? 'accent' : 'muted'}>
            {t.states[account.state]}
          </Pill>
        </div>
        {!account.available ? <p className={HINT_TEXT}>{t.unavailable}</p> : null}
        {account.auth_kind === 'cookie' ? (
          <form
            onSubmit={event => {
              event.preventDefault()
              void saveCredentials()
            }}
          >
            <label className={FIELD_LABEL} htmlFor={`${id}-credentials`}>
              {t.cookieLabel}
            </label>
            <textarea
              autoCapitalize="none"
              autoComplete="off"
              className={cn(INPUT_CLASS, 'min-h-24 resize-y font-mono')}
              disabled={!account.available || Boolean(busy)}
              id={`${id}-credentials`}
              maxLength={30000}
              onChange={event => setCredentials(event.currentTarget.value)}
              placeholder={t.cookiePlaceholder}
              rows={3}
              spellCheck={false}
              value={credentials}
            />
            <p className={cn(HINT_TEXT, 'mt-1')}>{t.cookieHint}</p>
            <BusyButton
              busy={busy === 'save'}
              busyLabel={t.saving}
              className={cn(BTN_PRIMARY, 'mt-3')}
              disabled={!account.available || Boolean(busy) || !credentials.trim()}
              type="submit"
            >
              {t.save}
            </BusyButton>
          </form>
        ) : null}
        {account.auth_kind === 'qr' ? (
          <div className="space-y-3">
            <p className={HINT_TEXT}>{t.qrHint(platformName)}</p>
            {qrImage && !expired ? (
              <img
                alt={t.qrAlt(platformName)}
                className="size-48 max-w-full rounded-lg bg-white p-2"
                height={192}
                src={qrImage}
                width={192}
              />
            ) : null}
            {login?.state ? (
              <p aria-live="polite" className="text-xs text-body" role="status">
                {t.loginStates[expired ? 'expired' : login.state]}
              </p>
            ) : null}
            {waiting && !qrImage ? <p className={HINT_TEXT}>{t.qrResumed}</p> : null}
            {waiting && expiresAt !== null ? (
              <p className={HINT_TEXT}>{t.expiresIn(Math.max(0, Math.ceil((expiresAt - now) / 1000)))}</p>
            ) : null}
            {login?.state === 'sms_required' && !expired ? (
              <form
                className="space-y-2"
                onSubmit={event => {
                  event.preventDefault()
                  void verifyLogin()
                }}
              >
                <label className={FIELD_LABEL} htmlFor={`${id}-code`}>
                  {t.codeLabel}
                </label>
                <p className={HINT_TEXT}>{t.codeHint}</p>
                <div className="flex flex-wrap gap-2">
                  <input
                    autoComplete="one-time-code"
                    className={cn(INPUT_CLASS, 'max-w-40')}
                    disabled={Boolean(busy) || !account.available}
                    id={`${id}-code`}
                    inputMode="numeric"
                    maxLength={6}
                    minLength={6}
                    onChange={event => setCode(event.currentTarget.value.replace(/[^0-9]/g, ''))}
                    pattern="[0-9]{6}"
                    required
                    type="text"
                    value={code}
                  />
                  <BusyButton
                    busy={busy === 'verify'}
                    busyLabel={t.submittingCode}
                    className={BTN_PRIMARY}
                    disabled={Boolean(busy) || !account.available || !/^[0-9]{6}$/.test(code)}
                    type="submit"
                  >
                    {t.submitCode}
                  </BusyButton>
                </div>
                {login.verification_error === 'invalid_code' ? (
                  <p className="text-xs text-danger-fg" role="alert">
                    {t.invalidCode}
                  </p>
                ) : null}
              </form>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <BusyButton
                busy={busy === 'login'}
                busyLabel={t.refreshing}
                className={BTN_PRIMARY}
                disabled={Boolean(busy) || !account.available}
                onClick={() => void startLogin()}
              >
                {login ? t.refreshQr : t.login}
              </BusyButton>
              {sessionId && !expired && !loggedIn ? (
                <>
                  <BusyButton
                    busy={busy === 'check'}
                    className={BTN_SUBTLE}
                    disabled={Boolean(busy) || !account.available}
                    onClick={() => void checkLogin()}
                  >
                    {t.check}
                  </BusyButton>
                  <BusyButton
                    busy={busy === 'cancel'}
                    busyLabel={t.canceling}
                    className={BTN_SUBTLE}
                    disabled={Boolean(busy)}
                    onClick={() => void cancelLogin()}
                  >
                    {t.cancel}
                  </BusyButton>
                </>
              ) : null}
            </div>
          </div>
        ) : null}
        {account.configured || account.state === 'login_required' ? (
          <BusyButton
            busy={busy === 'clear'}
            busyLabel={t.clearing}
            className={BTN_DANGER}
            disabled={Boolean(busy)}
            onClick={() => void clearConnection()}
          >
            {t.clear}
          </BusyButton>
        ) : null}
        <ErrorBanner message={error} />
        {pollingStopped && waiting ? <p className={HINT_TEXT}>{t.pollingStopped}</p> : null}
      </div>
    </SettingCard>
  )
}
