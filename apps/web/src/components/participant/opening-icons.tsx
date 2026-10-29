import {
  Award,
  BadgeIndianRupee,
  Crown,
  Dices,
  Film,
  Fish,
  Flame,
  Gamepad2,
  Gavel,
  Gift,
  GraduationCap,
  HeartHandshake,
  Hexagon,
  Lightbulb,
  Medal,
  MessagesSquare,
  Mic,
  Music,
  PhoneCall,
  Puzzle,
  Rocket,
  Sparkles,
  Star,
  Target,
  Trophy,
  Users,
  type LucideIcon,
  type LucideProps,
} from 'lucide-react'
import { createElement } from 'react'

/*
 * The icons an opening-page card can show. The keys are the API's ICON_KEYS (the API refuses
 * any other key); the labels are what the organiser's icon picker shows.
 */
const ICONS: Record<string, { icon: LucideIcon; label: string }> = {
  'phone-call': { icon: PhoneCall, label: 'Phone call' },
  trophy: { icon: Trophy, label: 'Trophy' },
  gavel: { icon: Gavel, label: 'Gavel' },
  'graduation-cap': { icon: GraduationCap, label: 'Graduation cap' },
  dices: { icon: Dices, label: 'Dice' },
  flame: { icon: Flame, label: 'Flame' },
  'badge-indian-rupee': { icon: BadgeIndianRupee, label: 'Rupee badge' },
  hexagon: { icon: Hexagon, label: 'Hexagon' },
  fish: { icon: Fish, label: 'Fish' },
  'messages-square': { icon: MessagesSquare, label: 'Conversation' },
  'heart-handshake': { icon: HeartHandshake, label: 'Heart handshake' },
  sparkles: { icon: Sparkles, label: 'Sparkles' },
  award: { icon: Award, label: 'Award ribbon' },
  medal: { icon: Medal, label: 'Medal' },
  puzzle: { icon: Puzzle, label: 'Puzzle' },
  target: { icon: Target, label: 'Target' },
  crown: { icon: Crown, label: 'Crown' },
  mic: { icon: Mic, label: 'Microphone' },
  film: { icon: Film, label: 'Film' },
  star: { icon: Star, label: 'Star' },
  lightbulb: { icon: Lightbulb, label: 'Light bulb' },
  rocket: { icon: Rocket, label: 'Rocket' },
  users: { icon: Users, label: 'People' },
  gift: { icon: Gift, label: 'Gift' },
  music: { icon: Music, label: 'Music' },
  gamepad: { icon: Gamepad2, label: 'Game controller' },
}

/** Every key this page can draw, in the picker's order. */
export const OPENING_ICON_KEYS = Object.keys(ICONS)

/** A readable name for an icon key; unknown keys are spelled out ("new-icon" → "New icon"). */
export function openingIconLabel(key: string): string {
  const known = ICONS[key]?.label
  if (known) return known
  const words = key.replace(/[-_]+/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Icon'
}

/** The card's icon. A key this build does not know (a newer API) falls back to sparkles. */
export function OpeningIcon({ name, ...props }: LucideProps & { name: string }) {
  return createElement(ICONS[name]?.icon ?? Sparkles, { 'aria-hidden': true, focusable: false, ...props })
}
