import { IconMaximize, IconPlayerPause, IconPlayerPlay, IconVolume, IconVolumeOff } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'

import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

function clock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '0:00'
  }

  const total = Math.floor(seconds)

  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/**
 * 带自有控制条的播放面。原生控件在页面没有全屏权限时仍会显示不可用的全屏按钮，
 * 预览类视频统一改用这里，全屏由调用方决定（桌面表面走窗口内查看器）。
 */
export function VideoSurface({
  autoPlay = false,
  className,
  frameClassName,
  muted: mutedProp = false,
  onEnded,
  onError,
  onFullscreen,
  onPause,
  onPlay,
  onSeeked,
  poster,
  showMute = true,
  src
}: {
  autoPlay?: boolean
  className?: string
  frameClassName?: string
  muted?: boolean
  onEnded?: () => void
  onError?: () => void
  onFullscreen?: () => void
  onPause?: () => void
  onPlay?: (video: HTMLVideoElement) => void
  onSeeked?: (video: HTMLVideoElement) => void
  poster?: string
  showMute?: boolean
  // 地址就绪前不渲染画面，与原生 video 的 src 语义一致。
  src?: string
}): React.JSX.Element {
  const t = useStrings().ui.player
  const videoRef = useRef<HTMLVideoElement>(null)
  const [muted, setMuted] = useState(mutedProp)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState(0)
  const [duration, setDuration] = useState(0)

  // 独立音轨就绪时由调用方固定静音，用户手动开关只在无人接管声音时保留。
  useEffect(() => setMuted(mutedProp), [mutedProp])

  const toggle = (): void => {
    const video = videoRef.current

    if (!video) {
      return
    }

    if (video.paused) {
      void video.play().catch(() => undefined)
    } else {
      video.pause()
    }
  }

  const openFullscreen = (): void => {
    videoRef.current?.pause()
    onFullscreen?.()
  }

  const syncDuration = (video: HTMLVideoElement): void =>
    setDuration(Number.isFinite(video.duration) ? video.duration : 0)

  return (
    <span className={cn('relative inline-block max-w-full', frameClassName)}>
      <video
        autoPlay={autoPlay}
        className={cn('cursor-pointer', className)}
        muted={muted}
        onClick={toggle}
        onDoubleClick={onFullscreen ? openFullscreen : undefined}
        onDurationChange={event => syncDuration(event.currentTarget)}
        onEnded={onEnded}
        onError={onError}
        onLoadedMetadata={event => syncDuration(event.currentTarget)}
        onPause={() => {
          setPlaying(false)
          onPause?.()
        }}
        onPlay={event => {
          setPlaying(true)
          onPlay?.(event.currentTarget)
        }}
        onSeeked={event => onSeeked?.(event.currentTarget)}
        onTimeUpdate={event => setCurrent(event.currentTarget.currentTime)}
        playsInline
        poster={poster}
        preload="metadata"
        ref={videoRef}
        src={src}
      />
      <span className="absolute inset-x-0 bottom-0 flex items-center gap-1.5 bg-black/60 px-2 py-1 text-[11px] text-white/90">
        <button
          aria-label={playing ? t.pause : t.play}
          className="flex size-5 shrink-0 items-center justify-center rounded hover:text-white"
          onClick={toggle}
          title={playing ? t.pause : t.play}
          type="button"
        >
          {playing ? <IconPlayerPause size={14} /> : <IconPlayerPlay size={14} />}
        </button>
        <span className="shrink-0 tabular-nums">
          {clock(current)} / {clock(duration)}
        </span>
        <input
          aria-label={t.progress}
          className="h-1 min-w-0 flex-1 cursor-pointer accent-white"
          max={duration || 0}
          min={0}
          onChange={event => {
            if (videoRef.current) {
              videoRef.current.currentTime = Number(event.currentTarget.value)
            }
          }}
          step={0.01}
          type="range"
          value={Math.min(current, duration || 0)}
        />
        {showMute ? (
          <button
            aria-label={muted ? t.unmute : t.mute}
            className="flex size-5 shrink-0 items-center justify-center rounded hover:text-white"
            onClick={() => setMuted(value => !value)}
            title={muted ? t.unmute : t.mute}
            type="button"
          >
            {muted ? <IconVolumeOff size={14} /> : <IconVolume size={14} />}
          </button>
        ) : null}
        {onFullscreen ? (
          <button
            aria-label={t.fullscreen}
            className="flex size-5 shrink-0 items-center justify-center rounded hover:text-white"
            onClick={openFullscreen}
            title={t.fullscreen}
            type="button"
          >
            <IconMaximize size={14} />
          </button>
        ) : null}
      </span>
    </span>
  )
}
