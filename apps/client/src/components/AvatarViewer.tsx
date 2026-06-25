import React, { useEffect, useState } from 'react';
import { useWebSocket } from '../contexts/WebSocketContext';

export const AvatarViewer: React.FC = () => {
    const { socket } = useWebSocket();
    // 預設指向潔西卡的動態載入網址
    const [glbUrl, setGlbUrl] = useState<string>('https://models.readyplayer.me/65x_your_jessica_id.glb');

    useEffect(() => {
        if (!socket) return;

        // 💡 監聽後端回傳的角色更換確認事件
        const handleMessage = (event: MessageEvent) => {
            try {
                const data = json.parse(event.data);
                if (data.type === 'status' && data.current_avatar) {
                    // ⚡ 前端收到確認，即刻將 Three.js / TalkingHead 渲染器的 GLB 路徑動態更新！
                    console.log("🎨 前端開始動態更換 3D GLB 模型網格:", data.current_avatar.glb_url);
                    setGlbUrl(data.current_avatar.glb_url);
                }
            } catch (e) {
                // 容錯
            }
        };

        socket.addEventListener('message', handleMessage);
        return () => socket.removeEventListener('message', handleMessage);
    }, [socket]);

    return (
        <div className="w-full h-full">
            {/* 這裡對接您原本的 3D TalkingHead 或 Three.js 骨架渲染組件 */}
            {/* <TalkingHead modelUrl={glbUrl} /> */}
            <div className="absolute bottom-4 left-4 bg-black/50 text-white text-xs p-2 rounded">
                當前加載模型: <span className="text-yellow-400 font-mono text-[10px]">{glbUrl}</span>
            </div>
        </div>
    );
};