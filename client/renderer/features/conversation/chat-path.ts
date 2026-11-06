export function basename(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path
}
