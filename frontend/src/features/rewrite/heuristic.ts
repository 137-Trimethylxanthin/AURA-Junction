/**
 * Cheap local decisions for the rewrite card. Never calls a model: it only decides whether to *offer*
 * "Tidy this up", and splits text locally where the split is unambiguous.
 */

/** Joiners that clearly start a second question, per language (lowercase, matched on word boundaries). */
const JOINERS = [
  'and also',
  'and then',
  'also,',
  'plus,',
  'et aussi',
  'et puis',
  'ja myös',
  'ja sitten',
  'und auch',
  'und dann',
  'außerdem',
]

const joinerRe = new RegExp(`(?:^|[\\s,;])(${JOINERS.map((j) => j.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\s`, 'i')

/** True when the input looks long, rambling or like several questions (or came from voice). */
export function needsTidy(text: string, opts: { fromVoice?: boolean } = {}): boolean {
  const t = text.trim()
  if (t.length < 12) return false
  if (opts.fromVoice) return true
  if (t.length > 160) return true
  if ((t.match(/[?？]/g) ?? []).length >= 2) return true
  return joinerRe.test(t)
}

/**
 * Split on question marks and on the clear joiners above. Returns at least two parts, or [] when there is
 * nothing honest to split (the card then is not offered in local mode).
 */
export function splitLocally(text: string): string[] {
  const t = text.trim()
  // First split after question marks, then inside each piece on clear joiners.
  const bySentence = t
    .split(/(?<=[?？])\s+/)
    .map((s) => s.trim())
    .filter(Boolean)
  const parts: string[] = []
  for (const s of bySentence) {
    let rest = s
    for (;;) {
      const m = joinerRe.exec(rest)
      if (!m || m.index === undefined) break
      const cut = m.index + (m[0].length - m[1].length - 1)
      const head = rest.slice(0, cut).replace(/[\s,;]+$/, '').trim()
      if (head) parts.push(head)
      rest = rest.slice(cut + m[1].length + 1).trim()
    }
    if (rest) parts.push(rest)
  }
  const clean = parts.map((p) => p.replace(/^[,;\s]+/, '').trim()).filter((p) => p.length >= 3)
  if (clean.length < 2) return []
  // Capitalise each part so it reads as its own question.
  return clean.map((p) => p.charAt(0).toUpperCase() + p.slice(1))
}
