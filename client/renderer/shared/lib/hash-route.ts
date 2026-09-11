// 三处都会重复的 hash 规范化：去前导 `#/`、截断 query 串、小写化、去端空格。
// 提取后调用方只需各自做"白名单 + 前缀/段切分 + 写回语义"——
// `clearSettingsHash`（清空 hash）与 `replaceState('#/${next}')`（覆写）仍各自保留。
export function normalizeHashPath(rawHash: string): string {
  return rawHash.replace(/^#\/?/, '').split('?')[0].toLowerCase().trim()
}
