import { ApiError, type RequestOptions } from '@/lib/api'
import { UploadQueue } from '@/lib/gallery-uploads'
import { adminAction } from './query'

const OUT_OF_SPACE = 'The gallery has run out of space. Free some up or raise its limit, then try again.'

/** adminAction, except that the storage messages speak to guests. Here, it is the organiser who can act. */
async function organiserCall<T>(path: string, options: RequestOptions): Promise<T> {
  try {
    return await adminAction<T>(path, options)
  } catch (error) {
    if (error instanceof ApiError && error.code === 'INSUFFICIENT_STORAGE') {
      throw new ApiError(error.status, error.code, OUT_OF_SPACE, error.details, error.requestId)
    }
    throw error
  }
}

let consoleQueue: UploadQueue | null = null

/**
 * The console's one queue. It lives as long as the tab, so moving to another console page does not
 * stop an upload, and coming back shows it. The warning before the tab closes lives with it.
 */
export function organiserQueue(direct: boolean): UploadQueue {
  if (consoleQueue) return consoleQueue
  const queue = new UploadQueue(direct, { call: organiserCall, root: '/admin/gallery/uploads' })
  const warn = (event: BeforeUnloadEvent) => event.preventDefault()
  let warning = false
  queue.subscribe(() => {
    if (queue.active === warning) return
    warning = queue.active
    if (warning) window.addEventListener('beforeunload', warn)
    else window.removeEventListener('beforeunload', warn)
  })
  consoleQueue = queue
  return queue
}

/** The queue, while it has files to send: they cannot finish once the session ends. */
export const queueStillSending = () => (consoleQueue?.active ? consoleQueue : null)
