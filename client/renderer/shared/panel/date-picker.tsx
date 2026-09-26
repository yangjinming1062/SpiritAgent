import type React from 'react'
import { useEffect, useRef } from 'react'

import { cn } from '@/shared/lib/utils'

import { INPUT_CLASS } from './palette'

// 自绘日期选择：YYYY-MM-DD 每位数字一个滚轮。value 空串表示未选择（显示短横），
// 首次拨动以今天为基准写入。
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

// 拨动某一位（0 = 个位，1 = 十位…）：字段 ±10^place。
// 年与日个位在上下界环回；日十位钉在当月天数，避免二月拨出怪值；
// 月十位只有 0/1，保留个位并夹到 1–12。年月变动后日再夹回当月合法日。
function stepField(fields: Fields, axis: 'd' | 'm' | 'y', place: number, delta: number): Fields {
  const next: Fields = { ...fields }
  const amount = delta * 10 ** place

  if (axis === 'y') {
    next.y = wrapInto(next.y + amount, MIN_YEAR, MAX_YEAR)
  } else if (axis === 'm') {
    if (place === 0) {
      next.m = wrapInto(next.m + delta, 1, 12)
    } else {
      const tens = next.m >= 10 ? 1 : 0
      next.m = clamp(wrapInto(tens + delta, 0, 1) * 10 + (next.m % 10), 1, 12)
    }
  } else {
    const max = daysInMonth(next.y, next.m)
    next.d = place === 0 ? wrapInto(next.d + amount, 1, max) : clamp(next.d + amount, 1, max)
  }

  next.d = clamp(next.d, 1, daysInMonth(next.y, next.m))

  return next
}

// 轨迹板 deltaY 细碎，按阈值累计成整格再拨。
const WHEEL_NOTCH = 40

function DigitWheel({
  digit,
  disabled,
  label,
  onStep
}: {
  digit: number | null
  disabled: boolean
  label: string
  onStep: (delta: number) => void
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

  const prev = digit === null ? null : wrapInto(digit - 1, 0, 9)
  const next = digit === null ? null : wrapInto(digit + 1, 0, 9)

  return (
    <div
      aria-label={label}
      aria-valuemax={9}
      aria-valuemin={0}
      aria-valuenow={digit ?? undefined}
      aria-valuetext={digit === null ? '未选择' : String(digit)}
      className={cn(
        'flex h-[52px] w-7 cursor-ns-resize select-none flex-col items-center justify-center rounded-md transition',
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
          'h-3 w-full text-[10px] leading-3 text-faint',
          prev === null ? '' : 'cursor-pointer hover:text-muted'
        )}
        onClick={() => onStep(-1)}
      >
        {prev ?? ' '}
      </div>
      <span className={cn('text-sm font-semibold leading-6', digit === null ? 'text-faint' : 'text-strong')}>
        {digit ?? '–'}
      </span>
      <div
        aria-hidden
        className={cn(
          'h-3 w-full text-[10px] leading-3 text-faint',
          next === null ? '' : 'cursor-pointer hover:text-muted'
        )}
        onClick={() => onStep(1)}
      >
        {next ?? ' '}
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

  const step = (axis: 'd' | 'm' | 'y', place: number, delta: number): void => {
    if (disabled) {
      return
    }

    const next = stepField(fields, axis, place, delta)
    onChange(toISODate(next.y, next.m, next.d))
  }

  const digitAt = (axis: 'd' | 'm' | 'y', place: number): number | null =>
    parsed ? Math.floor(parsed[axis] / 10 ** place) % 10 : null

  return (
    <div className={className} id={id}>
      <div
        aria-label={placeholder}
        className={cn(INPUT_CLASS, 'flex items-center justify-center gap-0.5 px-2 py-2', disabled && 'opacity-40')}
      >
        <DigitWheel digit={digitAt('y', 3)} disabled={disabled} label="年千位" onStep={d => step('y', 3, d)} />
        <DigitWheel digit={digitAt('y', 2)} disabled={disabled} label="年百位" onStep={d => step('y', 2, d)} />
        <DigitWheel digit={digitAt('y', 1)} disabled={disabled} label="年十位" onStep={d => step('y', 1, d)} />
        <DigitWheel digit={digitAt('y', 0)} disabled={disabled} label="年个位" onStep={d => step('y', 0, d)} />
        <Sep />
        <DigitWheel digit={digitAt('m', 1)} disabled={disabled} label="月十位" onStep={d => step('m', 1, d)} />
        <DigitWheel digit={digitAt('m', 0)} disabled={disabled} label="月个位" onStep={d => step('m', 0, d)} />
        <Sep />
        <DigitWheel digit={digitAt('d', 1)} disabled={disabled} label="日十位" onStep={d => step('d', 1, d)} />
        <DigitWheel digit={digitAt('d', 0)} disabled={disabled} label="日个位" onStep={d => step('d', 0, d)} />
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
