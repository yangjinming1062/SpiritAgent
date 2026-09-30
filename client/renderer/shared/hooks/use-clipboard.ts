import { useEffect, useRef, useState } from 'react'

type ClipboardStatus = 'idle' | 'copied' | 'failed'

export function useClipboard(): { status: ClipboardStatus; copy: (text: string) => Promise<void> } {
  const [status, setStatus] = useState<ClipboardStatus>('idle')
  const version = useRef(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      version.current += 1

      if (timer.current) {
        clearTimeout(timer.current)
      }
    }
  }, [])

  function showResult(result: ClipboardStatus, requestVersion: number): void {
    if (requestVersion !== version.current) {
      return
    }

    if (timer.current) {
      clearTimeout(timer.current)
    }

    setStatus(result)
    timer.current = setTimeout(() => {
      setStatus('idle')
      timer.current = null
    }, 1500)
  }

  async function copy(text: string): Promise<void> {
    const requestVersion = ++version.current

    try {
      if (window.spiritagent?.writeClipboard) {
        await window.spiritagent.writeClipboard(text)
      } else {
        await navigator.clipboard.writeText(text)
      }
    } catch (error) {
      showResult('failed', requestVersion)
      throw error
    }

    showResult('copied', requestVersion)
  }

  return { status, copy }
}
