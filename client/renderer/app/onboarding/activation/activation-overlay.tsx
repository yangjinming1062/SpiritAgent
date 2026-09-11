import { useStore } from '@nanostores/react'
import { type FormEvent, useRef, useState } from 'react'

import { useInteractiveRegion } from '@/modules/character'
import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { Loader2, Sparkles, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_ICON, BTN_PRIMARY, BTN_SUBTLE, INPUT_CLASS } from '@/shared/panel'
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
  const overlayRef = useRef<HTMLDivElement>(null)

  // 把全出血浮层注册为可交互区域，让 textarea 与提交按钮
  // 在默认鼠标穿透的精灵窗口里仍然可以点击——不注册的话，
  // 窗口的 setIgnoreMouseEvents(true, ...) 会吞掉所有点击。
  // 镜像 BootFailureOverlay 的模式。
  useInteractiveRegion('activation', overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  useEscapeKey(onClose, { capture: false, stopPropagation: false, busy })

  const error = auth.kind === 'unauthenticated' ? auth.error : null
  const trimmed = code.trim()

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()

    if (!trimmed || busy) {
      return
    }

    setBusy(true)

    try {
      await activate({ code: trimmed })
      onClose()
    } catch {
      // 错误信息挂在 $auth.error 上，banner 会读取它。
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[1200] flex items-center justify-center bg-black/75 p-6"
      onClick={e => {
        if (e.target === e.currentTarget && !busy) {
          onClose()
        }
      }}
      ref={overlayRef}
    >
      <form
        className="relative w-full max-w-lg rounded-2xl border border-line-standard bg-surface-panel p-7 text-strong shadow-2xl"
        onSubmit={onSubmit}
      >
        <div className="mb-5 flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl border border-line-standard bg-fill-faint text-accent">
              <Sparkles className="size-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold tracking-tight">{t.title(dict.brand.name)}</h2>
              <p className="text-sm text-faint">{t.subtitle}</p>
            </div>
          </div>
          <button aria-label={t.close} className={BTN_ICON} disabled={busy} onClick={onClose} type="button">
            <X />
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-rose-300/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
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
