import { useCallback, useEffect, useState } from 'react'

import { describeError } from '@/lib/api'

interface AsyncState<T> {
  data: T | undefined
  error: string | null
  loading: boolean
}

/** Load once per ``key``; ``reload`` fetches again and keeps showing the last data meanwhile. */
export function useAsync<T>(load: () => Promise<T>, key: string) {
  const [state, setState] = useState<AsyncState<T>>({ data: undefined, error: null, loading: true })
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    load().then(
      (data) => !cancelled && setState({ data, error: null, loading: false }),
      (err: unknown) =>
        !cancelled && setState((prev) => ({ ...prev, error: describeError(err), loading: false })),
    )
    return () => {
      cancelled = true
    }
    // ``load`` is a new closure every render; ``key`` says when it asks for something else.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce])

  const reload = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true }))
    setNonce((n) => n + 1)
  }, [])

  return { ...state, reload }
}
