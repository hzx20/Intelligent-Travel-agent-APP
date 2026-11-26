/** Page-level guards for account changes that can invalidate local drafts. */
const guards = new Set()
export function registerAuthChangeGuard(guard) {
  guards.add(guard)
  return () => guards.delete(guard)
}
export function allowAuthChange() {
  for (const guard of guards) if (!guard()) return false
  return true
}
