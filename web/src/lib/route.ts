import { useSyncExternalStore } from 'react'

// Hash routes, so a page reload or a shared link lands on the same session or page.
export type Route =
  | { page: 'chat'; sessionId: string | null }
  | { page: 'references'; query: string | null }
  | { page: 'tools' }
  | { page: 'evals' }

export function parseHash(hash: string): Route {
  const [path, search = ''] = hash.replace(/^#\/?/, '').split('?')
  const [page, id] = path.split('/')
  switch (page) {
    case 'references':
      return { page: 'references', query: new URLSearchParams(search).get('q') }
    case 'tools':
      return { page: 'tools' }
    case 'evals':
      return { page: 'evals' }
    default:
      return { page: 'chat', sessionId: page === 'chat' && id ? decodeURIComponent(id) : null }
  }
}

export function hrefFor(route: Route): string {
  switch (route.page) {
    case 'chat':
      return route.sessionId ? `#/chat/${encodeURIComponent(route.sessionId)}` : '#/chat'
    case 'references':
      return route.query ? `#/references?${new URLSearchParams({ q: route.query })}` : '#/references'
    default:
      return `#/${route.page}`
  }
}

export function navigate(route: Route): void {
  window.location.hash = hrefFor(route)
}

/** A reference pointer as words to search for: ``tone-playful#headlines`` → ``tone playful headlines``. */
export function pointerQuery(pointer: string): string {
  return pointer.replace(/[#-]/g, ' ')
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  return parseHash(hash)
}
