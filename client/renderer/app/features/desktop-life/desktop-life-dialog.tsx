import type { ComponentProps } from 'react'
import { useRef } from 'react'

import { useDialogFocus } from '@/shared/hooks/use-dialog-focus'
import { WizardModal } from '@/shared/panel'

export function DesktopLifeDialog({ children, ...props }: ComponentProps<typeof WizardModal>): React.JSX.Element {
  const content = useRef<HTMLDivElement>(null)
  useDialogFocus(content, { restoreFocus: true })

  return (
    <WizardModal {...props}>
      <div ref={content}>{children}</div>
    </WizardModal>
  )
}
