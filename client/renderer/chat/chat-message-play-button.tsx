import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'

import { Loader2, SquareFilled, Volume2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { presentationPorts } from '@/shared/presentation-ports'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { $chatMessageBodies } from './chat-store'

/** TTS 播放按钮，跟随生活空间已完成的精灵回复消息展示。点击切换「朗读 / 停止」。
 *
 *  设计要点：
 *  - 同一条消息的按钮第二次点击 = 停止（而不是"暂停+续播"，见 audio-track.ts 设计）。
 *  - 不同消息的按钮之间通过模块级单例抢占，旧的按钮立刻回到 idle。
 *  - TTS 调用统一走 `speakChatMessage`，永远 `persist: true`，命中磁盘缓存。
 *  - 精简紧凑的微型按钮，紧随文本末尾，使用现代矢量图标契合毛玻璃 UI 质感。
 */

interface ChatMessagePlayButtonProps {
  text: string
  messageId: string
  className?: string
}

const $chatPlaybackId = atom<string | null>(null)

export function ChatMessagePlayButton({
  text,
  messageId,
  className = ''
}: ChatMessagePlayButtonProps): React.JSX.Element {
  const dict = useStrings()
  const ports = presentationPorts()
  const voicePreparing = useStore(ports.$voicePreparing)
  const currentPlayingId = useStore($chatPlaybackId)

  const isMinePlaying = currentPlayingId === messageId
  // 其他消息在加载/播放时，当前按钮整体禁用，避免竞速点击。
  const otherBusy = voicePreparing && !isMinePlaying

  const onClick = async (e: React.MouseEvent): Promise<void> => {
    e.stopPropagation()

    // 场景 A：当前按钮就是正在播放/准备中的那一条 → 停止（toggle-stop）。
    // stopSpeaking → audio-track.stopAudio → 同步触发旧 onDone 清 atom；但若
    // TTS 合成仍在 IPC 途中（还没有 onDone 可触发），必须在这里直接清。
    if (currentPlayingId === messageId) {
      ports.stopSpeaking()
      $chatPlaybackId.set(null)

      return
    }

    // 场景 B：抢占。先停掉旧播放（同步触发旧 onDone → 清旧 atom），再占新槽位。
    if (currentPlayingId !== null) {
      ports.stopSpeaking()
    }

    $chatPlaybackId.set(messageId)

    // 点播历史消息不驱动说话状态——徽标只反映伙伴正在回复（流式 / 自动语音）；
    // 口型同步由 audio-track 的音频振幅直驱，与精灵状态无关。
    // myDone 由 audio-track 在 ended / error / stopAudio 三种路径上同步触发。
    // 只在它仍指向当前 messageId 时清空 atom，防止"老 onDone 误杀新播放"。
    const myDone = () => {
      if ($chatPlaybackId.get() === messageId) {
        $chatPlaybackId.set(null)
      }
    }

    try {
      await ports.speakChatMessage(text, undefined, myDone, $chatMessageBodies.get()[messageId]?.speechStyle)
    } catch (err) {
      // audio-track.stopAudio 已经在 synth() 的 catch 里调用；这里清状态并让用户看见失败——
      // 显式点了播放却无声回到 idle，用户无从得知语音服务已故障。
      if ($chatPlaybackId.get() === messageId) {
        $chatPlaybackId.set(null)
        notifyError(err, dict.chat.play.failed)
      }
    }
  }

  let label: string = dict.chat.play.label
  let icon = <Volume2 className="size-3.5 transition-transform group-hover/play:scale-110" />

  let stateClass =
    'text-faint hover:text-strong hover:bg-fill-hover/60 border border-transparent hover:border-line-hairline'

  if (isMinePlaying) {
    if (voicePreparing) {
      label = dict.chat.play.preparing
      icon = <Loader2 className="size-3.5 text-accent animate-spin" />
      stateClass = 'text-accent border border-transparent cursor-pointer'
    } else {
      label = dict.chat.play.stop
      icon = <SquareFilled className="size-2.5 text-accent animate-pulse" />
      stateClass = 'bg-accent-soft/90 text-accent border border-accent-line/50 shadow-xs'
    }
  } else if (otherBusy) {
    label = dict.chat.play.channelBusy
    icon = <Volume2 className="size-3.5 opacity-30" />
    stateClass = 'text-faint/40 border border-transparent cursor-not-allowed opacity-50'
  }

  return (
    <button
      aria-label={label}
      className={cn(
        'group/play inline-flex size-5 shrink-0 items-center justify-center rounded-md backdrop-blur-xs transition select-none',
        stateClass,
        className
      )}
      disabled={Boolean(otherBusy)}
      onClick={onClick}
      title={label}
      type="button"
    >
      {icon}
    </button>
  )
}
