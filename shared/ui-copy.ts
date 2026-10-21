import type { ErrorKind } from './error-classification'

/**
 * 跨端共享的中性中文措辞。Client 与 Remote 对同一动作和同一故障必须说法一致，
 * 因此通用表述定义在此处；领域文案仍归各自应用的字典，不进本文件。
 *
 * 语体约定：省略号一律用「…」；进行时用「X中…」；失败用「X失败，请重试」；空态分「暂无X」（真的没有）
 * 与「没有找到匹配的X」（筛选无结果）两族，不混用。
 */
export const uiCopy = {
  loading: '加载中…',
  saving: '保存中…',
  processing: '处理中…',
  retry: '重试',
  copied: '已复制',
  copyFailed: '复制失败，请重试',
  operationFailed: '操作失败，请重试'
}

/** 失败类别的对外说明，两端逐字一致。 */
export const uiCopyErrors: Record<ErrorKind, string> = {
  network: '连接失败，请检查网络后重试。已保存的内容仍然保留。',
  rate_limited: '请求受限，请稍后重试。已保存的内容仍然保留。',
  provider: '服务不可用，请稍后重试。已保存的内容仍然保留。',
  server: '操作失败，请稍后重试。已提交的内容可以返回列表查看。',
  unauthorized: '登录已失效，请重新登录。',
  unknown: '操作失败，请稍后重试。'
}
