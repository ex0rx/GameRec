export function formatPlaytime(minutes: number | null | undefined): string {
  if (minutes == null) return 'Playtime unavailable'
  if (minutes === 0) return 'Not played'
  if (minutes < 60) return `${minutes} min`
  const hours = Math.round((minutes / 60) * 10) / 10
  return `${hours} ${hours === 1 ? 'hr' : 'hrs'}`
}
