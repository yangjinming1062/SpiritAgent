export function speechText(text: string): string {
  const clean = text
    .replace(/<\/?[a-z][^>]*>/gi, ' ')
    .replace(/\bMEDIA:\S+/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/(\*\*|__|~~)([\s\S]*?)\1/g, '$2')
    .replace(/`+([^`]+)`+/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/\p{Extended_Pictographic}[\p{Emoji_Modifier}\uFE0F\u200D]*/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  return /[\p{L}\p{N}]/u.test(clean) ? clean : ''
}
