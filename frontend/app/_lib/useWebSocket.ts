'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ClientMessage, ServerMessage } from './types'

export type WsStatus = 'connecting' | 'connected' | 'disconnected'

const WS_URL = 'ws://localhost:8000/ws'
const RECONNECT_DELAY_MS = 3000

export function useWebSocket(onMessage: (msg: ServerMessage) => void) {
  const [status, setStatus] = useState<WsStatus>('connecting')
  const wsRef = useRef<WebSocket | null>(null)
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  useEffect(() => {
    let ws: WebSocket
    let reconnectTimer: ReturnType<typeof setTimeout>
    let destroyed = false

    function connect() {
      if (destroyed) return
      setStatus('connecting')

      ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () => {
        if (!destroyed) setStatus('connected')
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data as string) as ServerMessage
          onMessageRef.current(msg)
        } catch {
          // ignore malformed frames
        }
      }

      ws.onclose = () => {
        if (!destroyed) {
          setStatus('disconnected')
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS)
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    }

    connect()

    return () => {
      destroyed = true
      clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [])

  const send = useCallback((msg: ClientMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg))
    }
  }, [])

  return { status, send }
}
