/** Identity refreshes may finish after a newer profile save. */
export function mergeUserInfo(current, next) {
  if (!current || current.id !== next.id) return {...next}
  const merged = {...current,...next}
  if ((next.profile_version || 0) < (current.profile_version || 0)) {
    for (const key of ['nickname','avatar','profile_version']) merged[key] = current[key]
  }
  return merged
}
