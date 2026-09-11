import type { BackendClient } from '../backend/client'

const DEFAULT_TIMEOUT_MS = 120_000

// JSON-RPC 2.0 §5.1 — "Method not found".
const METHOD_NOT_FOUND_CODE = -32601

export interface BackendSessionLike {
  client: () => BackendClient
  getSession: () => null | { hasToken: boolean }
  getToken: () => null | string
}

export interface ReverseRpcOptions {
  backendSession?: BackendSessionLike | null
  log?: (chunk: string) => void
}

interface LlmMessageContentPart {
  image?: unknown
  image_url?: unknown
  text?: string
  type?: string
}

interface LlmMessage {
  content?: string | LlmMessageContentPart[]
  role?: string
  tool_call_id?: string
  tool_calls?: Array<Record<string, unknown>>
}

interface LlmResponseInputItem {
  arguments?: string
  call_id?: string
  content?: Array<Record<string, unknown>>
  name?: string
  output?: unknown
  role?: string
  type?: string
}

interface LlmCompletionParams {
  max_tokens?: number
  input?: unknown
  instructions?: string
  messages?: LlmMessage[]
  model?: string
  temperature?: number
}

export function createReverseRpc(
  options: ReverseRpcOptions = {}
): (method: string, params?: unknown) => Promise<unknown> {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const backendSession = options.backendSession

  if (!backendSession) {
    throw new TypeError('createReverseRpc requires options.backendSession.')
  }

  // 单会话累计限额；Runner 重新连接时（新的 WS 会话）配额会重置。
  const MAX_MESSAGES_PER_SESSION = 200
  const MAX_TEXT_BYTES_PER_SESSION = 1 * 1024 * 1024
  const MAX_VISION_BYTES_PER_SESSION = 10 * 1024 * 1024
  let sessionMessagesSent = 0
  let sessionBytesSent = 0

  function hasVisionContent(content: unknown): boolean {
    if (!Array.isArray(content)) {
      return false
    }

    for (const part of content) {
      if (
        typeof part === 'object' &&
        part !== null &&
        ((part as LlmMessageContentPart).type === 'input_image' ||
          (part as LlmMessageContentPart).type === 'image_url' ||
          (part as LlmMessageContentPart).image_url !== undefined)
      ) {
        return true
      }
    }

    return false
  }

  function contentToInputParts(content: unknown): Array<Record<string, unknown>> {
    if (typeof content === 'string') {
      return content ? [{ type: 'input_text', text: content }] : []
    }

    if (!Array.isArray(content)) {
      return []
    }

    const parts: Array<Record<string, unknown>> = []

    for (const part of content) {
      if (typeof part === 'string') {
        parts.push({ type: 'input_text', text: part })

        continue
      }

      if (typeof part !== 'object' || part === null) {
        continue
      }

      const source = part as LlmMessageContentPart

      if (source.type === 'text' || source.type === 'input_text') {
        parts.push({ type: 'input_text', text: source.text ?? '' })
      } else if (source.type === 'image' || source.type === 'image_url' || source.type === 'input_image') {
        const image = source.image_url ?? source.image
        const url = typeof image === 'object' && image !== null ? (image as { url?: unknown }).url : image

        if (url !== undefined && url !== null && url !== '') {
          parts.push({ type: 'input_image', image_url: url })
        }
      }
    }

    return parts
  }

  function toResponsesPayload(params: LlmCompletionParams): {
    instructions: string
    input: LlmResponseInputItem[] | string
  } {
    if (params.input !== undefined) {
      return {
        instructions: params.instructions ?? '',
        input: typeof params.input === 'string' ? params.input : (params.input as LlmResponseInputItem[])
      }
    }

    const messages = params.messages ?? []
    const instructionParts: string[] = []
    const items: LlmResponseInputItem[] = []
    let sawConversationItem = false

    for (const message of messages) {
      const role = message?.role

      if (role === 'system' && !sawConversationItem) {
        if (typeof message.content === 'string' && message.content) {
          instructionParts.push(message.content)
        }

        continue
      }

      sawConversationItem = true

      if (role === 'tool') {
        items.push({
          type: 'function_call_output',
          call_id: message.tool_call_id ?? '',
          output: message.content ?? ''
        })

        continue
      }

      const sourceParts = Array.isArray(message.content)
        ? message.content.map(part => part as Record<string, unknown>)
        : typeof message.content === 'string'
          ? [{ type: 'text', text: message.content }]
          : []

      const content =
        role === 'assistant'
          ? contentToInputParts(sourceParts).map(part => ({ type: 'output_text', text: part.text }))
          : contentToInputParts(sourceParts)

      if (content.length > 0) {
        items.push({ role: role ?? 'user', content })
      }

      for (const call of message.tool_calls ?? []) {
        const fn = (call.function ?? {}) as Record<string, unknown>
        items.push({
          type: 'function_call',
          call_id: typeof call.id === 'string' ? call.id : '',
          name: typeof fn.name === 'string' ? fn.name : '',
          arguments: typeof fn.arguments === 'string' ? fn.arguments : '{}'
        })
      }
    }

    return { instructions: instructionParts.join('\n\n'), input: items }
  }

  async function handleRequestLlm(params: unknown): Promise<unknown> {
    const session = backendSession!.getSession()

    if (!session?.hasToken) {
      throw new Error('No active session — cannot proxy LLM request.')
    }

    const payloadObj = (params as LlmCompletionParams) || {}
    const messages = payloadObj.messages || []
    const messageCount = messages.length
    const responsesPayload = toResponsesPayload(payloadObj)

    if (messageCount > MAX_MESSAGES_PER_SESSION) {
      throw new Error(
        `request_llm rejected: too many messages in this call (${messageCount} > ${MAX_MESSAGES_PER_SESSION}).`
      )
    }

    const payloadBytes = Buffer.byteLength(
      JSON.stringify({ input: responsesPayload.input, instructions: responsesPayload.instructions }),
      'utf8'
    )

    const isVision =
      Array.isArray(responsesPayload.input) && responsesPayload.input.some(item => hasVisionContent(item.content))

    const maxBytes = isVision ? MAX_VISION_BYTES_PER_SESSION : MAX_TEXT_BYTES_PER_SESSION

    if (payloadBytes > maxBytes) {
      throw new Error(`request_llm rejected: messages payload too large (${payloadBytes} bytes > ${maxBytes}).`)
    }

    sessionMessagesSent += messageCount || (Array.isArray(responsesPayload.input) ? responsesPayload.input.length : 1)

    if (sessionMessagesSent > MAX_MESSAGES_PER_SESSION) {
      throw new Error(
        `request_llm rejected: session exceeded ${MAX_MESSAGES_PER_SESSION} messages (sent ${sessionMessagesSent}).`
      )
    }

    sessionBytesSent += payloadBytes

    if (sessionBytesSent > maxBytes) {
      throw new Error(`request_llm rejected: session exceeded ${maxBytes} bytes (sent ${sessionBytesSent}).`)
    }

    const itemCount = Array.isArray(responsesPayload.input) ? responsesPayload.input.length : 1
    log(
      `[reverse-rpc] request_llm (${itemCount} input items, ${payloadBytes} bytes, session ${sessionMessagesSent}/${sessionBytesSent})`
    )

    const token = backendSession!.getToken()
    const client = backendSession!.client()

    return client.post('/api/llm/completion', {
      body: {
        input: responsesPayload.input,
        instructions: responsesPayload.instructions || undefined,
        max_output_tokens: payloadObj.max_tokens || undefined,
        model: payloadObj.model || undefined,
        temperature: payloadObj.temperature || undefined
      },
      timeoutMs: DEFAULT_TIMEOUT_MS,
      token: token || undefined
    })
  }

  const handlers: Record<string, (params: unknown) => Promise<unknown>> = {
    request_llm: handleRequestLlm
  }

  async function handleRequest(method: string, params?: unknown): Promise<unknown> {
    const handler = handlers[method]

    if (!handler) {
      const error = new Error(`Unknown reverse RPC method: ${method}`) as Error & { code?: number }
      error.code = METHOD_NOT_FOUND_CODE
      throw error
    }

    return handler(params)
  }

  return handleRequest
}
