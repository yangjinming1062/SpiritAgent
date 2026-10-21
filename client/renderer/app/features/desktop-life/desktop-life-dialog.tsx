import type { ComponentProps, RefObject } from 'react'
import { useLayoutEffect, useRef } from 'react'

import { cycleTabFocus } from '@/shared/lib/focus-cycle'
import { ConfirmDialog, WizardModal } from '@/shared/panel'

function useDialogFocus(content: RefObject<HTMLDivElement | null>): void {
  useLayoutEffect(() => {
    const previous = document.activeElement

    const dialog =
      content.current?.closest<HTMLElement>('[role="dialog"]') ??
      content.current?.querySelector<HTMLElement>('[role="dialog"]')

    if (!dialog) {
      return
    }

    const selector =
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, video[controls], [tabindex="0"]'

    dialog.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true })

    const containFocus = (event: KeyboardEvent): void => {
      if (!event.defaultPrevented) {
        cycleTabFocus(event, dialog, selector, true)
      }
    }

    dialog.addEventListener('keydown', containFocus)

    return () => {
      dialog.removeEventListener('keydown', containFocus)

      if (previous instanceof HTMLElement && previous.isConnected && previous.getClientRects().length) {
        previous.focus({ preventScroll: true })
      }
    }
  }, [content])
}

export function DesktopLifeDialog({ children, ...props }: ComponentProps<typeof WizardModal>): React.JSX.Element {
  const content = useRef<HTMLDivElement>(null)
  useDialogFocus(content)

  return (
    <WizardModal {...props}>
      <div ref={content}>{children}</div>
    </WizardModal>
  )
}

export function DesktopLifeConfirmDialog(props: ComponentProps<typeof ConfirmDialog>): React.JSX.Element | null {
  return props.open ? <FocusedConfirmDialog {...props} /> : null
}

function FocusedConfirmDialog(props: ComponentProps<typeof ConfirmDialog>): React.JSX.Element {
  const content = useRef<HTMLDivElement>(null)
  useDialogFocus(content)

  return (
    <div ref={content}>
      <ConfirmDialog {...props} />
    </div>
  )
}
