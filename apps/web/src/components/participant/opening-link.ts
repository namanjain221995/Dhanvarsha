/**
 * Only links the API allows are followed: http(s) addresses and paths on this site. Never
 * "//host" or a backslash, which browsers read as another site.
 */
export function safeHref(link: string): string | null {
  const value = link.trim()
  if (/^https?:\/\/[^\s\\/?#]+[^\s\\]*$/i.test(value)) return value
  if (/^\/(?!\/)[^\s\\]*$/.test(value)) return value
  return null
}

/** Other sites open in a new tab; paths on this site in the same one. */
export const newTab = (href: string) => (href.startsWith('/') ? {} : { target: '_blank', rel: 'noopener noreferrer' })
