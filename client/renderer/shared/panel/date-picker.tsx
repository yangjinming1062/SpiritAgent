import type React from 'react'
import { useEffect, useRef } from 'react'

import { cn } from '@/shared/lib/utils'

import { INPUT_CLASS } from './palette'

// 自绘日期选择：YYYY / MM / DD 三组整段滚轮（1993 的邻位是 1992/1994）。
// value 空串表示未选择（显示短横），首次拨动以今天为基准写入。
// 年份钳在四位：0–99 会被 JS Date 重映射到 1900+，也写不出 YYYY-MM-DD。

const pad2 = (n: number): string => String(n).padStart(2, '0')

const MIN_YEAR = 1000
const MAX_YEAR = 9999

type Fields = { d: number; m: number; y: number }

const toISODate = (y: number, m: number, d: number): string => `${y}-${pad2(m)}-${pad2(d)}`

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n))
}

function wrapInto(n: number, min: number, max: number): number {
  const span = max - min + 1

  return min + ((((n - min) % span) + span) % span)
}

function daysInMonth(y: number, m: number): number {
  return new Date(y, m, 0).getDate()
}

// 只接受真实存在的 YYYY-MM-DD；解析失败按未选择处理。
function parseISODate(value: string): Fields | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)

  if (!match) {
    return null
  }

  const y = Number(match[1])
  const m = Number(match[2])
  const d = Number(match[3])
  const date = new Date(y, m - 1, d)

  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? { d, m, y } : null
}

function todayFields(): Fields {
  const now = new Date()

  return { d: now.getDate(), m: now.getMonth() + 1, y: now.getFullYear() }
}

// 年/月/日各自环回；年月变动后日夹回当月合法日（如 1-31 → 2 月会变 2-28）。
function stepField(fields: Fields, axis: 'd' | 'm' | 'y', delta: number): Fields {
  const next: Fields = { ...fields }

  if (axis === 'y') {
    next.y = wrapInto(next.y + delta, MIN_YEAR, MAX_YEAR)
  } else if (axis === 'm') {
    next.m = wrapInto(next.m + delta, 1, 12)
  } else {
    next.d = wrapInto(next.d + delta, 1, daysInMonth(next.y, next.m))
  }

  next.d = clamp(next.d, 1, daysInMonth(next.y, next.m))

  return next
}

// 轨迹板 deltaY 细碎，按阈值累计成整格再拨。
const WHEEL_NOTCH = 40

function FieldWheel({
  disabled,
  format,
  label,
  max,
  min,
  minW = 'min-w-[2ch]',
  onStep,
  value
}: {
  disabled: boolean
  format: (n: number) => string
  label: string
  max: number
  min: number
  minW?: string
  onStep: (delta: number) => void
  value: number | null
}): React.JSX.Element {
  const ref = useRef<HTMLDivElement>(null)
  const accRef = useRef(0)

  useEffect(() => {
    const el = ref.current

    if (!el || disabled) {
      return
    }

    // passive: false 才能拦住外层滚动，滚轮只用来拨数字。
    const onWheel = (e: WheelEvent): void => {
      e.preventDefault()
      accRef.current += e.deltaY

      while (accRef.current >= WHEEL_NOTCH) {
        onStep(1)
        accRef.current -= WHEEL_NOTCH
      }

      while (accRef.current <= -WHEEL_NOTCH) {
        onStep(-1)
        accRef.current += WHEEL_NOTCH
      }
    }

    el.addEventListener('wheel', onWheel, { passive: false })

    return () => el.removeEventListener('wheel', onWheel)
  }, [disabled, onStep])

  const prev = value === null ? null : wrapInto(value - 1, min, max)
  const next = value === null ? null : wrapInto(value + 1, min, max)

  return (
    <div
      aria-label={label}
      aria-valuemax={max}
      aria-valuemin={min}
      aria-valuenow={value ?? undefined}
      aria-valuetext={value === null ? '未选择' : format(value)}
      className={cn(
        'flex h-[52px] cursor-ns-resize select-none flex-col items-center justify-center rounded-md px-1 transition',
        'hover:bg-fill-hover focus-visible:outline focus-visible:outline-1 focus-visible:outline-focus-line',
        disabled && 'pointer-events-none opacity-40'
      )}
      onKeyDown={e => {
        if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
          e.preventDefault()
          onStep(-1)
        }

        if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
          e.preventDefault()
          onStep(1)
        }
      }}
      ref={ref}
      role="spinbutton"
      tabIndex={disabled ? -1 : 0}
    >
      <div
        aria-hidden
        className={cn(
          'h-3 w-full text-center text-[10px] leading-3 text-faint',
          prev === null ? '' : 'cursor-pointer hover:text-muted'
        )}
        onClick={() => onStep(-1)}
      >
        {prev === null ? ' ' : format(prev)}
      </div>
      <span
        className={cn(
          minW,
          'text-center text-sm font-semibold leading-6 tabular-nums',
          value === null ? 'text-faint' : 'text-strong'
        )}
      >
        {value === null ? '–' : format(value)}
      </span>
      <div
        aria-hidden
        className={cn(
          'h-3 w-full text-center text-[10px] leading-3 text-faint',
          next === null ? '' : 'cursor-pointer hover:text-muted'
        )}
        onClick={() => onStep(1)}
      >
        {next === null ? ' ' : format(next)}
      </div>
    </div>
  )
}

function Sep(): React.JSX.Element {
  return (
    <span aria-hidden className="px-0.5 pb-1 text-xs text-faint">
      -
    </span>
  )
}

interface DatePickerProps {
  className?: string
  clearLabel: string
  disabled?: boolean
  id?: string
  onChange: (value: string) => void
  placeholder: string
  value: string
}

export function DatePicker({
  className,
  clearLabel,
  disabled = false,
  id,
  onChange,
  placeholder,
  value
}: DatePickerProps): React.JSX.Element {
  const parsed = parseISODate(value)
  const fields = parsed ?? todayFields()

  const step = (axis: 'd' | 'm' | 'y', delta: number): void => {
    if (disabled) {
      return
    }

    const next = stepField(fields, axis, delta)
    onChange(toISODate(next.y, next.m, next.d))
  }

  const fieldAt = (axis: 'd' | 'm' | 'y'): number | null => parsed?.[axis] ?? null

  return (
    <div className={className} id={id}>
      <div
        aria-label={placeholder}
        className={cn(INPUT_CLASS, 'flex items-center justify-center gap-1 px-2 py-2', disabled && 'opacity-40')}
      >
        <FieldWheel
          disabled={disabled}
          format={n => String(n)}
          label="年"
          max={MAX_YEAR}
          min={MIN_YEAR}
          minW="min-w-[4ch]"
          onStep={d => step('y', d)}
          value={fieldAt('y')}
        />
        <Sep />
        <FieldWheel
          disabled={disabled}
          format={pad2}
          label="月"
          max={12}
          min={1}
          onStep={d => step('m', d)}
          value={fieldAt('m')}
        />
        <Sep />
        <FieldWheel
          disabled={disabled}
          format={pad2}
          label="日"
          max={daysInMonth(fields.y, fields.m)}
          min={1}
          onStep={d => step('d', d)}
          value={fieldAt('d')}
        />
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        {!value && placeholder ? <span className="text-[10px] text-faint">{placeholder}</span> : null}
        {value ? (
          <button
            className="text-[10px] text-muted transition hover:text-strong disabled:pointer-events-none disabled:opacity-40"
            disabled={disabled}
            onClick={() => onChange('')}
            type="button"
          >
            {clearLabel}
          </button>
        ) : null}
      </div>
    </div>
  )
}
