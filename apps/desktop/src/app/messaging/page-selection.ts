import type { StatusTone } from '@/components/status-dot'
import type { MessagingPlatformInfo } from '@/hermes'

/**
 * The stored platform the detail pane shows for *selectedId*: none while the id names a catalog
 * row that has not been installed yet (its own detail takes the pane), else the platform that id
 * points at — the list's first as the fallback, so an id that left the list never blanks the pane.
 *
 * Kept here rather than inline: the page's render already carries its structure branches, and
 * these three belong to the selection, not to the layout.
 */
export function selectedPlatformFor(
  platforms: MessagingPlatformInfo[] | null,
  selectedId: string,
  catalogRowSelected: boolean
): MessagingPlatformInfo | null {
  if (!platforms || catalogRowSelected) {
    return null
  }

  return platforms.find(platform => platform.id === selectedId) ?? platforms[0] ?? null
}

/**
 * The status filter the tabs actually show: a tone whose last platform changed state is no longer
 * a tab, so it falls back to All instead of rendering an empty list.
 */
export function resolveStatusFilter(tone: 'all' | StatusTone, presentTones: readonly StatusTone[]): 'all' | StatusTone {
  return tone !== 'all' && presentTones.includes(tone) ? tone : 'all'
}

/** The draft map for one platform; an absent entry reads as "nothing edited". */
export function draftFor(edits: Record<string, Record<string, string>>, platformId: string): Record<string, string> {
  return edits[platformId] || {}
}
