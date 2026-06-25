'use client';

import React, { useEffect, useRef } from 'react';
import { useWebSocket } from '../contexts/WebSocketContext';

interface CameraStreamProps {
  cameraStream: MediaStream | null;
}

export const CameraStream: React.FC<CameraStreamProps> = ({ cameraStream }) => {
  const { socket } = useWebSocket();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // 1. 當主頁面的 cameraStream 狀態改變時，動態將視訊流掛載到網頁隱藏的 <video> 標籤
  useEffect(() => {
    if (videoRef.current && cameraStream) {
      videoRef.current.srcObject = cameraStream;
      console.log("🟢 Live Eye 視訊流已成功掛載至網頁組件");
    }
  }, [cameraStream]);

  // 2. 核心黃金定時器：每 3 秒對影像進行 HTML5 Canvas 擷圖並轉成 0.6 壓縮率之 JPEG Base64 發送
  useEffect(() => {
    const interval = setInterval(() => {
      if (!cameraStream || !videoRef.current || !canvasRef.current || !socket || socket.readyState !== WebSocket.OPEN) {
        return; // 任何一個環節未就緒，自動跳過，不造成通訊堵塞
      }

      const context = canvasRef.current.getContext('2d');
      if (context) {
        // 將 320x240 的即時畫面繪製到 Canvas
        context.drawImage(videoRef.current, 0, 0, 320, 240);

        // 轉為 0.6 壓縮率的 JPEG base64 字串，防禦行動端穿透帶寬過載
        const base64Image = canvasRef.current.toDataURL('image/jpeg', 0.6);
        const cleanBase64 = base64Image.replace(/^data:image\/jpeg;base64,/, "");

        // 💡 透過統一的通訊協議推送至 FastAPI 後端進行 Context 覆蓋
        socket.send(JSON.stringify({
          type: "image_update",
          image: cleanBase64,
          filename: "webcam_frame.jpg"
        }));

        console.log("👁️ [Live Eye Sync] 即時畫面已擷取並推送至地端大腦 Context");
      }
    }, 3000); // 💡 每 3 秒更新一次視覺記憶

    return () => clearInterval(interval);
  }, [cameraStream, socket]);

  return (
    <div className="hidden">
      {/* 隱藏的音訊視訊幀捕獲節點 */}
      <video ref={videoRef} autoPlay playsInline muted width="320" height="240" />
      <canvas ref={canvasRef} width="320" height="240" />
    </div>
  );
};

// 為了相容您 Home 頁面的 CameraToggleButton 命名，保留對接導出
export const CameraToggleButton: React.FC<{ onStreamChange: (stream: MediaStream | null) => void }> = ({ onStreamChange }) => {
  const [isCameraOn, setIsCameraOn] = React.useState(false);

  const toggleCamera = async () => {
    if (isCameraOn) {
      onStreamChange(null);
      setIsCameraOn(false);
    } else {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240 } });
        onStreamChange(stream);
        setIsCameraOn(true);
      } catch (err) {
        alert("無法獲取視訊鏡頭權限，請確認是否為 HTTPS 加密網域！");
      }
    }
  };

  return (
    <button
      onClick={toggleCamera}
      className={`fixed bottom-4 right-4 z-50 p-4 rounded-full shadow-2xl text-white font-bold transition-all ${isCameraOn ? 'bg-red-500 hover:bg-red-600' : 'bg-cyan-500 hover:bg-cyan-600'
        }`}
    >
      {isCameraOn ? '关闭 Live Eye 眼睛' : '开启 Live Eye 眼睛'}
    </button>
  );
};