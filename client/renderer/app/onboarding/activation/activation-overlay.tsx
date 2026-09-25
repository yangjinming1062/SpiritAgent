import { useStore } from '@nanostores/react'
import { type FormEvent, useRef, useState } from 'react'

import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { Loader2, Sparkles, X } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { cn } from '@/shared/lib/utils'
import { BTN_ICON, BTN_PRIMARY, BTN_SUBTLE, INPUT_CLASS, SURFACE_OVERLAY } from '@/shared/panel'
import { $auth, activate } from '@/shared/store/auth'
import { useStrings } from '@/shared/strings'

/**
 * 激活码输入浮层：在伙伴（精灵）窗口中未鉴权时显示。
 * 用户粘贴 base64 激活码，主进程通过 ``/api/user/activate`` 换取会话 JWT。
 */
export function ActivationOverlay({ onClose }: { onClose: () => void }): React.JSX.Element {
  const auth = useStore($auth)
  const dict = useStrings()
  const t = dict.activation
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<null | string>(null)
  const formRef = useRef<HTMLFormElement>(null)

  // 只让卡片接收鼠标；桌面其他位置继续穿透到下方窗口，且不因点击背景关闭。
  useInteractiveRegion('activation', formRef)

  useEscapeKey(onClose, { capture: false, stopPropagation: false, busy })

  const addAccount = auth.kind === 'authenticated'
  const trimmed = code.trim()

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()

    if (!trimmed || busy) {
      return
    }

    setBusy(true)
    setError(null)

    try {
      await activate({ code: trimmed })
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="pointer-events-none fixed inset-0 z-[1200] flex items-center justify-center p-6">
      <form
        className={`pointer-events-auto relative w-full max-w-lg rounded-2xl p-7 text-strong ${SURFACE_OVERLAY}`}
        onSubmit={onSubmit}
        ref={formRef}
      >
        <div className="mb-5 flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl border border-line-standard bg-fill-faint text-accent">
              <Sparkles className="size-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold tracking-tight">
                {addAccount ? t.addTitle : t.title(dict.brand.name)}
              </h2>
              <p className="text-sm text-faint">{addAccount ? t.addSubtitle : t.subtitle}</p>
            </div>
          </div>
          <button aria-label={t.close} className={BTN_ICON} disabled={busy} onClick={onClose} type="button">
            <X />
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-danger-line bg-danger-bg px-3 py-2 text-sm text-danger-fg">
            {error}
          </div>
        )}

        <textarea
          autoFocus
          className={cn(INPUT_CLASS, 'h-24 resize-none font-mono leading-relaxed')}
          disabled={busy}
          onChange={e => setCode(e.target.value)}
          placeholder={t.placeholder}
          spellCheck={false}
          value={code}
        />

        <div className="mt-5 flex justify-end gap-2">
          <button className={BTN_SUBTLE} disabled={busy} onClick={onClose} type="button">
            {t.cancel}
          </button>
          <button className={cn(BTN_PRIMARY, 'gap-2')} disabled={!trimmed || busy} type="submit">
            {busy ? <Loader2 className="size-4 animate-spin" /> : null}
            {busy ? t.submitBusy : t.submit}
          </button>
        </div>
      </form>
    </div>
  )
}
