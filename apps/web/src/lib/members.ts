/**
 * The people in a team as one line: "Naman", "Naman & Heet", "Srishti, Ishita G. & Nikita".
 * Used wherever team members are shown (participant ballots, the console's editor and preview).
 */
export function formatMembers(members: readonly string[]): string {
  const names = members.map((name) => name.trim()).filter(Boolean)
  if (names.length <= 1) return names[0] ?? ''
  return `${names.slice(0, -1).join(', ')} & ${names[names.length - 1]}`
}
