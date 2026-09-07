import { sleep } from '@runtime'

import { hydrateMesh2D, hydratePuppet, resetMesh2D, resetPuppet, setMesh2DStatus, switchRenderMode } from '@/2d'
import {
  $clipMap,
  $modelGenError,
  $modelGenProgress,
  $modelGenState,
  clearModelRetry,
  hydrateExpressions,
  setModelFailed,
  setModelInfo
} from '@/3d'
import {
  $chatDraftFromUndo,
  $chatSessionId,
  $chatTurnInFlight,
  $turnHadBubbleBreak,
  appendAssistantDelta,
  appendAssistantReasoningDelta,
  beginAssistantMessage,
  bindTrailingAssistantMessageId,
  bindTrailingUserMessageIds,
  cancelVoiceBar,
  clearPendingPrompts,
  finalizeAssistantMessage,
  hydrateChatMessages,
  isLivingVoiceBarActive,
  markAssistantTerminal,
  pushAffectTraceMessage,
  pushMediaMessage,
  pushProactiveMessage,
  pushStatusPill,
  setAssistantTool,
  setSessionContextUsage,
  setTurnHadBubbleBreak,
  setTurnPendingEmotion,
  showMediaHint,
  submitPendingBatch,
  switchSession
} from '@/chat'
import { $screenLocked, reportInteractionStat } from '@/companion/activity'
import { resolveAvatarRegeneration } from '@/companion/avatar-regen-store'
import {
  $effectiveTier,
  $spriteState,
  lockGazeToPoint,
  playSpriteActionSequence,
  setSpriteState,
  type SpriteEmotion
} from '@/companion/companion-store'
import { $defaultScale, computePerchPlacement, setLocale, startRoam } from '@/companion/spatial'
import { emitVfx } from '@/companion/vfx'
import { hydrateWardrobe } from '@/companion/wardrobe/wardrobe-store'
import { onBackdropEvent, onJournalEvent } from '@/living'
import { type GatewayEvent, type SlashCommandResultPayload } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { $gateway } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { $surfaceOpen, requestOpenSurface } from '@/shared/store/surfaces'
import type { ChatMediaItem, SessionMessage } from '@/shared/types/spiritagent'

import { $devMode, pushDevLog } from './developer-overlay'
import { $companionMood } from './persona-store'
import { speakProactive } from './proactive/proactive'
import { findWindowByKeyword, gazeTowardsPoint, performRitualWalk, type WindowGeom } from './ritual-walk'
import { triggerFootGlowPulse } from './sprite/foot-glow'

const PERCH_RETRY_MS = 300
const PERCH_RETRY_COUNT = 5
// click_at 虚拟目标几何的边长（px）：只为 perch 落位与指向方位提供参照，
// 精灵会站到点击点旁而非覆盖它。
const CLICK_GEOM_SIZE = 160
const CLICK_GEOM_HALF = CLICK_GEOM_SIZE / 2

// 高唤醒负面情绪冒冷汗（DESIGN §6.3 粒子清单 💦 的情绪侧触发点）
const SWEAT_EMOTIONS: ReadonlySet<string> = new Set(['scared', 'embarrassed', 'concerned', 'apologetic'])

// 遥控回合可并发多个工具，一个先返回不能把仍在跑的复位掉。
let remoteToolDepth = 0

// 已受理过的设备指令 call_id。tool.call 进重放缓冲，WS 断开重连时会被重发——没有这道去重，
// 一条「删文件」会在本机执行第二次（后端的 resolve_future 只是丢弃迟到结果，拦不住已发生的副作用）。
const seenToolCalls = new Set<string>()
const SEEN_TOOL_CALL_CAP = 500

function markToolCallSeen(callId: string): boolean {
  if (seenToolCalls.has(callId)) {
    return false
  }

  // 上限对齐服务端重放缓冲容量；超出后按插入序淘汰最旧的，重放窗口内的 id 不会被提前丢掉。
  if (seenToolCalls.size >= SEEN_TOOL_CALL_CAP) {
    seenToolCalls.delete(seenToolCalls.values().next().value as string)
  }

  seenToolCalls.add(callId)

  return true
}

function releaseRemoteTool(): void {
  remoteToolDepth = Math.max(0, remoteToolDepth - 1)

  // force：IDLE（10）< WORKING（70），没有 force 会被优先级门控静默拒绝，精灵永久卡在工作姿态。
  // 仅在状态仍是工作态（或仪式行走留下的 interacting 瞬态）且没有桌面回合接管时复位。
  if (remoteToolDepth === 0) {
    const current = $spriteState.get()

    if (current === 'working' || current === 'interacting') {
      setSpriteState('idle', { force: true })
    }
  }
}

function maybeEmotionVfx(emotion?: string): void {
  if (emotion && SWEAT_EMOTIONS.has(emotion)) {
    emitVfx('sweat', { nx: 0.5, ny: 0.2, count: 2 })
  }
}

async function findWindowWithRetry(keyword: string): Promise<WindowGeom | null> {
  for (let attempt = 0; attempt <= PERCH_RETRY_COUNT; attempt++) {
    const geom = await findWindowByKeyword(keyword)

    if (geom) {
      return geom
    }

    if (attempt < PERCH_RETRY_COUNT) {
      await sleep(PERCH_RETRY_MS)
    }
  }

  return null
}

function applySpatialCue(locale?: string, target?: string): void {
  // 空间 cue 是云端语义的移动指令，只有自主档兑现（DESIGN §3.5）——常规不移动，静止不做任何主动表达。
  if (!locale || $screenLocked.get() || $effectiveTier.get() !== 'autonomous') {
    return
  }

  // 生活空间打开时精灵收起，不要强行触发移动。
  if ($surfaceOpen.get() === 'living' && (locale === 'home' || locale === 'roam')) {
    return
  }

  void (async () => {
    if (locale === 'perch' && target) {
      const geom = await findWindowWithRetry(target)

      if (!geom) {
        return
      }

      const perch = computePerchPlacement(geom, $defaultScale.get())

      if (!perch) {
        return
      }

      // 与仪式行走同规则：飞行与栖息途中视线锁定目标窗口，数秒后交还指针跟随
      lockGazeToPoint(gazeTowardsPoint({ x: geom.x + geom.w / 2, y: geom.y + geom.h / 2 }))
      setLocale('perch', { position: perch.pos, scaleLimit: perch.scale, locomotion: 'fly' })
    } else if (locale === 'home' && $surfaceOpen.get() !== 'living') {
      setLocale('home', { locomotion: 'fly' })
    } else if (locale === 'roam') {
      startRoam()
    }
  })().catch(err => {
    log.error('events', 'applySpatialCue error:', err)
  })
}

export function handleCompanionEvent(event: GatewayEvent): void {
  // 仅在冷启动 hydrateAuth 尚未完成时（'pending'）丢弃 WSEvent：无用户态，事件无主。
  // 'unauthenticated' 不丢弃：登出 race 里到达的 message.complete / model.ready /
  // companion.2d.ready 还要落地——否则流式 chat 卡 thinking、模型 ready 漏掉让用户
  // 看到旧 model。跨会话污染由事件本身的 session_id 闸门（下方 session_id 过滤段）兜底。
  // 写持久化原子的副作用分支（model.ready / companion.2d.ready）在自己内部用 $auth.kind
  // 二次防御，避免 OPFS / localStorage 串味。
  const authed = (): boolean => $auth.get().kind === 'authenticated'

  if ($auth.get().kind === 'pending') {
    log.warn('events', 'Discarded event during pending auth:', event.type)

    return
  }

  if ($devMode.get()) {
    pushDevLog(event.type, JSON.stringify(event.payload ?? {}))
  }

  // 聊天回合事件（message.start/delta/complete/persisted、tool.*、error）携带发出该事件的会话 session_id。
  // 来自渲染层当前未查看会话的事件不应作用于可见聊天——
  // 例如 cron 的自动回合通过 cron 会话流式输出文本；没有这道门的话，
  // 用户会看到 cron 的回复，好像它回答了主会话上一条消息。
  // WSEvent 驱动的事件（companion.message/affect、model.*、
  // avatar.regenerated）没有 session_id，直接放行。
  if (event.session_id !== undefined) {
    const current = $chatSessionId.get()

    if (current === null || event.session_id !== current) {
      return
    }
  }

  const gw = $gateway.get()
  const isProxy = Boolean(gw && 'isProxy' in gw && gw.isProxy)
  const shouldPlayAudio = isProxy || $surfaceOpen.get() === null

  switch (event.type) {
    case 'message.start':
      beginAssistantMessage()
      setTurnHadBubbleBreak(false)
      setSpriteState('thinking')

      break
    case 'message.delta': {
      const text = (event.payload as { text?: string } | undefined)?.text ?? ''

      if (text) {
        appendAssistantDelta(text)
      }

      break
    }

    case 'message.reasoning.delta': {
      const text = (event.payload as { text?: string } | undefined)?.text ?? ''

      if (text) {
        appendAssistantReasoningDelta(text)
      }

      break
    }

    case 'message.break': {
      // 后端把回合切成了连续的气泡——收尾当前气泡；
      // 下一条 message.delta 会开一个新气泡（后端已在它们之间插入 0.5–1.5 秒停顿）。
      setTurnHadBubbleBreak(true)
      finalizeAssistantMessage()

      break
    }

    case 'message.persisted': {
      const p = event.payload as { role?: string; message_ids?: unknown } | undefined

      if (p?.role === 'user' && Array.isArray(p.message_ids)) {
        bindTrailingUserMessageIds(p.message_ids.filter((id): id is number => typeof id === 'number'))
      }

      break
    }

    case 'message.complete': {
      const payload = event.payload as
        | {
            affect?: { actions?: string[]; emotion?: string; locale?: string; target?: string }
            media?: ChatMediaItem[]
            message_id?: number
            reasoning?: string
            text?: string
            usage?: { completion_tokens?: number; prompt_tokens?: number; total_tokens?: number }
          }
        | undefined

      const text = payload?.text ?? ''
      const emotion = payload?.affect?.emotion
      const actions = payload?.affect?.actions ?? []
      const locale = payload?.affect?.locale
      const target = payload?.affect?.target

      if (payload?.usage) {
        setSessionContextUsage({
          completionTokens: payload.usage.completion_tokens,
          promptTokens: payload.usage.prompt_tokens,
          totalTokens:
            payload.usage.total_tokens ??
            (payload.usage.prompt_tokens && payload.usage.completion_tokens
              ? payload.usage.prompt_tokens + payload.usage.completion_tokens
              : undefined)
        })
      }

      // 锁屏状态下，抑制渲染层的提示。
      const screenLocked = $screenLocked.get()

      // "neutral" 是 LLM 的无操作情绪；当作无 affect 处理，避免触发徽标闪烁。
      // 情绪通道不受锁屏拦截（DESIGN §6.2）；锁屏只静默语音与消息。
      const hasEmotion = Boolean(emotion && emotion !== 'neutral')

      // 多气泡回合：每个气泡各自携带流式文本；
      // payload.text 是整轮（包含两个气泡）的全文，会覆盖最后一个气泡。
      // 这种情况下保留 last.text。媒体与正文正交，始终挂到最后一格。
      const hadBreak = $turnHadBubbleBreak.get()
      finalizeAssistantMessage(
        hadBreak ? undefined : payload?.text,
        payload?.media,
        hadBreak ? undefined : payload?.reasoning
      )

      if (typeof payload?.message_id === 'number') {
        bindTrailingAssistantMessageId(payload.message_id)
      }

      // 媒体已送达但生活空间收起：气泡只做轻量提示，点击打开生活空间查看（富媒体统一在对话窗展示）。
      if (payload?.media?.length && $surfaceOpen.get() === null && !screenLocked) {
        showMediaHint(
          payload.media.some(m => m.type === 'video')
            ? '🎬 我生成了一段视频，点这里查看'
            : '🖼️ 我生成了一张图片，点这里查看'
        )
      }

      // DESIGN §6.6 场景 1：纯情绪/动作回合无正文，空气泡已被上面剪掉——
      // 补一条情绪痕迹行，与后端持久化的 status_affect 行保持一致。
      if (!text.trim() && (hasEmotion || actions.length > 0)) {
        pushAffectTraceMessage()
      }

      if (hasEmotion) {
        maybeEmotionVfx(emotion)
      }

      const livingVoiceActive = isLivingVoiceBarActive()

      if (livingVoiceActive && text.trim()) {
        // TTS 等待保持「在想事情」；条开始播放进入「在说话」（左栏头像同步）；播完/打断回 idle（完成帧情绪叠加规则不变）。
        if (hasEmotion) {
          setTurnPendingEmotion({ actions, emotion: emotion as SpriteEmotion })
        } else {
          setTurnPendingEmotion(null)
        }
      } else {
        if (hasEmotion) {
          setSpriteState('emotional', { action: actions[0], emotion: emotion as SpriteEmotion })
          playSpriteActionSequence(actions)
        } else {
          setSpriteState('idle', { force: true })
        }
      }

      triggerFootGlowPulse('completed', 1200)

      if (!isProxy) {
        applySpatialCue(locale, target)
      }

      // 每日互动统计——chat_turn 仅在确有文本可统计时计数
      if (!isProxy && text.trim()) {
        reportInteractionStat('chat_turn')
      }

      // in-flight 回合结束——清掉标记并冲刷用户在回合运行期间排队的消息
      // （合并为单次批量提交）。
      $chatTurnInFlight.set(false)
      submitPendingBatch()

      break
    }

    case 'companion.affect': {
      // 云端独立 affect 推送承载情绪、空间与心境；动作序列走 message.complete 的 affect.actions。
      const payload = event.payload as { emotion?: string; locale?: string; mood?: string; target?: string } | undefined

      const emotion = payload?.emotion
      const locale = payload?.locale
      const target = payload?.target
      const mood = payload?.mood?.trim()
      const still = $effectiveTier.get() === 'still'

      // 静止档不消费主动心境推送（PROTOCOL：源头已停，此处兜底）。
      if (mood && !still) {
        $companionMood.set(mood)
      }

      // 情绪通道不受锁屏拦截（DESIGN §6.2）；静止档经防御性跳过——后端源头已断流，此处兜底。
      if (emotion && emotion !== 'neutral' && !still) {
        maybeEmotionVfx(emotion)
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion })
      }

      if (!isProxy) {
        applySpatialCue(locale, target)
      }

      break
    }

    case 'tool.start': {
      // 全局 WORKING 入口——所有工具（后端 / memory / runner）
      // 在执行开始前都会发 tool_start，因此无论工具位置如何精灵都会进入 WORKING。
      // tool.call（下方）只针对 Runner 工具触发，并携带 IPC 分发所需的参数。
      const p = event.payload as { name?: string } | undefined

      setAssistantTool(p?.name ?? '工具')
      setSpriteState('working')

      break
    }

    case 'tool.call': {
      // 仅 Runner 分发——桌面回合的 WORKING 由 tool.start 设置。
      // tool.call 是用户级设备指令（不带 session_id、按 call_id 关联），因此不经上方会话闸门；
      // 缺少 bridge 或 call_id 时后端的等待会在 300 秒后超时并上报错误。
      const p =
        (event.payload as
          | { name?: string; args?: Record<string, unknown>; call_id?: string; session_id?: string }
          | undefined) ?? {}

      const runnerInvoke = window.spiritagent?.runnerInvoke

      if (isProxy || !p.call_id || !runnerInvoke) {
        break
      }

      // 重放的重复指令直接丢弃：本机副作用不可撤销，宁可让后端等到超时也不能执行第二次。
      if (!markToolCallSeen(p.call_id)) {
        log.warn('events', `duplicate tool.call ${p.call_id} ignored (replayed frame)`)

        break
      }

      const name = p.name ?? ''

      // 该回合的帧是否落在用户正看着的会话里。不是则 tool.start / message.complete 都被上方
      // 会话闸门拦下，没有任何帧能驱动或复位精灵，只能由本分支自持工作态，让「伙伴在帮忙」的
      // 叙事在桌面成立（ARCHITECTURE §8 不变量 9）。
      // 用 session_id 比对而非枚举回合类型：遥控、自主、子 agent 乃至以后新增的回合种类都自动落对，
      // 枚举法漏一种就是「机器在动、精灵发呆」，且不会有任何报错。
      const selfDriven = !p.session_id || p.session_id !== $chatSessionId.get()

      if (selfDriven) {
        remoteToolDepth += 1
        setSpriteState('working', { force: true })
      }

      // fire-and-forget 调用 Runner 并把结果回传，让后端的
      // 等待解析完成；工具错误不得冒泡到本处理器。
      const gateway = $gateway.get()

      void (async () => {
        try {
          const args = p.args ?? {}

          // 仪式行走的解析目标：
          // - system.click_at 的目标就是点击坐标本身（包成虚拟窗口几何，走到旁边
          //   后 execute 即那次点击，不再额外补一次 click → 双击）；
          // - open_application / browser_* 按名称或 URL 匹配既有窗口；
          //   关键词缺失时重试也不会有结果，直接走常规调用。
          let findTarget: (() => Promise<WindowGeom | null>) | null = null
          let previewClick = true

          if (name === 'system.click_at') {
            const cx = Number(args.x)
            const cy = Number(args.y)

            if (Number.isFinite(cx) && Number.isFinite(cy)) {
              const geom: WindowGeom = {
                x: cx - CLICK_GEOM_HALF,
                y: cy - CLICK_GEOM_HALF,
                w: CLICK_GEOM_SIZE,
                h: CLICK_GEOM_SIZE
              }

              findTarget = () => Promise.resolve(geom)
              previewClick = false
            }
          } else {
            const keyword = String(args.name ?? args.url ?? args.keyword ?? '')

            if (keyword.trim()) {
              findTarget = () => findWindowByKeyword(keyword)
            }
          }

          const result = findTarget
            ? await performRitualWalk(findTarget, () => runnerInvoke(name, args), { previewClick })
            : await runnerInvoke(name, args)

          await gateway?.request('tool.result', { call_id: p.call_id, result })
        } catch (err) {
          try {
            // DESIGN §6.5「Runner 宕机人格化拒绝层」：原始错误不回传 LLM——
            // message 可能含路径/系统调用细节，LLM 可能照念给用户。诚实（承认没做到）
            // 但不暴露技术细节；原始错误只进本地日志留痕。
            log.warn('events', `runner tool ${name} failed:`, err)
            await gateway?.request('tool.result', {
              call_id: p.call_id,
              result: { ok: false, error: '（手没回应：本机执行器没有完成这次操作）' }
            })
          } catch {
            /* 尽力而为——后端的 300 秒兜底会处理 */
          }
        } finally {
          if (selfDriven) {
            releaseRemoteTool()
          }
        }
      })()

      break
    }

    case 'tool.complete': {
      // 全局 WORKING 出口——所有工具的 finally 块都会发 tool_end。
      // force：THINKING（50）< WORKING（70），没有 force 的话优先级门控会静默拒绝该转换。
      setAssistantTool(null)
      setSpriteState('thinking', { force: true })

      break
    }

    case 'model.ready': {
      // 后端在 /api/companion/model 生成结束后推送此事件。
      // 只要 $modelInfo.asset_url 变化，3D 引擎就会重新加载（见 companion-3d.tsx）。
      // error 字段用于展示生成失败；目前 UI 只是记录日志，恢复流程在后续切片。
      //
      // 二次 auth 防御：上方顶层 guard 只挡 'pending'，'unauthenticated' 的事件正常落地
      // 是为了 message.complete 不卡 thinking。但 model.ready 写持久化 atom（isPersistable
      // 通过 → localStorage），登出 race 里到达会污染下一位用户的冷启动读数。这里显式再挡一次。
      if (!authed()) {
        break
      }

      const p = event.payload as
        | {
            model_id?: number
            asset_url?: string
            species?: string
            rig_type?: string
            style?: string
            content_hash?: string
            error?: string
            clip_map?: Readonly<Record<string, string>>
          }
        | undefined

      if (p?.error) {
        log.warn('events', 'model.ready error:', p.error)
        setModelFailed(p.error)

        break
      }

      $modelGenState.set('succeeded')
      $modelGenProgress.set(null)
      $modelGenError.set(null)
      clearModelRetry()
      setModelInfo({
        id: p?.model_id ?? null,
        asset_url: p?.asset_url ?? null,
        species: p?.species ?? null,
        rig_type: p?.rig_type ?? 'biped',
        style: p?.style ?? 'realistic',
        content_hash: p?.content_hash ?? null,
        status: 'succeeded',
        has_rig: true
      })
      // 运行时新生成的模型必须在此接住映射，否则角色会一直静止到下次水合。
      $clipMap.set(p?.clip_map ?? {})

      break
    }

    case 'model.gen.progress': {
      const p = event.payload as { stage?: string; progress?: number } | undefined

      // 终态已定后,迟到的 progress 不能再把它打回 'generating' —— 否则覆盖层会重现。
      if ($modelGenState.get() === 'succeeded') {
        break
      }

      $modelGenState.set(p?.stage === 'done' ? 'succeeded' : 'generating')
      $modelGenProgress.set({ stage: p?.stage ?? '', progress: p?.progress ?? 0 })

      break
    }

    case 'model.failed': {
      const p = event.payload as { reason?: string; retry_download?: boolean; model_id?: number } | undefined
      setModelFailed(p?.reason ?? '3D 模型生成失败', {
        retryDownload: p?.retry_download === true,
        modelId: p?.model_id ?? null
      })

      break
    }

    case 'companion.assets.updated': {
      // 伙伴即时创建了新表情（create_expression 工具）；重新拉取让聊天窗无需重启即可用上。
      void hydrateExpressions()

      break
    }

    case 'companion.2d.ready': {
      // 2d 拆分完成——重新水合 2d 行并串一次 puppet 分流判定（manifest 恒为 kind=psd 描述符）。
      //
      // 二次 auth 防御：与 model.ready 同理，hydrateMesh2D 走 authedApi + 写持久化 atom，
      // 登出 race 里到达会把旧 session 的 manifest 写进 localStorage。这里早返回避免污染。
      if (!authed()) {
        break
      }

      const p = event.payload as
        | {
            model_id?: number
            manifest_url?: string | null
            layers?: { name: string; url: string }[]
          }
        | undefined

      if (p?.manifest_url) {
        log.info('events', '2d ready:', p.model_id)
      }

      void hydrateMesh2D().then(() => hydratePuppet())

      break
    }

    case 'companion.2d.failed': {
      // 切分失败：渲染层由 SpriteStage 兜底（程序化蛋 / 已就绪的 3D 模型）。
      const p = event.payload as { reason?: string } | undefined
      setMesh2DStatus('failed', p?.reason ?? '2D 切分失败')
      log.warn('events', '2d failed:', p?.reason)

      break
    }

    case 'companion.outfit.updated': {
      // 衣柜状态变化（切分就绪/穿着翻转/删除）——重拉列表；列表端点是真相源，事件只当刷新触发。
      // 仅穿着翻转时重水合 2d（幂等，与 2d.ready 双触发无妨）；入柜不换装与删除不动当前穿着。
      const p = event.payload as { worn?: boolean } | undefined

      void hydrateWardrobe()

      if (p?.worn) {
        void hydrateMesh2D().then(() => hydratePuppet())
      }

      break
    }

    case 'companion.outfit.failed': {
      const p = event.payload as { reason?: string } | undefined
      void hydrateWardrobe()
      log.warn('events', 'outfit failed:', p?.reason)

      break
    }

    case 'companion.render_mode.changed': {
      const p = event.payload as { new_mode?: '2d' | '3d' } | undefined

      if (p?.new_mode === '2d' || p?.new_mode === '3d') {
        void switchRenderMode(p.new_mode)
      }

      break
    }

    case 'avatar.regenerated': {
      // 后台重新生成的结果——通过 job_id 解析等待者，
      // 让肖像能直接替换而不阻塞处理器。
      const p = event.payload as
        | {
            job_id?: string
            asset_url?: string | null
            id?: number
            error?: string
          }
        | undefined

      if (p?.job_id) {
        resolveAvatarRegeneration(p)
      }

      // 与 model.ready / companion.2d.ready 同理：下方 resetMesh2D/resetPuppet 走
      // 定义了 Persisted atom 的 clear handler，登出 race 里触发会把刚清空的 localStorage
      // 又把 in-memory atom 写回 fallback（语义无害但与 clearCompanionStorage 重叠），
      // 之后的 hydrateMesh2D 在已登出窗口写持久化。这里同样加显式 auth 二次防御。
      if (!authed()) {
        break
      }

      // DESIGN §1.2 不变量：头像重生不使 2D/3D 模型失效——模型只随物种变更或用户
      // 显式请求重生。这里只做幂等的本地状态刷新（hydrate 重新拉取既有资产行）。
      resetMesh2D()
      resetPuppet()
      void hydrateMesh2D().then(() => hydratePuppet())

      break
    }

    case 'error': {
      cancelVoiceBar()
      $chatTurnInFlight.set(false)
      clearPendingPrompts()
      // 强制重置为 idle——精灵在 'thinking' / 'working' 时，
      // 优先级门控会静默拒绝普通的状态转换。
      const message = (event.payload as { message?: string } | undefined)?.message ?? '出了点小问题'
      markAssistantTerminal({ error: message })
      setSpriteState('idle', { force: true })
      triggerFootGlowPulse('failed', 2000)

      break
    }

    case 'companion.message': {
      const payload = event.payload as { text?: string; affect?: { emotion?: string } } | undefined
      const text = payload?.text ?? ''
      const currentTier = $effectiveTier.get()
      const affectEmotion = payload?.affect?.emotion

      // Affect 在文本之前流动，这样即便文本被抑制，反应仍能显示；静止档连主动情绪一并停
      // （后端源头已拦，此处兜底非官方链路）。
      if (affectEmotion && affectEmotion !== 'neutral' && currentTier !== 'still') {
        setSpriteState('emotional', { emotion: affectEmotion as SpriteEmotion })
      }

      // 静止档与锁屏会抑制气泡。
      const textSuppressed = currentTier === 'still' || $screenLocked.get()

      if (text && !textSuppressed) {
        if (shouldPlayAudio) {
          void speakProactive(text, { affect: affectEmotion })
        }

        // 无论生活空间当前是否在屏，均推入对话消息历史以供回溯查阅
        pushProactiveMessage(text)
      }

      break
    }

    case 'video_gen.completed': {
      // 后台视频任务完成（WSEvent outbox 路径，信封不带 session_id，载荷自带）。
      const p = event.payload as
        | { task_id?: string; url?: string; session_id?: string; media?: ChatMediaItem[] }
        | undefined

      const sessionId = p?.session_id
      const media: ChatMediaItem[] = p?.media?.length ? p.media : p?.url ? [{ type: 'video', url: p.url }] : []

      if (!media.length) {
        break
      }

      if (sessionId && sessionId === $chatSessionId.get()) {
        pushMediaMessage(media)
      } else if (!$screenLocked.get()) {
        // 正在看别的会话时用通知承载跳转；生活空间收起时用精灵气泡提示。
        if ($surfaceOpen.get() !== null && sessionId) {
          notify({
            kind: 'success',
            message: '视频生成好了',
            action: {
              label: '查看',
              onClick: () => {
                void switchSession(sessionId)
                void requestOpenSurface('living', { sessionId, view: 'chat' })
              }
            }
          })
        } else {
          showMediaHint('🎬 视频生成好了，点这里查看', sessionId)
        }
      }

      break
    }

    case 'video_gen.failed': {
      const p = event.payload as { error?: string } | undefined

      if (p?.error && !$screenLocked.get()) {
        notify({ kind: 'warning', message: p.error })
      }

      break
    }

    case 'channel.status': {
      // IM 通道绑定状态变化（outbox；Hub 设置页以 REST 为真相源，这里只做桌面提醒）。
      const p = event.payload as { channel?: string; status?: string; error?: string } | undefined
      const label = p?.channel === 'weixin_ilink' ? '微信' : 'IM 通道'

      const text =
        p?.status === 'connected'
          ? `${label}已连接`
          : p?.status === 'login_required'
            ? `${label}登录已过期，请到设置重新扫码`
            : p?.status === 'error'
              ? `${label}通道异常${p?.error ? `：${p.error}` : ''}`
              : null

      if (text && !$screenLocked.get()) {
        notify({ kind: p?.status === 'error' ? 'error' : 'info', message: text })
      }

      break
    }

    case 'channel.peer_request': {
      // 陌生对端首次来信（outbox）：提示主人到设置「聊天通道」审批。
      const p = event.payload as
        | { channel?: string; peer_id?: string; peer_name?: string; preview?: string }
        | undefined

      const label = p?.channel === 'weixin_ilink' ? '微信' : 'IM'
      const name = p?.peer_name || p?.peer_id || ''

      if (!$screenLocked.get()) {
        notify({
          kind: 'info',
          message: `${label}上有人想和伙伴聊天${name ? `：${name}` : ''}`,
          detail: p?.preview
        })
      }

      break
    }

    case 'command.result': {
      // 服务端在 command.dispatch RPC response 之外另行广播此事件（PROTOCOL §1.3）；
      // 触发它的窗口已通过 RPC 路径自己渲染过 pill，本路径只服务其他窗口的同步渲染。
      // RPC 路径的 pushStatusPill 已在 slash command 执行中幂等执行。
      const payload = event.payload as SlashCommandResultPayload | undefined

      const r = payload?.result

      if (!r) {
        break
      }

      // 仅同步 status_cleared / compress_summary 等历史变化（hydrate=true）：
      // 其他窗口需要本地 hydrateChatMessages，否则会显示陈旧消息列表。
      if (r.status === 'ok' && r.hydrate) {
        const messages = (r.payload as { messages?: unknown } | undefined)?.messages

        if (Array.isArray(messages)) {
          hydrateChatMessages(messages as SessionMessage[])
        }
      }

      break
    }

    case 'compress.completed': {
      // 自动压缩单行插入；手动 /压缩 走 command.result + hydrate=true，互斥互补（PROTOCOL §1.3）。
      const p = event.payload as { subtype?: string; text?: string; message_id?: number } | undefined

      if (p?.subtype === 'compress_summary' && typeof p.text === 'string') {
        pushStatusPill('compress_summary', p.text)
      }

      break
    }

    case 'message.deleted': {
      // 多窗口同步：session.undo_to_message RPC 之外，服务端另发此事件给同 user 的其他窗口。
      // 发起窗口已通过 RPC 路径 hydrate，其它窗口借本事件追上。session-id 过滤已在上面统一完成。
      const p = event.payload as
        | {
            session_id?: string
            deleted_count?: number
            anchor?: { text?: string; content_type?: string; media_json?: string | null }
            messages?: unknown[]
          }
        | undefined

      if (Array.isArray(p?.messages)) {
        hydrateChatMessages(p.messages as SessionMessage[])
      }

      // 跟随窗口从事件 payload 取 anchor 推到草稿总线——对话组件用 session_id 过滤应用。
      if (p?.session_id && p.anchor) {
        $chatDraftFromUndo.set({
          session_id: p.session_id,
          text: p.anchor.text ?? '',
          content_type: p.anchor.content_type ?? 'text',
          media_json: p.anchor.media_json ?? null
        })
      }

      break
    }

    case 'companion.room.ready':

    case 'companion.room.failed':

    case 'companion.room.invalidated':
    case 'companion.room.progress': {
      onBackdropEvent(event)

      break
    }

    case 'companion.moment.created':
    case 'companion.diary.upserted': {
      onJournalEvent(event)

      break
    }

    default:
      break
  }
}
