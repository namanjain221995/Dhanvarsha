'use client'

/*
 * No page scrolling behind the opening page's dialogs (the big card, the media viewer), without
 * the layout jumping where scrollbars take room. Counted, because the viewer can open over the
 * big card and either may close first: the page scrolls again only when both are gone.
 */

let holders = 0
let before = { overflow: '', gutter: '' }

export function lockScroll(): () => void {
  const root = document.documentElement
  if (holders === 0) {
    before = { overflow: root.style.overflow, gutter: root.style.scrollbarGutter }
    const scrollbar = window.innerWidth - root.clientWidth
    root.style.overflow = 'hidden'
    if (scrollbar > 0) root.style.scrollbarGutter = 'stable'
  }
  holders += 1
  let released = false
  return () => {
    if (released) return
    released = true
    holders -= 1
    if (holders === 0) {
      root.style.overflow = before.overflow
      root.style.scrollbarGutter = before.gutter
    }
  }
}
