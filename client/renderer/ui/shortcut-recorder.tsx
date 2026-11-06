import { Fragment, useEffect, useRef, useState } from 'react'

import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { AlertCircle, Check, Pencil, RefreshCw, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

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

// 每行为 [显示标签, ...按键名别名（小写）]。
const KEY_LABELS = new Map(
  [
    [IS_MAC ? '⌘' : 'Ctrl', 'commandorcontrol', 'cmdorctrl'],
    [IS_MAC ? '⌃' : 'Ctrl', 'ctrl', 'control'],
    [IS_MAC ? '⌥' : 'Alt', 'alt', 'option'],
    [IS_MAC ? '⇧' : 'Shift', 'shift'],
    [IS_MAC ? '⌘' : 'Win', 'super', 'meta', 'command', 'cmd'],
    ['↑', 'up'],
    ['↓', 'down'],
    ['←', 'left'],
    ['→', 'right'],
    ['Enter', 'return', 'enter'],
    ['Space', 'space'],
    ['Esc', 'escape', 'esc']
  ].flatMap(([label, ...names]) => names.map(name => [name, label] as const))
)

function formatKeyLabel(token: string): string {
  const t = token.trim()

  return KEY_LABELS.get(t.toLowerCase()) ?? (t.length === 1 ? t.toUpperCase() : t)
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

// 字母、数字、F 键与小键盘数字由正则处理，其余 KeyboardEvent.code 经此表转为 accelerator 主键。
const CODE_BASE_KEYS = new Map([
  ['Space', 'Space'],
  ['Enter', 'Return'],
  ['NumpadEnter', 'Return'],
  ['Tab', 'Tab'],
  ['ArrowUp', 'Up'],
  ['ArrowDown', 'Down'],
  ['ArrowLeft', 'Left'],
  ['ArrowRight', 'Right'],
  ['Home', 'Home'],
  ['End', 'End'],
  ['PageUp', 'PageUp'],
  ['PageDown', 'PageDown'],
  ['Insert', 'Insert'],
  ['Delete', 'Delete'],
  ['Backquote', '`'],
  ['Minus', '-'],
  ['NumpadSubtract', '-'],
  ['Equal', '='],
  ['BracketLeft', '['],
  ['BracketRight', ']'],
  ['Backslash', '\\'],
  ['Semicolon', ';'],
  ['Quote', "'"],
  ['Comma', ','],
  ['Period', '.'],
  ['NumpadDecimal', '.'],
  ['Slash', '/'],
  ['NumpadDivide', '/'],
  ['NumpadAdd', 'plus'],
  ['NumpadMultiply', '*']
])

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

  const mapped = CODE_BASE_KEYS.get(code)

  if (mapped !== undefined) {
    return mapped
  }

  if (key && key.length === 1 && !/\s/.test(key)) {
    return key.toUpperCase()
  }

  return null
}

export function ShortcutRecorder({
  value,
  onChange,
  defaultValue,
  registered = true,
  error,
  disabled = false
}: ShortcutRecorderProps): React.JSX.Element {
  const t = useStrings().settings.shortcuts
  const [recording, setRecording] = useState(false)
  const [heldModifiers, setHeldModifiers] = useState<string[]>([])
  const containerRef = useRef<HTMLDivElement>(null)
  const onChangeRef = useLatestRef(onChange)

  useEffect(() => {
    if (!recording) {
      return
    }

    const stop = (): void => {
      setRecording(false)
      setHeldModifiers([])
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      e.preventDefault()
      e.stopPropagation()

      if (e.key === 'Escape') {
        stop()

        return
      }

      if ((e.key === 'Backspace' || e.key === 'Delete') && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey) {
        onChangeRef.current('')
        stop()

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
        onChangeRef.current(parts.join('+'))
        stop()
      }
    }

    const handleKeyUp = (e: KeyboardEvent) => {
      setHeldModifiers(modifiersFromEvent(e))
    }

    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        stop()
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
  }, [recording, onChangeRef])

  const tokens = parseAcceleratorTokens(value)
  const isCustomized = defaultValue !== undefined && value !== defaultValue

  return (
    <div className="flex flex-col gap-1.5" ref={containerRef}>
      <div className="flex items-center gap-2">
        <button
          aria-label={recording ? t.recordingAria : t.editAria}
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
                  {t.pressKeysPrompt}
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
              <span className="text-[11px] text-faint">{t.empty}</span>
            )}
          </div>

          <div className="flex items-center gap-1.5 pl-2 text-faint group-hover:text-muted">
            {recording ? (
              <span className="text-[10px] text-faint">{t.cancelHint}</span>
            ) : (
              <>
                {value &&
                  (registered && !error ? (
                    <Check className="size-3.5 text-success" title={t.registeredOk} />
                  ) : (
                    <AlertCircle className="size-3.5 text-danger-fg" title={error || t.registerFailed} />
                  ))}
                <Pencil className="size-3 opacity-0 transition group-hover:opacity-100" />
              </>
            )}
          </div>
        </button>

        {value && !recording && (
          <button
            aria-label={t.clearAria}
            className={cn(BTN_ICON, 'size-8 text-faint hover:text-strong')}
            disabled={disabled}
            onClick={() => onChange('')}
            title={t.clearTitle}
            type="button"
          >
            <X />
          </button>
        )}

        {isCustomized && defaultValue && !recording && (
          <button
            aria-label={t.resetAria}
            className={cn(BTN_GHOST, 'h-8 px-2 text-muted hover:text-strong')}
            disabled={disabled}
            onClick={() => onChange(defaultValue)}
            title={t.resetTitle(defaultValue)}
            type="button"
          >
            <RefreshCw className="mr-1 size-3.5" />
            <span>{t.defaultLabel}</span>
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
