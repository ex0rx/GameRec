export const LIBRARY_PAGE_SIZE = 50

/** Syntactic validation only; this does not verify an account or its ownership. */
export function validLibrarySteamId(value: string | null | undefined): boolean {
  return /^[0-9]{17}$/.test(value?.trim() ?? '')
}

export function readLibraryPage(params: URLSearchParams): number {
  const raw = params.get('page')
  if (!raw || !/^[1-9][0-9]*$/.test(raw)) return 1
  const page = Number(raw)
  return Number.isSafeInteger(page) && Number.isSafeInteger((page - 1) * LIBRARY_PAGE_SIZE)
    ? page
    : 1
}

export function libraryPath(steamId: string, page = 1): string {
  return `/library/${encodeURIComponent(steamId.trim())}${page > 1 ? `?page=${page}` : ''}`
}

/** The signed-in Library never needs an account identifier in its address. */
export function myLibraryPath(page = 1): string {
  return `/library${page > 1 ? `?page=${page}` : ''}`
}
