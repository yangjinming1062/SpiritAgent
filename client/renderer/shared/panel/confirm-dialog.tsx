import { useId, useLayoutEffect, useRef, useState } from 'react'

import { useAsyncGuard } from '../hooks/use-async-guard'
import { useEscapeKey } from '../hooks/use-escape-key'
import { probeInteractiveRegions, useInteractiveRegion } from '../lib/interactive-regions'
import { log } from '../lib/log'
import { useStrings } from '../strings'

import { BTN_DANGER, BTN_PRIMARY, BTN_SUBTLE, SURFACE_MODAL } from './palette'

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel: string
  variant?: 'default' | 'destructive'
  onConfirm: () => void | Promise<void>
}

export function ConfirmDialog({ open, ...props }: ConfirmDialogProps): React.JSX.Element | null {
  return open ? <OpenConfirmDialog {...props} /> : null
}

// 每次打开独立持有提交状态；关闭、卸载或换号后的回包不能关闭新弹窗。
function OpenConfirmDialog({
  onOpenChange,
  title,
  description,
  confirmLabel,
  variant = 'default',
  onConfirm
}: Omit<ConfirmDialogProps, 'open'>): React.JSX.Element {
  const t = useStrings()
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)
  const begin = useAsyncGuard()
  const overlayRef = useRef<HTMLDivElement>(null)
  const regionId = useId()

  useInteractiveRegion(`confirm-dialog:${regionId}`, overlayRef)
  useLayoutEffect(() => probeInteractiveRegions(), [])
  useEscapeKey(() => onOpenChange(false), { busy })

  const confirm = async (): Promise<void> => {
    if (submitting.current) {
      return
    }

    const isLive = begin()
    submitting.current = true
    setBusy(true)

    try {
      await onConfirm()

      if (isLive()) {
        onOpenChange(false)
      }
    } catch (error) {
      log.warn('confirm-dialog', 'onConfirm failed', error)
    } finally {
      submitting.current = false

      if (isLive()) {
        setBusy(false)
      }
    }
  }

  return (
    <div
      aria-label={title}
      aria-modal="true"
      className="fixed inset-0 z-[80] flex items-center justify-center p-6 [-webkit-app-region:no-drag]"
      onPointerDown={event => {
        if (!busy && event.target === event.currentTarget) {
          onOpenChange(false)
        }
      }}
      ref={overlayRef}
      role="dialog"
    >
      <div className={`w-full max-w-md rounded-2xl p-5 text-strong ${SURFACE_MODAL}`}>
        <h3 className="text-sm font-semibold">{title}</h3>
        {description && <p className="mt-2 text-xs leading-relaxed text-muted">{description}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button className={BTN_SUBTLE} disabled={busy} onClick={() => onOpenChange(false)} type="button">
            {t.common.cancel}
          </button>
          <button
            className={variant === 'destructive' ? BTN_DANGER : BTN_PRIMARY}
            disabled={busy}
            onClick={() => void confirm()}
            type="button"
          >
            {busy ? t.common.processing : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
