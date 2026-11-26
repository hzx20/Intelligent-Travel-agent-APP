/** Classify a session check without exposing or modifying its token. */
export async function checkStoredSession(token, fetchUser, isCurrent) {
  if (!token) return {status:'anonymous'}
  try {
    const user = await fetchUser()
    return isCurrent(token) ? {status:'authenticated', user} : {status:'superseded'}
  } catch (error) {
    if (!isCurrent(token)) return {status:'superseded'}
    return {status:error?.status === 401 ? 'expired' : 'unavailable'}
  }
}
