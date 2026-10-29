'use client'

import { ArrowDown, ArrowUp, ChevronDown, Trash2 } from 'lucide-react'
import { useId } from 'react'
import { OpeningIcon, openingIconLabel } from '@/components/participant/opening-icons'
import { controlClass, InputField } from '../form'
import { IconButton } from '../primitives'
import { CircleImageField, OpeningCircle, type ImageRef } from './circle-image-field'

export type CardDraft = { id: string; title: string; icon: string; image: ImageRef; link: string }

/** Field errors of one card, by field name (title, icon, link, image_asset_id). */
export type CardErrors = Partial<Record<'title' | 'icon' | 'link' | 'image_asset_id', string>>

/** Ids of the row's controls, so focus can follow a card that moved. */
export const cardControlId = (cardId: string, control: 'title' | 'up' | 'down' | 'delete') => `opening-card-${control}-${cardId}`

/** One card of the opening page in the editor: its title, icon or picture, link and place in the order. */
export function CardRow({
  card,
  position,
  count,
  icons,
  errors,
  onChange,
  onMove,
  onDelete,
}: {
  card: CardDraft
  /** 0-based. */
  position: number
  count: number
  icons: string[]
  errors: CardErrors
  onChange: (patch: Partial<CardDraft>) => void
  onMove: (direction: -1 | 1) => void
  onDelete: () => void
}) {
  const id = useId()
  const number = position + 1
  const options = icons.includes(card.icon) ? icons : [card.icon, ...icons]
  const iconError = errors.icon

  return (
    <li className="rounded-2xl border border-sand bg-paper px-4 pb-4 pt-3 sm:px-5">
      <div role="group" aria-labelledby={`${id}-label`}>
        <div className="flex items-center gap-3">
          <OpeningCircle size={44}>
            {card.image.url ? (
              // eslint-disable-next-line @next/next/no-img-element -- API serves pre-optimised WebP
              <img src={card.image.url} alt="" className="absolute inset-0 size-full object-cover" />
            ) : (
              <OpeningIcon name={card.icon} className="size-[1.3rem]" strokeWidth={1.75} />
            )}
          </OpeningCircle>
          <div className="min-w-0 flex-1">
            <p id={`${id}-label`} className="text-sm font-bold text-plum">
              Card {number}
            </p>
            <p className="truncate text-sm text-plum-soft">{card.title.trim() || 'No title yet'}</p>
          </div>
          <div className="flex shrink-0 gap-0.5">
            <IconButton
              id={cardControlId(card.id, 'up')}
              label={`Move card ${number} up`}
              disabled={position === 0}
              onClick={() => onMove(-1)}
            >
              <ArrowUp aria-hidden className="size-[1.1rem]" />
            </IconButton>
            <IconButton
              id={cardControlId(card.id, 'down')}
              label={`Move card ${number} down`}
              disabled={position === count - 1}
              onClick={() => onMove(1)}
            >
              <ArrowDown aria-hidden className="size-[1.1rem]" />
            </IconButton>
            <IconButton
              id={cardControlId(card.id, 'delete')}
              label={`Delete card ${number}`}
              className="hover:bg-danger-wash hover:text-danger"
              onClick={onDelete}
            >
              <Trash2 aria-hidden className="size-[1.1rem]" />
            </IconButton>
          </div>
        </div>

        <div className="mt-3 grid gap-4 md:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <InputField
            id={cardControlId(card.id, 'title')}
            label="Title"
            value={card.title}
            maxLength={120}
            error={errors.title}
            onChange={(e) => onChange({ title: e.target.value })}
          />
          <div>
            <label htmlFor={`${id}-icon`} className="mb-1.5 block text-sm font-semibold text-plum">
              Icon
            </label>
            <div className="relative">
              <OpeningIcon
                name={card.icon}
                className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-gold-deep"
                strokeWidth={1.75}
              />
              <select
                id={`${id}-icon`}
                value={card.icon}
                aria-invalid={iconError ? true : undefined}
                aria-describedby={iconError ? `${id}-icon-error` : undefined}
                onChange={(e) => onChange({ icon: e.target.value })}
                className={`${controlClass} min-h-12 appearance-none pl-12 pr-11 ${iconError ? 'border-danger' : 'border-plum-faint/70'}`}
              >
                {options.map((key) => (
                  <option key={key} value={key}>
                    {openingIconLabel(key)}
                  </option>
                ))}
              </select>
              <ChevronDown aria-hidden className="pointer-events-none absolute right-4 top-1/2 size-4 -translate-y-1/2 text-plum-soft" />
            </div>
            {iconError ? (
              <p id={`${id}-icon-error`} className="mt-1.5 text-sm font-medium text-danger">
                {iconError}
              </p>
            ) : null}
          </div>
          <InputField
            label="Link"
            optional
            type="text"
            inputMode="url"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            placeholder="https://… or /path"
            value={card.link}
            maxLength={500}
            error={errors.link}
            onChange={(e) => onChange({ link: e.target.value })}
          />
          <CircleImageField
            label="Picture"
            kind="event_logo"
            image={card.image}
            size={56}
            fallback={<OpeningIcon name={card.icon} className="size-6" strokeWidth={1.75} />}
            fallbackText="Shows the icon."
            error={errors.image_asset_id}
            onChange={(image) => onChange({ image })}
          />
        </div>
      </div>
    </li>
  )
}
