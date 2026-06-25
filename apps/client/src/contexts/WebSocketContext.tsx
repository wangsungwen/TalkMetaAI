'use client';

import React, {
  createContext,
  useContext,
  useRef,
  useCallback,
  useState,
  ReactNode
} from 'react';

interface WordTiming {
  word: string;
  start_time: number;
  end_time: number;
}

interface WebSocketMessage {
  status?: string;
  client_id?: string;
  interrupt?: boolean;
  audio?: string;
  word_timings?: WordTiming[];
  sample_rate?: number;
  method?: string;
  audio_complete?: boolean;
  error?: string;
  type?: string;
}

interface WebSocketContextType {
  socket: WebSocket | null; // 💡 確保型別定義存在
  isConnected: boolean;
  isConnecting: boolean;
  connect: () => Promise<void>;
  disconnect: () => void;
  sendAudioSegment: (audioData: ArrayBuffer) => void;
  sendImage: (imageData: string) => void;
  sendAudioWithImage: (audioData: ArrayBuffer, imageData: string) => void;
  onAudioReceived: (
    callback: (
      audioData: string,
      timingData?: any,
      sampleRate?: number,
      method?: string
    ) => void
  ) => void;
  onInterrupt: (callback: () => void) => void;
  onError: (callback: (error: string) => void) => void;
  onStatusChange: (
    callback: (status: 'connected' | 'disconnected' | 'connecting') => void
  ) => void;
}

const WebSocketContext = createContext<WebSocketContextType | null>(null);

// 💡 為了相容原本的專案程式碼，保留 useWebSocketContext 命名
export const useWebSocketContext = () => {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error(
      'useWebSocketContext must be used within a WebSocketProvider'
    );
  }
  return context;
};

interface WebSocketProviderProps {
  children: ReactNode;
  serverUrl?: string;
}

export const WebSocketProvider: React.FC<WebSocketProviderProps> = ({
  children,
  serverUrl = 'ws://localhost:8000/ws/test-client'
}) => {
  const wsRef = useRef<WebSocket | null>(null);
  const [socket, setSocket] = useState<WebSocket | null>(null); // 💡 加增狀態管理 socket 變數
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);

  // Callback refs
  const audioReceivedCallbackRef = useRef<
    | ((
      audioData: string,
      timingData?: any,
      sampleRate?: number,
      method?: string
    ) => void)
    | null
  >(null);
  const interruptCallbackRef = useRef<(() => void) | null>(null);
  const errorCallbackRef = useRef<((error: string) => void) | null>(null);
  const statusChangeCallbackRef = useRef<
    ((status: 'connected' | 'disconnected' | 'connecting') => void) | null
  >(null);

  const connect = useCallback(async () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      setIsConnecting(true);
      statusChangeCallbackRef.current?.('connecting');

      // 💡 行動端自適應：如果傳入的是預設的 localhost，但實際上網頁是用外部 IP 或 ngrok 開啟，自動動態轉換協議網址
      let finalUrl = serverUrl;
      if (typeof window !== 'undefined' && (serverUrl.includes('localhost') || serverUrl.includes('127.0.0.1'))) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        finalUrl = `${protocol}//${host}/ws/test-client`;
      }

      console.log(`🔌 Connecting to WebSocket: ${finalUrl}`);
      const ws = new WebSocket(finalUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        setIsConnecting(false);
        setSocket(ws); // 💡 成功連線時將實體傳給 React 狀態
        statusChangeCallbackRef.current?.('connected');
        console.log('WebSocket connected successfully');
      };

      ws.onmessage = (event) => {
        try {
          const data: WebSocketMessage = JSON.parse(event.data);
          console.log('WebSocket message received:', data);

          if (data.status === 'connected') {
            console.log(
              `Server confirmed connection. Client ID: ${data.client_id}`
            );
          } else if (data.interrupt) {
            console.log('Received interrupt signal');
            interruptCallbackRef.current?.();
          } else if (data.audio) {
            let timingData = null;

            if (data.word_timings) {
              // 轉為 TalkingHead 標準發音對嘴嘴型格式
              timingData = {
                words: data.word_timings.map((wt) => wt.word),
                word_times: data.word_timings.map((wt) => wt.start_time),
                word_durations: data.word_timings.map(
                  (wt) => wt.end_time - wt.start_time
                )
              };
              console.log('Converted timing data:', timingData);
            }

            audioReceivedCallbackRef.current?.(
              data.audio,
              timingData,
              data.sample_rate || 24000,
              data.method || 'unknown'
            );
          } else if (data.audio_complete) {
            console.log('Audio processing complete');
          } else if (data.error) {
            errorCallbackRef.current?.(data.error);
          } else if (data.type === 'ping') {
            // Keepalive 保活
          }
        } catch (e) {
          console.log('Non-JSON message:', event.data);
        }
      };

      ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        errorCallbackRef.current?.('WebSocket connection error');
      };

      ws.onclose = () => {
        setIsConnected(false);
        setIsConnecting(false);
        setSocket(null); // 清空
        statusChangeCallbackRef.current?.('disconnected');
        console.log('WebSocket disconnected');
      };
    } catch (error) {
      setIsConnecting(false);
      errorCallbackRef.current?.('Failed to connect to WebSocket server');
    }
  }, [serverUrl]);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
      setSocket(null);
    }
  }, []);

  const sendAudioSegment = useCallback((audioData: ArrayBuffer) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      const bytes = new Uint8Array(audioData);
      let binary = '';
      for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
      }
      const base64Audio = btoa(binary);

      const message = {
        audio_segment: base64Audio
      };

      wsRef.current.send(JSON.stringify(message));
    }
  }, []);

  const sendImage = useCallback((imageData: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      const message = {
        type: 'image_update', // 💡 對齊單元二後端微調規格
        image: imageData,
        filename: 'webcam_frame.jpg'
      };

      wsRef.current.send(JSON.stringify(message));
      console.log('Sent image to server');
    }
  }, []);

  const sendAudioWithImage = useCallback(
    (audioData: ArrayBuffer, imageData: string) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        const bytes = new Uint8Array(audioData);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        const base64Audio = btoa(binary);

        const message = {
          audio_segment: base64Audio,
          image: imageData
        };

        wsRef.current.send(JSON.stringify(message));
        console.log(`Sent audio + image: ${audioData.byteLength} bytes audio`);
      }
    },
    []
  );

  const onAudioReceived = useCallback(
    (
      callback: (
        audioData: string,
        timingData?: any,
        sampleRate?: number,
        method?: string
      ) => void
    ) => {
      audioReceivedCallbackRef.current = callback;
    },
    []
  );

  const onInterrupt = useCallback((callback: () => void) => {
    interruptCallbackRef.current = callback;
  }, []);

  const onError = useCallback((callback: (error: string) => void) => {
    errorCallbackRef.current = callback;
  }, []);

  const onStatusChange = useCallback(
    (
      callback: (status: 'connected' | 'disconnected' | 'connecting') => void
    ) => {
      statusChangeCallbackRef.current = callback;
    },
    []
  );

  // 💡 關鍵型別修正：將 socket 補進 value 物件中傳遞！
  const value: WebSocketContextType = {
    socket,
    isConnected,
    isConnecting,
    connect,
    disconnect,
    sendAudioSegment,
    sendImage,
    sendAudioWithImage,
    onAudioReceived,
    onInterrupt,
    onError,
    onStatusChange
  };

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  );
};

// 💡 完美對齊 AvatarSelector.tsx 調用名稱，導出 useWebSocket Hook
export const useWebSocket = () => {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocket must be used within a WebSocketProvider');
  }
  return context;
};