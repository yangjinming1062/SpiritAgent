import { useStore } from '@nanostores/react'
import { IconPlayerPause, IconPlayerPlay } from '@tabler/icons-react'
import type React from 'react'
import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { usePanelActivity } from '@/shared/context/panel-activity'
import { AlertCircle, FileText, Loader2, RefreshCw, Volume2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'
import { clamp } from '@runtime'

import styles from './chat-voice-bar.module.css'
import { useConversationView } from './conversation-view'
import { VoiceBarMenu } from './voice-bar-menu'
import {
  $voiceBarFailedIds,
  $voiceBarLoadingId,
  $voiceBarPausedId,
  $voiceBarPlayingId,
  voiceBarControl
} from './voice-link'
import { getVoicePlaybackStore } from './voice-playback'

// 时长来自后端音频，播放状态经 voice-link 投影；本组件不发起文字合成。

interface ChatVoiceBarProps {
  duration?: number
  messageId: string
  playbackKey?: string
  text: string
}

export function ChatVoiceBar({ duration, messageId, playbackKey, text }: ChatVoiceBarProps): React.JSX.Element {
  const dict = useStrings()
  const t = dict.chat.voice
  const control = voiceBarControl()
  const autoplay = useStore(control.$autoplay)
  const playingId = useStore($voiceBarPlayingId)
  const loadingId = useStore($voiceBarLoadingId)
  const pausedId = useStore($voiceBarPausedId)
  const failedIds = useStore($voiceBarFailedIds, { keys: [messageId] })
  const { runtime, eligible } = useConversationView()
  const panelActive = usePanelActivity()
  const { $voicePlaybackRecords } = getVoicePlaybackStore(runtime.$chatSessionId.get())
  const records = useStore($voicePlaybackRecords, { keys: playbackKey ? [playbackKey] : [] })
  const record = playbackKey ? records[playbackKey] : undefined
  const unavailable = failedIds[messageId]
  const [expanded, setExpanded] = useState(false)
  const [menuPosition, setMenuPosition] = useState<{ x: number; y: number } | null>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuId = useId()
  const trimmed = text.trim()

  const closeMenu = useCallback((restoreFocus = false): void => {
    setMenuPosition(null)

    if (restoreFocus) {
      buttonRef.current?.focus({ preventScroll: true })
    }
  }, [])

  useEffect(() => {
    closeMenu()
  }, [eligible, panelActive, runtime, messageId, closeMenu])

  const isPlaying = playingId === messageId
  const isLoading = loadingId === messageId
  const position = record?.positionSeconds ?? 0
  const listened = record?.listened === true
  const isPaused = !isPlaying && !isLoading && (pausedId === messageId || position > 0)

  const hasRealDuration = typeof duration === 'number' && duration > 0
  const effectiveSec = hasRealDuration ? duration : 1
  const sec = clamp(effectiveSec, 1, 60)
  const widthPx = 88 + Math.round(((sec - 1) / 59) * (220 - 88))

  const state = isLoading
    ? 'loading'
    : isPlaying
      ? 'playing'
      : unavailable
        ? 'failed'
        : isPaused
          ? 'paused'
          : listened
            ? 'listened'
            : 'unheard'

  const action = isPlaying || isLoading ? t.pause : unavailable ? t.retry : isPaused ? t.resume : t.play
  const label = [t[state], hasRealDuration ? `${Math.ceil(duration)}″` : '', action].filter(Boolean).join(', ')
  const progress = hasRealDuration ? Math.min(100, (position / duration) * 100) : 0

  const handleClick = (e: React.MouseEvent): void => {
    e.stopPropagation()
    closeMenu()
    control.toggle(messageId)
  }

  return (
    <div className="flex min-w-0 max-w-full flex-col items-start">
      <span
        className="relative inline-flex max-w-full pr-3 align-top"
        onContextMenu={event => {
          event.preventDefault()
          event.stopPropagation()

          if (eligible && panelActive) {
            setMenuPosition({ x: event.clientX, y: event.clientY })
          }
        }}
      >
        <button
          aria-controls={menuPosition ? menuId : undefined}
          aria-expanded={menuPosition !== null}
          aria-haspopup="menu"
          aria-label={label}
          aria-pressed={isPlaying}
          className={cn(
            'group/voicebar relative inline-flex h-9 max-w-full items-center justify-between overflow-hidden rounded-lg px-3.5 text-xs backdrop-blur-md transition select-none cursor-pointer',
            'border border-line-standard bg-surface-card text-strong shadow-xs hover:border-line-strong hover:bg-surface-card/90',
            isPlaying && 'bg-accent-soft/40 border-accent-line/50',
            unavailable && !isLoading && 'text-danger-fg'
          )}
          data-voice-state={state}
          onClick={handleClick}
          onKeyDown={event => {
            if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
              event.preventDefault()
              event.stopPropagation()

              if (eligible && panelActive) {
                const bounds = event.currentTarget.getBoundingClientRect()
                setMenuPosition({ x: bounds.left, y: bounds.bottom })
              }
            }
          }}
          ref={buttonRef}
          style={{ width: `${widthPx}px` }}
          title={label}
          type="button"
        >
          <div className="flex items-center gap-1.5">
            {isLoading ? (
              <Loader2 className="size-3.5 shrink-0 text-accent animate-spin" />
            ) : isPlaying ? (
              <Volume2 className={cn('size-4 shrink-0 text-accent', styles.playing)} />
            ) : unavailable ? (
              <AlertCircle className="size-4 shrink-0 text-danger-fg" />
            ) : (
              <Volume2 className="size-3.5 shrink-0 text-muted group-hover/voicebar:text-strong transition-colors" />
            )}
          </div>
          <span
            className={cn(
              'ml-2 text-[11px] font-medium tabular-nums',
              isPlaying ? 'text-accent font-semibold' : 'text-muted'
            )}
          >
            {unavailable && !isLoading ? t.retryShort : hasRealDuration ? `${Math.ceil(duration)}″` : '…'}
          </span>
          {(position > 0 || isPlaying) && hasRealDuration ? (
            <span
              aria-label={t.progress}
              aria-valuemax={100}
              aria-valuemin={0}
              aria-valuenow={Math.round(progress)}
              className="absolute inset-x-3 bottom-1 h-0.5 overflow-hidden rounded-full bg-fill-hover"
              role="progressbar"
            >
              <span className="block h-full rounded-full bg-accent" style={{ width: `${progress}%` }} />
            </span>
          ) : null}
        </button>
        {!listened ? (
          <span aria-hidden="true" className="absolute right-0 top-1 size-1.5 rounded-full bg-red-500" />
        ) : null}
      </span>
      {expanded && trimmed ? (
        <div className="mt-1 max-h-60 max-w-full overflow-y-auto rounded-lg border border-line-standard bg-surface-card p-2.5 text-xs leading-relaxed text-strong shadow-inner backdrop-blur-md select-text cursor-text whitespace-pre-wrap break-words font-sans">
          {trimmed}
        </div>
      ) : null}
      {menuPosition && eligible && panelActive ? (
        <VoiceBarMenu
          id={menuId}
          items={[
            {
              disabled: !trimmed,
              icon: FileText,
              label: expanded ? t.hideTranscript : t.showTranscript,
              onSelect: () => setExpanded(value => !value)
            },
            { icon: RefreshCw, label: t.restart, onSelect: () => control.restart(messageId) },
            {
              icon: isPlaying || isLoading ? IconPlayerPause : unavailable ? RefreshCw : IconPlayerPlay,
              label: isPlaying || isLoading ? t.pause : unavailable ? t.retryPlayback : isPaused ? t.resume : t.play,
              onSelect: () => control.toggle(messageId)
            },
            {
              checked: autoplay,
              icon: Volume2,
              label: t.autoplay,
              onSelect: () => control.setAutoplay(!autoplay)
            }
          ]}
          label={t.controls}
          onClose={closeMenu}
          position={menuPosition}
        />
      ) : null}
    </div>
  )
}
