// hash 路径规范化：去前导 `#/`、截断 query 串、小写化、去端空格。
export function normalizeHashPath(rawHash: string): string {
  return rawHash.replace(/^#\/?/, '').split('?')[0].toLowerCase().trim()
}
