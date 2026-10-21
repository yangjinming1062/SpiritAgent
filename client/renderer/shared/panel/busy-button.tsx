import type { ReactNode } from 'react'

import { Spinner } from './components'

/**
 * 带忙碌态的按钮：忙碌时用旋转指示器替换图标并禁用自身。
 * 业务只传「忙碌时做什么」，不自己拼指示器，避免各页各写一套。
 */
export function BusyButton({
  busy,
  busyLabel,
  children,
  className,
  disabled = false,
  icon,
  onClick,
  type = 'button',
  ariaLabel
}: {
  busy: boolean
  /** 忙碌时读屏与提示用的文案；给了就替换图标旁的文字。 */
  busyLabel?: string
  children?: ReactNode
  className?: string
  disabled?: boolean
  /** 非忙碌状态下的图标。 */
  icon?: ReactNode
  onClick?: () => void
  type?: 'button' | 'submit'
  ariaLabel?: string
}): React.JSX.Element {
  return (
    <button aria-label={ariaLabel} className={className} disabled={disabled || busy} onClick={onClick} type={type}>
      {busy ? <Spinner size="sm" /> : icon}
      {busy && busyLabel ? busyLabel : children}
    </button>
  )
}
