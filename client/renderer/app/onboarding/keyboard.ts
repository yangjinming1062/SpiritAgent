import type { KeyboardEvent } from 'react'

export function submitOnEnter(event: KeyboardEvent<HTMLElement>, submit: () => void): void {
  if (
    event.key !== 'Enter' ||
    event.nativeEvent.isComposing ||
    event.nativeEvent.keyCode === 229 ||
    event.shiftKey ||
    event.ctrlKey ||
    event.altKey ||
    event.metaKey
  ) {
    return
  }

  event.preventDefault()

  if (!event.repeat) {
    submit()
  }
}
