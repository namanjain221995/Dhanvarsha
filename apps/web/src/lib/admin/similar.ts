/**
 * Client-side mirror of the server's correction check for frozen ballot titles and
 * nominee names: Python `difflib.SequenceMatcher(None, a, b).ratio() >= 0.8` on
 * case-folded, trimmed strings. Only a hint — the server decides.
 */
export function similarity(rawA: string, rawB: string): number {
  const a = rawA.trim().toLowerCase()
  const b = rawB.trim().toLowerCase()
  if (!a.length && !b.length) return 1
  const matches = matchingCharacters(a, b)
  return (2 * matches) / (a.length + b.length)
}

export function isSmallCorrection(before: string, after: string): boolean {
  return similarity(before, after) >= 0.8
}

function longestMatch(a: string, b: string, alo: number, ahi: number, blo: number, bhi: number): [number, number, number] {
  let besti = alo
  let bestj = blo
  let bestk = 0
  let previous = new Map<number, number>()
  for (let i = alo; i < ahi; i++) {
    const current = new Map<number, number>()
    for (let j = blo; j < bhi; j++) {
      if (a[i] !== b[j]) continue
      const k = (previous.get(j - 1) ?? 0) + 1
      current.set(j, k)
      if (k > bestk) {
        besti = i - k + 1
        bestj = j - k + 1
        bestk = k
      }
    }
    previous = current
  }
  return [besti, bestj, bestk]
}

function matchingCharacters(a: string, b: string): number {
  let total = 0
  const queue: [number, number, number, number][] = [[0, a.length, 0, b.length]]
  while (queue.length) {
    const [alo, ahi, blo, bhi] = queue.pop()!
    const [i, j, k] = longestMatch(a, b, alo, ahi, blo, bhi)
    if (!k) continue
    total += k
    if (alo < i && blo < j) queue.push([alo, i, blo, j])
    if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi])
  }
  return total
}
