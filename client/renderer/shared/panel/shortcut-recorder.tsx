import { Fragment, useCallback, useEffect, useRef, useState } from 'react'

import { AlertCircle, Check, Pencil, RefreshCw, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { BTN_GHOST, BTN_ICON } from './palette'

interface ShortcutRecorderProps {
  defaultValue?: string
  disabled?: boolean
  error?: string
  onChange: (accelerator: string) => void
  registered?: boolean
  value: string
}

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPod|iPad/i.test(navigator.userAgent)

function formatKeyLabel(token: string): string {
  const t = token.trim()
  const lower = t.toLowerCase()

  if (lower === 'commandorcontrol' || lower === 'cmdorctrl') {
    return IS_MAC ? '⌘' : 'Ctrl'
  }

  if (lower === 'ctrl' || lower === 'control') {
    return IS_MAC ? '⌃' : 'Ctrl'
  }

  if (lower === 'alt' || lower === 'option') {
    return IS_MAC ? '⌥' : 'Alt'
  }

  if (lower === 'shift') {
    return IS_MAC ? '⇧' : 'Shift'
  }

  if (lower === 'super' || lower === 'meta' || lower === 'command' || lower === 'cmd') {
    return IS_MAC ? '⌘' : 'Win'
  }

  if (lower === 'up') {
    return '↑'
  }

  if (lower === 'down') {
    return '↓'
  }

  if (lower === 'left') {
    return '←'
  }

  if (lower === 'right') {
    return '→'
  }

  if (lower === 'return' || lower === 'enter') {
    return 'Enter'
  }

  if (lower === 'space') {
    return 'Space'
  }

  if (lower === 'escape' || lower === 'esc') {
    return 'Esc'
  }

  return t.length === 1 ? t.toUpperCase() : t
}

function parseAcceleratorTokens(accelerator: string): string[] {
  if (!accelerator || !accelerator.trim()) {
    return []
  }

  return accelerator
    .split('+')
    .map(s => s.trim())
    .filter(Boolean)
}

function modifiersFromEvent(e: KeyboardEvent): string[] {
  const held: string[] = []

  if (e.ctrlKey) {
    held.push(IS_MAC ? 'Control' : 'CommandOrControl')
  }

  if (e.altKey) {
    held.push('Alt')
  }

  if (e.shiftKey) {
    held.push('Shift')
  }

  if (e.metaKey) {
    held.push(IS_MAC ? 'CommandOrControl' : 'Super')
  }

  return held
}

function normalizeCodeToBaseKey(e: KeyboardEvent): string | null {
  const code = e.code
  const key = e.key

  if (['Control', 'Shift', 'Alt', 'Meta'].includes(key)) {
    return null
  }

  if (/^Key[A-Z]$/.test(code)) {
    return code.slice(3)
  }

  if (/^Digit[0-9]$/.test(code)) {
    return code.slice(5)
  }

  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) {
    return code
  }

  if (/^Numpad[0-9]$/.test(code)) {
    return `num${code.slice(6)}`
  }

  switch (code) {
    case 'Space':
      return 'Space'

    case 'Enter':

    case 'NumpadEnter':
      return 'Return'

    case 'Tab':
      return 'Tab'

    case 'ArrowUp':
      return 'Up'

    case 'ArrowDown':
      return 'Down'

    case 'ArrowLeft':
      return 'Left'

    case 'ArrowRight':
      return 'Right'

    case 'Home':
      return 'Home'

    case 'End':
      return 'End'

    case 'PageUp':
      return 'PageUp'

    case 'PageDown':
      return 'PageDown'

    case 'Insert':
      return 'Insert'

    case 'Delete':
      return 'Delete'

    case 'Backquote':
      return '`'

    case 'Minus':

    case 'NumpadSubtract':
      return '-'

    case 'Equal':
      return '='

    case 'BracketLeft':
      return '['

    case 'BracketRight':
      return ']'

    case 'Backslash':
      return '\\'

    case 'Semicolon':
      return ';'

    case 'Quote':
      return "'"

    case 'Comma':
      return ','

    case 'Period':

    case 'NumpadDecimal':
      return '.'

    case 'Slash':

    case 'NumpadDivide':
      return '/'

    case 'NumpadAdd':
      return 'plus'

    case 'NumpadMultiply':
      return '*'

    default:
      if (key && key.length === 1 && !/\s/.test(key)) {
        return key.toUpperCase()
      }

      return null
  }
}

export function ShortcutRecorder({
  value,
  onChange,
  defaultValue,
  registered = true,
  error,
  disabled = false
}: ShortcutRecorderProps): React.JSX.Element {
  const [recording, setRecording] = useState(false)
  const [heldModifiers, setHeldModifiers] = useState<string[]>([])
  const containerRef = useRef<HTMLDivElement>(null)

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!recording) {
        return
      }

      e.preventDefault()
      e.stopPropagation()

      if (e.key === 'Escape') {
        setRecording(false)
        setHeldModifiers([])

        return
      }

      if ((e.key === 'Backspace' || e.key === 'Delete') && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey) {
        onChange('')
        setRecording(false)
        setHeldModifiers([])

        return
      }

      const currentHeld = modifiersFromEvent(e)
      const baseKey = normalizeCodeToBaseKey(e)

      if (!baseKey) {
        setHeldModifiers(currentHeld)

        return
      }

      const parts = [...currentHeld]

      if (!parts.includes(baseKey)) {
        parts.push(baseKey)
      }

      // 单个非功能键不允许无修饰符注册（防止拦截全局单个字符输入，F1-F24 允许裸按）
      const isFunctionKey = /^F([1-9]|1[0-9]|2[0-4])$/.test(baseKey)

      if (parts.length > 1 || isFunctionKey) {
        const accelerator = parts.join('+')
        onChange(accelerator)
        setRecording(false)
        setHeldModifiers([])
      }
    },
    [recording, onChange]
  )

  const handleKeyUp = useCallback(
    (e: KeyboardEvent) => {
      if (!recording) {
        return
      }

      const currentHeld = modifiersFromEvent(e)
      setHeldModifiers(currentHeld)
    },
    [recording]
  )

  useEffect(() => {
    if (!recording) {
      return
    }

    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setRecording(false)
        setHeldModifiers([])
      }
    }

    window.addEventListener('keydown', handleKeyDown, true)
    window.addEventListener('keyup', handleKeyUp, true)
    window.addEventListener('mousedown', handleClickOutside, true)

    return () => {
      window.removeEventListener('keydown', handleKeyDown, true)
      window.removeEventListener('keyup', handleKeyUp, true)
      window.removeEventListener('mousedown', handleClickOutside, true)
    }
  }, [recording, handleKeyDown, handleKeyUp])

  const tokens = parseAcceleratorTokens(value)
  const isCustomized = defaultValue !== undefined && value !== defaultValue

  return (
    <div className="flex flex-col gap-1.5" ref={containerRef}>
      <div className="flex items-center gap-2">
        <button
          aria-label={recording ? '正在录制快捷键' : '点击修改快捷键'}
          className={cn(
            'group relative flex min-h-8 min-w-44 items-center justify-between gap-2 rounded-lg border px-3 py-1 text-xs transition select-none',
            recording
              ? 'border-accent bg-accent/10 ring-1 ring-accent'
              : error
                ? 'border-danger-line bg-danger-bg hover:border-danger-line'
                : 'border-line-standard bg-fill-faint hover:border-line-strong hover:bg-fill-hover',
            disabled && 'pointer-events-none opacity-40'
          )}
          disabled={disabled}
          onClick={() => {
            if (!recording) {
              setRecording(true)
              setHeldModifiers([])
            }
          }}
          type="button"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            {recording ? (
              heldModifiers.length > 0 ? (
                heldModifiers.map((mod, i) => (
                  <kbd
                    className="inline-flex h-5 items-center rounded border border-accent/40 bg-accent/20 px-1.5 font-mono text-[11px] font-medium text-accent shadow-xs"
                    key={i}
                  >
                    {formatKeyLabel(mod)}
                  </kbd>
                ))
              ) : (
                <span className="flex items-center gap-1.5 text-[11px] text-accent animate-pulse font-medium">
                  请按下组合键…
                </span>
              )
            ) : tokens.length > 0 ? (
              tokens.map((token, i) => (
                <Fragment key={i}>
                  {i > 0 && <span className="text-[11px] font-medium text-muted">+</span>}
                  <kbd className="inline-flex h-5 items-center rounded border border-line-strong bg-fill-hover px-1.5 font-mono text-[11px] font-medium text-strong shadow-xs">
                    {formatKeyLabel(token)}
                  </kbd>
                </Fragment>
              ))
            ) : (
              <span className="text-[11px] text-faint">未设置</span>
            )}
          </div>

          <div className="flex items-center gap-1.5 pl-2 text-faint group-hover:text-muted">
            {recording ? (
              <span className="text-[10px] text-faint">Esc 取消</span>
            ) : (
              <>
                {value &&
                  (registered && !error ? (
                    <Check className="size-3.5 text-success" title="已成功注册全局热键" />
                  ) : (
                    <AlertCircle className="size-3.5 text-danger-fg" title={error || '热键注册失败'} />
                  ))}
                <Pencil className="size-3 opacity-0 transition group-hover:opacity-100" />
              </>
            )}
          </div>
        </button>

        {value && !recording && (
          <button
            aria-label="清空快捷键"
            className={cn(BTN_ICON, 'size-8 text-faint hover:text-strong')}
            disabled={disabled}
            onClick={() => onChange('')}
            title="禁用 / 清空快捷键"
            type="button"
          >
            <X />
          </button>
        )}

        {isCustomized && defaultValue && !recording && (
          <button
            aria-label="恢复默认快捷键"
            className={cn(BTN_GHOST, 'h-8 px-2 text-muted hover:text-strong')}
            disabled={disabled}
            onClick={() => onChange(defaultValue)}
            title={`恢复为默认值 (${defaultValue})`}
            type="button"
          >
            <RefreshCw className="mr-1 size-3.5" />
            <span>默认</span>
          </button>
        )}
      </div>

      {error && !recording && (
        <div className="flex items-center gap-1 text-[11px] text-danger-fg">
          <AlertCircle className="size-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </div>
  )
}
