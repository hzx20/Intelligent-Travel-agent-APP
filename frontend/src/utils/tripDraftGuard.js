/** Confirm before discarding a trip draft or interrupting a pending write. */
export function allowTripDeparture(hasDraft, busy, confirm) {
  return !(hasDraft || busy) || confirm('还有未保存的输入、仅在本页显示的分享地址或正在进行的操作。离开后内容可能丢失，确定离开吗？')
}

export function guardTripUnload(event, hasDraft, busy) {
  if (hasDraft || busy) { event.preventDefault(); event.returnValue = '' }
}
