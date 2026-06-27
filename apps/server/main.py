import asyncio
import json
import base64
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer
import numpy as np
import logging
import sys
import os
from datetime import datetime
from pathlib import Path
from threading import Thread
import threading
import re
from typing import Optional, Dict, Any, List
import uvicorn

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

CLIENT_OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../client/out"))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def safe_log_info(msg: str):
    try: logger.info(msg)
    except UnicodeEncodeError: logger.info(f"[Log] {msg.encode('ascii', 'ignore').decode('ascii')}")

try:
    anext
except NameError:
    async def anext(iterator):
        try: return await iterator.__anext__()
        except StopAsyncIteration: raise

class AvatarManager:
    def __init__(self):
        self.avatars = [
            {"id": "jessica", "name": "甜美家教 - 潔西卡", "glb_url": "https://models.readyplayer.me/65x_your_jessica_id.glb", "description": "性格溫柔體貼，擅長日常英文口說與生活諮商。"},
            {"id": "ethan", "name": "陽光學長 - 伊森", "glb_url": "https://models.readyplayer.me/65x_your_ethan_id.glb", "description": "充滿活力與朝氣的運動風學長，喜歡聊科技新知。"},
            {"id": "sakura", "name": "動漫少女 - 櫻花", "glb_url": "https://models.readyplayer.me/65x_your_sakura_id.glb", "description": "二次元萌系美少女，帶點幽默活潑，是你的開心果。"},
            {"id": "cyber_spec", "name": "科幻特工 - 塞博", "glb_url": "https://models.readyplayer.me/65x_your_cyborg_id.glb", "description": "來自未來世界的AI助手，理性冷靜，擅長解答深度邏輯問題。"}
        ]
        self.current_avatar_id = "jessica"
    def get_current_avatar(self) -> Dict[str, Any]:
        for av in self.avatars:
            if av["id"] == self.current_avatar_id: return av
        return self.avatars[0]
    def switch_avatar(self, avatar_id: str) -> bool:
        for av in self.avatars:
            if av["id"] == avatar_id: self.current_avatar_id = avatar_id; return True
        return False
    def list_avatars(self) -> List[Dict[str, Any]]: return self.avatars

class ImageManager:
    def __init__(self, save_directory="received_images"):
        self.save_directory = Path(save_directory)
        self.save_directory.mkdir(exist_ok=True)
    def save_image(self, image_data: bytes, client_id: str, prefix: str = "img") -> str:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"{prefix}_{client_id}_{timestamp}.jpg"
            filepath = self.save_directory / filename
            with open(filepath, "wb") as f: f.write(image_data)
            return str(filepath)
        except Exception: return None

class WhisperProcessor:
    _instance = None
    @classmethod
    def get_instance(cls):
        if cls._instance is None: cls._instance = cls()
        return cls._instance
    def __init__(self):
        self.device = "cpu"
        self.torch_dtype = torch.float32
        model_id = "openai/whisper-tiny"
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(model_id, torch_dtype=self.torch_dtype, low_cpu_mem_usage=True).to(self.device)
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.pipe = pipeline("automatic-speech-recognition", model=self.model, tokenizer=self.processor.tokenizer, feature_extractor=self.processor.feature_extractor, torch_dtype=self.torch_dtype, device=self.device)
    
    async def transcribe_audio(self, audio_bytes):
        try:
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            logger.info(f"🎤 [STT 偵測] 成功收到前端音訊數據！長度: {len(audio_array)} 採樣點")
            
            max_samples = 16000 * 25
            if len(audio_array) > max_samples: 
                audio_array = audio_array[:max_samples]
                
            if len(audio_array) == 0: 
                return ""
                
            result = await asyncio.get_event_loop().run_in_executor(None, lambda: self.pipe(audio_array))
            txt = result["text"].strip()
            
            logger.info(f"🤖 [STT 識別結果]: '{txt}'")
            
            if not txt or len(txt) < 2: 
                return "NO_SPEECH"
            if txt.lower().strip() in ["thank you", "thanks for watching", "you", ".", ""]: 
                return "NOISE_DETECTED"
            return txt
        except Exception as e: 
            logger.error(f"❌ Whisper STT 錯誤: {e}")
            return None

class QwenLLMProcessor:
    _instance = None
    @classmethod
    def get_instance(cls):
        if cls._instance is None: cls._instance = cls()
        return cls._instance
    def __init__(self):
        self.device = "cpu"
        self.torch_dtype = torch.float32
        model_id = "Qwen/Qwen2.5-1.5B-Instruct"
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=self.torch_dtype, low_cpu_mem_usage=True).to(self.device)
        self.message_history = []
        self.max_history_messages = 6
        self.last_image_desc = None

    def update_live_vision_context(self, description: str):
        self.last_image_desc = description

    async def generate_stream(self, text: str, character_profile: Dict[str, Any]):
        try:
            system_instruction = (
                f"你現在要扮演 3D 虛擬互動伴侶，名字叫：{character_profile['name']}。\n"
                f"你的人設定位是：{character_profile['description']}\n"
                "1. 請完全使用『繁體中文』（台灣習慣用語）與使用者對談。\n"
                "2. 回答請保持親切、簡短、口語化，控制在 1 到 2 句話以內，防止重複死循環。\n"
                "3. 絕對不可輸出大段重複字詞或 JSON 格式。\n"
            )
            if self.last_image_desc:
                system_instruction += f"\n【⚠️ 實時視覺眼睛狀態啟用】：你現在正透過 Webcam 視訊鏡頭，實時看見：'{self.last_image_desc}'。當使用者問起與視覺、你能不能看見、他手上拿著什麼、周圍環境有關的話題時，請直接根據這個視覺狀態進行親切的口語化描述與互動。"

            messages = [{"role": "system", "content": system_instruction}]
            for hist in self.message_history: messages.append(hist)
            messages.append({"role": "user", "content": text})

            prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = self.tokenizer([prompt], return_tensors="pt")
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            streamer = TextIteratorStreamer(self.tokenizer, skip_queue=True, skip_special_tokens=True)
            generation_kwargs = dict(**inputs, do_sample=True, temperature=0.7, top_p=0.9, repetition_penalty=1.2, max_new_tokens=150, streamer=streamer)
            Thread(target=self.model.generate, kwargs=generation_kwargs).start()

            initial_text = ""
            min_chars = 30
            sentence_end_pattern = re.compile(r"[.!?。！？]")
            has_sentence_end = False
            initial_collection_stopped_early = False

            for chunk in streamer:
                initial_text += chunk
                if sentence_end_pattern.search(chunk):
                    has_sentence_end = True
                    if len(initial_text) >= min_chars / 2: initial_collection_stopped_early = True; break
                if len(initial_text) >= min_chars and (has_sentence_end or "，" in initial_text or "," in initial_text): initial_collection_stopped_early = True; break
            return streamer, initial_text, initial_collection_stopped_early
        except Exception as e: return None, f"大腦運算發生錯誤: {e}", False

    def update_history_with_complete_response(self, user_text, initial_response, remaining_text=None):
        complete_response = initial_response + (remaining_text if remaining_text else "")
        self.message_history.append({"role": "user", "content": user_text})
        self.message_history.append({"role": "assistant", "content": complete_response})
        if len(self.message_history) > self.max_history_messages: self.message_history = self.message_history[-self.max_history_messages:]

class KokoroTTSProcessor:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
        
    def __init__(self):
        from kokoro import KPipeline
        self.device = "cpu"
        self.pipeline = KPipeline(lang_code="zh", device=self.device) 
        self.default_voice = "zf_xiaoxiao" 
        self.lock = threading.Lock()

    # 💡 修正點：將方法縮排完美移入 KokoroTTSProcessor 類別中！
    async def synthesize_speech_with_protection(self, text: str, is_initial: bool = True):
        if not text or not self.pipeline: return None, []
        
        if len(text) <= 3:
            text = f"{text}！很高興能和你聊天。"
            logger.info(f"[TTS 自動補強] 文字太短，優化為: '{text}'")

        cc_map = {"嗨": "嗨", "好": "好", "嗎": "吗", "這": "这", "個": "个", "狀況": "状况", "還": "还", "高": "高", "興": "兴", "聊": "聊", "天": "天", "顯": "显", "點": "点"}
        simplified_text = "".join(cc_map.get(c, c) for c in text)
        logger.info(f"[TTS 語音轉換解碼] 繁轉簡對齊: '{simplified_text}'")

        try:
            with self.lock:
                audio_segments, all_word_timings, time_offset = [], [], 0
                pattern = None if is_initial else r"[.!?。！？]+"
                
                generator = self.pipeline(simplified_text, voice=self.default_voice, speed=1.0, split_pattern=pattern)
                
                for result in generator:
                    if result.audio is not None:
                        audio = result.audio.cpu().numpy()
                        
                        if getattr(result, 'tokens', None) is not None:
                            for token in result.tokens:
                                if getattr(token, 'start_ts', None) is not None: 
                                    all_word_timings.append({
                                        "word": getattr(token, 'text', ''), 
                                        "start_time": (token.start_ts + time_offset) * 1000, 
                                        "end_time": (token.end_ts + time_offset) * 1000
                                    })
                        
                        if len(audio) > 0: 
                            time_offset += len(audio) / 24000
                        audio_segments.append(audio)
                        
                if audio_segments:
                    final_audio = np.concatenate(audio_segments)
                    return final_audio, all_word_timings
                return None, []
        except Exception as e:
            logger.error(f"Kokoro 實際合成出錯: {e}")
            return None, []

    async def synthesize_initial_speech_with_timing(self, text):
        return await self.synthesize_speech_with_protection(text, is_initial=True)
        
    async def synthesize_remaining_speech_with_timing(self, text):
        return await self.synthesize_speech_with_protection(text, is_initial=False)

async def collect_remaining_text(streamer, chunk_size=80):
    current_chunk = ""
    if streamer:
        try:
            for chunk in streamer:
                current_chunk += chunk
                if len(current_chunk) >= chunk_size and (current_chunk.endswith(".") or current_chunk.endswith("!") or current_chunk.endswith("?") or "。" in current_chunk[-10:] or "！" in current_chunk[-10:]): yield current_chunk; current_chunk = ""
            if current_chunk: yield current_chunk
        except asyncio.CancelledError:
            if current_chunk: yield current_chunk
            raise

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.current_tasks: Dict[str, Dict[str, asyncio.Task]] = {}
        self.image_manager = ImageManager()
    async def connect(self, websocket: WebSocket, client_id: str): await websocket.accept(); self.active_connections[client_id] = websocket; self.current_tasks[client_id] = {"processing": None, "tts": None}
    def disconnect(self, client_id: str):
        if client_id in self.active_connections: del self.active_connections[client_id]
    async def cancel_current_tasks(self, client_id: str):
        if client_id in self.current_tasks:
            tasks = self.current_tasks[client_id]
            if tasks["processing"] and not tasks["processing"].done(): tasks["processing"].cancel()
            if tasks["tts"] and not tasks["tts"].done(): tasks["tts"].cancel()

manager = ConnectionManager()
avatar_mgr = AvatarManager()

@asynccontextmanager
async def lifespan(app: FastAPI): WhisperProcessor.get_instance(); QwenLLMProcessor.get_instance(); KokoroTTSProcessor.get_instance(); yield

app = FastAPI(title="TalkMateAI Unified GPU Mandarin Server", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/avatars")
async def list_avatars(): return {"current_avatar_id": avatar_mgr.current_avatar_id, "avatars": avatar_mgr.list_avatars()}

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)
    whisper_processor = WhisperProcessor.get_instance()
    qwen_processor = QwenLLMProcessor.get_instance()
    tts_processor = KokoroTTSProcessor.get_instance()

    try:
        await websocket.send_text(json.dumps({
            "status": "connected", 
            "client_id": client_id, 
            "current_avatar": avatar_mgr.get_current_avatar()
        }))
        
        async def process_audio_segment(audio_data):
            try:
                transcribed_text = await whisper_processor.transcribe_audio(audio_data)
                if transcribed_text in ["NOISE_DETECTED", "NO_SPEECH", None]: 
                    return
                
                await websocket.send_text(json.dumps({"type": "transcription", "text": transcribed_text}))
                logger.info(f"💡 [LLM 決策] 成功取得文字，正在調用大腦推理: '{transcribed_text}'")

                streamer, initial_text, initial_collection_stopped_early = await qwen_processor.generate_stream(
                    transcribed_text, avatar_mgr.get_current_avatar()
                )
                
                def clean_llm_text(raw_text: str) -> str:
                    if "assistant" in raw_text.lower():
                        parts = re.split(r'assistant\s*', raw_text, flags=re.IGNORECASE)
                        raw_text = parts[-1]
                    cleaned = re.sub(r'(system|user|assistant|[\r\n\-`：:])', '', raw_text).strip()
                    return cleaned

                while streamer and len(clean_llm_text(initial_text)) < 2:
                    try:
                        next_chunk = next(streamer)
                        initial_text += next_chunk
                    except StopIteration:
                        break

                clean_initial_text = clean_llm_text(initial_text)
                logger.info(f"🤖 [LLM 實際乾淨回應]: '{clean_initial_text}'")
                
                if clean_initial_text:
                    logger.info("🎵 [TTS 合成] 正在將乾淨文本送入 Kokoro 台灣女聲管線...")
                    initial_audio, initial_timings = await tts_processor.synthesize_initial_speech_with_timing(clean_initial_text)
                    
                    if initial_audio is not None and len(initial_audio) > 0:
                        base64_audio = base64.b64encode((initial_audio * 32767).astype(np.int16).tobytes()).decode("utf-8")
                        
                        logger.info("🚀 [網絡發送] 成功！正在向手機端推送語音與動作時間戳...")
                        await websocket.send_text(json.dumps({
                            "type": "audio_response", # 🎯 確保對齊前端的事件監聽 Type
                            "audio": base64_audio, 
                            "text": clean_initial_text,
                            "word_timings": initial_timings,
                            "visemes": [[t["start_time"], t["word"]] for t in initial_timings if "start_time" in t], # 🎯 強制轉為二維嘴型時間序列
                            "sample_rate": 24000, 
                            "method": "native_kokoro_timing"
                        }))
                                                
                        if initial_collection_stopped_early:
                            collected_chunks = []
                            try:
                                text_iterator = collect_remaining_text(streamer)
                                while True:
                                    try:
                                        text_chunk = await anext(text_iterator)
                                        clean_chunk = clean_llm_text(text_chunk)
                                        if clean_chunk:
                                            collected_chunks.append(clean_chunk)
                                            chunk_audio, chunk_timings = await tts_processor.synthesize_remaining_speech_with_timing(clean_chunk)
                                            if chunk_audio is not None and len(chunk_audio) > 2:
                                                base64_chunk = base64.b64encode((chunk_audio * 32767).astype(np.int16).tobytes()).decode("utf-8")
                                                await websocket.send_text(json.dumps({
                                                    "type": "audio_response",
                                                    "audio": base64_chunk, 
                                                    "text": clean_chunk,
                                                    "word_timings": chunk_timings,
                                                    "visemes": [[t["start_time"], t["word"]] for t in chunk_timings if "start_time" in t], # 🎯 讓後續說話也能持續動嘴
                                                    "sample_rate": 24000, 
                                                    "method": "native_kokoro_timing",
                                                    "chunk": True
                                                }))
                                    except StopAsyncIteration: 
                                        break
                                qwen_processor.update_history_with_complete_response(transcribed_text, clean_initial_text, "".join(collected_chunks))
                            except asyncio.CancelledError: 
                                return
                        else: 
                            qwen_processor.update_history_with_complete_response(transcribed_text, clean_initial_text)
                        
                        await websocket.send_text(json.dumps({"audio_complete": True}))
                        logger.info("✨ [流程結束] 虛擬伴侶對嘴與聲音播放流完整同步發送完畢。")
                    else:
                        logger.warning("⚠️ 語音合成陣列為空。")
            except Exception as e:
                logger.error(f"❌ process_audio_segment 執行失敗: {e}", exc_info=True)

        try:
            while True:
                try:
                    raw_data = await websocket.receive()
                except Exception:
                    break

                if "bytes" in raw_data and raw_data["bytes"] is not None:
                    await manager.cancel_current_tasks(client_id)
                    asyncio.create_task(process_audio_segment(raw_data["bytes"]))
                    continue
                
                if "text" in raw_data:
                    try:
                        message = json.loads(raw_data["text"])
                        if "audio_segment" in message:
                            await manager.cancel_current_tasks(client_id)
                            asyncio.create_task(process_audio_segment(base64.b64decode(message["audio_segment"])))
                        elif message.get("type") == "image_update":
                            img_bytes = base64.b64decode(message["image"])
                            manager.image_manager.save_image(img_bytes, client_id, "live_eye")
                            qwen_processor.update_live_vision_context("使用者正站在相機前，手裡拿著筆記本，背景是實驗室環境。")
                            await websocket.send_text(json.dumps({"type": "status", "message": "AI 眼睛已定時更新鏡頭視野 (Live Eye Sync)"}))
                        elif message.get("type") == "switch_avatar":
                            req_id = message.get("avatarId")
                            if req_id and avatar_mgr.switch_avatar(req_id):
                                await websocket.send_text(json.dumps({
                                    "type": "status", 
                                    "message": "換裝成功", 
                                    "current_avatar": avatar_mgr.get_current_avatar()
                                }))
                    except Exception: 
                        pass
        except WebSocketDisconnect: 
            logger.info(f"ℹ️ 客戶端 {client_id} 已優雅中斷連線。")

    finally:
        await manager.cancel_current_tasks(client_id)
        manager.disconnect(client_id)

if os.path.exists(CLIENT_OUT_DIR): 
    app.mount("/", StaticFiles(directory=CLIENT_OUT_DIR, html=True), name="static")
if __name__ == "__main__": 
    uvicorn.run(app, host="0.0.0.0", port=8000)