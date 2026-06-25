'use client';

import React, { useEffect, useState } from 'react';
import { useWebSocket } from '../contexts/WebSocketContext';

interface AvatarProfile {
    id: string;
    name: string;
    glb_url: string;
    description: string;
}

export const AvatarSelector: React.FC = () => {
    const { socket, isConnected } = useWebSocket();
    const [avatarList, setAvatarList] = useState<AvatarProfile[]>([]);
    const [currentActiveId, setCurrentAvatarId] = useState<string>('jessica');

    // 💡 核心控制：管理小視窗是否為「展開」狀態，預設為 false (保持收合不遮擋)
    const [isOpen, setIsOpen] = useState<boolean>(false);

    useEffect(() => {
        const protocol = window.location.protocol;
        const host = window.location.host;

        fetch(`${protocol}//${host}/avatars`)
            .then((res) => res.json())
            .then((data) => {
                if (data.avatars) {
                    setAvatarList(data.avatars);
                    setCurrentAvatarId(data.current_avatar_id);
                }
            })
            .catch((err) => console.error("❌ 無法獲取 3D 伴侶清單:", err));
    }, []);

    const handleSwitchAvatar = (avatarId: string) => {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            alert("⚠️ WebSocket 尚未連線，無法切換角色！");
            return;
        }
        socket.send(JSON.stringify({ type: "switch_avatar", avatarId: avatarId }));
        setCurrentAvatarId(avatarId);

        // 💡 體驗優化：手機端使用者點擊切換完角色後，自動收合面板
        if (window.innerWidth < 768) {
            setIsOpen(false);
        }
    };

    return (
        // 🎯 採用 fixed 右上角懸浮定位，確保層級最頂級 (z-50)
        <div className="fixed top-4 right-4 z-50 flex flex-col items-end gap-2 text-white">

            {/* 🔮 按鈕 A：觸發收合的懸浮圓形齒輪按鈕 */}
            <button
                onClick={() => setIsOpen(!isOpen)}
                className={`flex h-12 w-12 items-center justify-center rounded-full shadow-2xl transition-all duration-300 backdrop-blur-md border ${isOpen
                        ? 'bg-red-500/80 border-red-400 rotate-90'
                        : 'bg-black/70 border-cyan-500/50 shadow-[0_0_15px_rgba(34,211,238,0.2)] hover:scale-105'
                    }`}
                title={isOpen ? "關閉選單" : "切換 3D 伴侶"}
            >
                {isOpen ? (
                    // 關閉 X 圖標
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                ) : (
                    // 智慧齒輪/精靈圖標
                    <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6 text-cyan-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                        <circle cx="12" cy="12" r="3" />
                    </svg>
                )}
            </button>

            {/* 🔮 區塊 B：滑出式角色控制面板 */}
            <div
                className={`bg-black/85 backdrop-blur-xl p-4 rounded-2xl border border-white/20 w-72 shadow-2xl transition-all duration-300 origin-top-right transform ${isOpen
                        ? 'opacity-100 scale-100 translate-y-0'
                        : 'opacity-0 scale-95 -translate-y-2 pointer-events-none'
                    }`}
            >
                <h3 className="text-xs font-bold mb-3 text-cyan-400 tracking-wider uppercase flex items-center gap-1">
                    ✨ 3D 伴侶角色切換庫
                </h3>

                <div className="flex flex-col gap-2 max-h-[60vh] overflow-y-auto pr-1">
                    {avatarList.map((avatar) => (
                        <button
                            key={avatar.id}
                            onClick={() => handleSwitchAvatar(avatar.id)}
                            className={`text-left p-2.5 rounded-xl border transition-all duration-200 ${currentActiveId === avatar.id
                                    ? 'bg-cyan-500/20 border-cyan-400 shadow-[0_0_12px_rgba(34,211,238,0.25)]'
                                    : 'bg-white/5 border-transparent hover:bg-white/10 hover:border-white/10'
                                }`}
                        >
                            <div className="font-semibold text-sm flex justify-between items-center">
                                <span>{avatar.name}</span>
                                {currentActiveId === avatar.id && (
                                    <span className="text-[10px] bg-cyan-400 text-black px-1.5 py-0.5 rounded-md font-bold scale-90">
                                        ACTIVE
                                    </span>
                                )}
                            </div>
                            <p className="text-[11px] text-gray-400 mt-1 line-clamp-2 leading-relaxed">
                                {avatar.description}
                            </p>
                        </button>
                    ))}
                </div>

                <div className="text-[9px] text-gray-500 mt-3 text-center border-t border-white/10 pt-2">
                    連線狀態: {isConnected ? '🟢 已與地端 CPU 小模型對齊' : '🔴 斷線中'}
                </div>
            </div>

        </div>
    );
};