import asyncio
import json
import base64
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer
import numpy as np
import logging
import sys
import io
from PIL import Image
import time
import os
from datetime import datetime
from pathlib import Path
from threading import Thread
import re
from typing import Optional, Dict, Any, List
import uvicorn

# FastAPI 相關組件導入
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager

# 💡 自動識別 Next.js 靜態匯出目錄 (out 資料夾)
CLIENT_OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../client/out"))

# 配置日誌
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

def safe_log_info(msg: str):
    try:
        logger.info(msg)
    except UnicodeEncodeError:
        clean_msg = msg.encode('ascii', 'ignore').decode('ascii')
        logger.info(f"[Log] {clean_msg}")

try:
    anext
except NameError:
    async def anext(iterator):
        try:
            return await iterator.__anext__()
        except StopAsyncIteration:
            raise


# ----------------------------------------------------------------------
# 1. 3D 伴侶角色數據庫與動態切換管理器 (Avatar Manager)
# ----------------------------------------------------------------------
class AvatarManager:
    """管理 TalkMateAI 多組 3D GLB 伴侶角色的設定與動態切換"""
    def __init__(self):
        self.avatars = [
            {"id": "jessica", "name": "甜美家教 - 潔西卡", "gender": "female", "glb_url": "https://models.readyplayer.me/65x_your_jessica_id.glb", "voice": "zs_jessica", "description": "性格溫柔體貼，擅長以親切的語氣指導日常英文口說與生活諮商。"},
            {"id": "ethan", "name": "陽光學長 - 伊森", "gender": "male", "glb_url": "https://models.readyplayer.me/65x_your_ethan_id.glb", "voice": "zs_jessica", "description": "充滿活力與朝氣的運動風學長，喜歡聊運動、旅遊與科技新知。"},
            {"id": "sakura", "name": "動漫少女 - 櫻花", "gender": "female", "glb_url": "https://models.readyplayer.me/65x_your_sakura_id.glb", "voice": "zs_jessica", "description": "二次元萌系美少女，帶點幽默與活潑的語調，是你的開心果。"},
            {"id": "cyber_spec", "name": "科幻特工 - 塞博", "gender": "cyborg", "glb_url": "https://models.readyplayer.me/65x_your_cyborg_id.glb", "voice": "zs_jessica", "description": "來自未來世界的AI人工智慧助手，理性冷靜，擅長解答深度邏輯問題。"}
        ]
        self.current_avatar_id = "jessica"
        safe_log_info("3D Avatar Profile Database initialized successfully.")

    def get_current_avatar(self) -> Dict[str, Any]:
        for av in self.avatars:
            if av["id"] == self.current_avatar_id: return av
        return self.avatars[0]

    def switch_avatar(self, avatar_id: str) -> bool:
        for av in self.avatars:
            if av["id"] == avatar_id:
                self.current_avatar_id = avatar_id
                safe_log_info(f"成功將 3D 伴侶更換為: {av['name']}")
                return True
        return False

    def list_avatars(self) -> List[Dict[str, Any]]:
        return self.avatars


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


# ----------------------------------------------------------------------
# 2. Whisper 語音識別單例類別 (STT) — CPU 穩定版
# ----------------------------------------------------------------------
class WhisperProcessor:
    """地端語音識別處理器 (STT) — 強制鎖定 CPU (float32)"""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None: cls._instance = cls()
        return cls._instance

    def __init__(self):
        # 💡 CPU 版本強行死鎖
        self.device = "cpu"
        self.torch_dtype = torch.float32
        safe_log_info(f"Using device for Whisper: {self.device}")

        model_id = "openai/whisper-tiny"
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id, torch_dtype=self.torch_dtype, low_cpu_mem_usage=True, use_safetensors=True
        ).to(self.device)
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.pipe = pipeline(
            "automatic-speech-recognition", model=self.model, tokenizer=self.processor.tokenizer,
            feature_extractor=self.processor.feature_extractor, torch_dtype=self.torch_dtype, device=self.device
        )
        self.transcription_count = 0

    async def transcribe_audio(self, audio_bytes):
        try:
            # 1. 將前端傳入的二進位 Byte 數據流轉換為 NumPy 陣列，並正規化至 [-1.0, 1.0]
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            
            # 🛡️ 【終極防禦補丁】：防止音訊緩衝環路爆炸
            # Whisper 標準音訊採樣率為 16000Hz。30秒限制對應 16000 * 30 = 480000 個採樣點。
            # 我們安全地將上限設定為 25 秒（16000 * 25 = 400000），只要超過就強制切斷，從根本上杜絕 3000 mel 錯誤！
            max_samples = 16000 * 25 
            if len(audio_array) > max_samples:
                logger.warning(f"偵測到超長音訊或回音環路 ({len(audio_array)} 採樣)，後端已自動強制截斷至 25 秒，保護模型推理！")
                audio_array = audio_array[:max_samples] # 強制切片，只保留前 25 秒

            # 2. 檢查截斷後是否為空封包
            if len(audio_array) == 0: 
                return ""
            
            # 3. 調用異步執行器（Thread Pool）將音訊陣列送入 Whisper Pipeline 進行地端語音識別
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.pipe(audio_array)
            )
            
            # 4. 清理並取出識別出的文字
            txt = result["text"].strip()
            self.transcription_count += 1
            
            # 5. 空白或無意義的短音訊（低於2個字）判定為未說話
            if not txt or len(txt) < 2: 
                return "NO_SPEECH"
            
            # 6. 幻覺與背景雜音防禦：排除 Whisper 在安靜或空白環境下容易產生的英文幻覺字詞
            noise_indicators = ["thank you", "thanks for watching", "you", ".", "", "謝謝"]
            if txt.lower().strip() in noise_indicators: 
                return "NOISE_DETECTED"
            
            # 7. 成功識別，回傳乾淨的繁體中文文本
            return txt
            
        except Exception as e:
            logger.error(f"Whisper STT 錯誤（已啟用防禦攔截）: {e}")
            return None


# ----------------------------------------------------------------------
# 3. Qwen2.5-1.5B-Instruct 大腦處理器 (繁體中文專精) — CPU 穩定版
# ----------------------------------------------------------------------
class QwenLLMProcessor:
    """純文字 LLM 大腦 (繁體中文專精) — 強制鎖定 CPU (float32)"""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None: cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.device = "cpu"
        self.torch_dtype = torch.float32
        safe_log_info(f"Using device for QwenLLM: {self.device}")

        model_id = "Qwen/Qwen2.5-1.5B-Instruct"
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype=self.torch_dtype, low_cpu_mem_usage=True
        ).to(self.device)
        self.message_history = []
        self.max_history_messages = 6
        self.last_image_desc = None

    def update_live_vision_context(self, description: str):
        self.last_image_desc = description

    async def generate_stream(self, text: str, character_profile: Dict[str, Any]):
        try:
            system_instruction = (
                f"你現在要扮演 3D 虛擬互動伴侶，名字叫：{character_profile['name']}。\n"
                f"你的人設定位與講話風格是：{character_profile['description']}\n"
                "1. 請完全使用『繁體中文』（台灣習慣用語）與使用者對談。\n"
                "2. 回答請保持親切、簡短、口語化，控制在 1 到 2 句話以內，防止重複死循環。\n"
                "3. 絕對不可輸出大段重複字詞或 JSON 格式。\n"
            )
            if self.last_image_desc:
                system_instruction += f"\n【實時視覺眼睛狀態】：你現在看見：'{self.last_image_desc}'。"

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
                    if len(initial_text) >= min_chars / 2:
                        initial_collection_stopped_early = True
                        break
                if len(initial_text) >= min_chars and (has_sentence_end or "，" in initial_text or "," in initial_text):
                    initial_collection_stopped_early = True
                    break
            return streamer, initial_text, initial_collection_stopped_early
        except Exception as e:
            return None, f"大腦運算發生錯誤: {e}", False

    def update_history_with_complete_response(self, user_text, initial_response, remaining_text=None):
        complete_response = initial_response + (remaining_text if remaining_text else "")
        self.message_history.append({"role": "user", "content": user_text})
        self.message_history.append({"role": "assistant", "content": complete_response})
        if len(self.message_history) > self.max_history_messages: self.message_history = self.message_history[-self.max_history_messages:]


# ----------------------------------------------------------------------
# 4. Kokoro TTS 中文語音與 Viseme 嘴型同步處理器 (TTS) — CPU 版
# ----------------------------------------------------------------------
class KokoroTTSProcessor:
    """語音合成處理器 (TTS) — 繁體中文台灣女聲管線 (強制鎖定 CPU)"""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None: cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.device = "cpu"
        from kokoro import KPipeline
        self.pipeline = KPipeline(lang_code="z", device="cpu")
        self.default_voice = "zs_jessica"
        safe_log_info(f"Kokoro TTS processor initialized successfully on {self.device}")

    async def synthesize_initial_speech_with_timing(self, text):
        if not text or not self.pipeline: return None, []
        try:
            audio_segments, all_word_timings, time_offset = [], [], 0
            generator = await asyncio.get_event_loop().run_in_executor(None, lambda: self.pipeline(text, voice=self.default_voice, speed=1, split_pattern=None))
            for result in generator:
                audio = result.audio.cpu().numpy()
                for token in result.tokens:
                    if token.start_ts is not None:
                        all_word_timings.append({"word": token.text, "start_time": (token.start_ts + time_offset) * 1000, "end_time": (token.end_ts + time_offset) * 1000})
                if len(audio) > 0: time_offset += len(audio) / 24000
                audio_segments.append(audio)
            return np.concatenate(audio_segments) if audio_segments else None, all_word_timings
        except Exception: return None, []

    async def synthesize_remaining_speech_with_timing(self, text):
        if not text or not self.pipeline: return None, []
        try:
            audio_segments, all_word_timings, time_offset = [], [], 0
            generator = await asyncio.get_event_loop().run_in_executor(None, lambda: self.pipeline(text, voice=self.default_voice, speed=1, split_pattern=r"[.!?。！？]+"))
            for result in generator:
                audio = result.audio.cpu().numpy()
                for token in result.tokens:
                    if token.start_ts is not None:
                        all_word_timings.append({"word": token.text, "start_time": (token.start_ts + time_offset) * 1000, "end_time": (token.end_ts + time_offset) * 1000})
                if len(audio) > 0: time_offset += len(audio) / 24000
                audio_segments.append(audio)
            return np.concatenate(audio_segments) if audio_segments else None, all_word_timings
        except Exception: return None, []


async def collect_remaining_text(streamer, chunk_size=80):
    current_chunk = ""
    if streamer:
        try:
            for chunk in streamer:
                current_chunk += chunk
                if len(current_chunk) >= chunk_size and (
                    current_chunk.endswith(".") or current_chunk.endswith("!") or current_chunk.endswith("?") or "。" in current_chunk[-10:] or "！" in current_chunk[-10:]
                ):
                    yield current_chunk
                    current_chunk = ""
            if current_chunk:
                yield current_chunk
        except asyncio.CancelledError:
            if current_chunk:
                yield current_chunk
            raise


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.current_tasks: Dict[str, Dict[str, asyncio.Task]] = {}
        self.image_manager = ImageManager()

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        self.current_tasks[client_id] = {"processing": None, "tts": None}

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
async def lifespan(app: FastAPI):
    WhisperProcessor.get_instance()
    QwenLLMProcessor.get_instance()
    KokoroTTSProcessor.get_instance()
    yield


app = FastAPI(title="TalkMateAI Unified CPU Mandarin Server", version="1.0.0", lifespan=lifespan)
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
        await websocket.send_text(json.dumps({"status": "connected", "client_id": client_id, "current_avatar": avatar_mgr.get_current_avatar()}))
        
        async def process_audio_segment(audio_data):
            await websocket.send_text(json.dumps({"interrupt": True}))
            transcribed_text = await whisper_processor.transcribe_audio(audio_data)
            if transcribed_text in ["NOISE_DETECTED", "NO_SPEECH", None]: return
            await websocket.send_text(json.dumps({"type": "transcription", "text": transcribed_text}))

            streamer, initial_text, initial_collection_stopped_early = await qwen_processor.generate_stream(transcribed_text, avatar_mgr.get_current_avatar())
            if initial_text:
                initial_audio, initial_timings = await tts_processor.synthesize_initial_speech_with_timing(initial_text)
                if initial_audio is not None and len(initial_audio) > 0:
                    base64_audio = base64.b64encode((initial_audio * 32767).astype(np.int16).tobytes()).decode("utf-8")
                    await websocket.send_text(json.dumps({"audio": base64_audio, "word_timings": initial_timings, "sample_rate": 24000, "method": "native_kokoro_timing", "modality": "audio_only"}))
                    
                    if initial_collection_stopped_early:
                        collected_chunks = []
                        try:
                            text_iterator = collect_remaining_text(streamer)
                            while True:
                                try:
                                    text_chunk = await anext(text_iterator)
                                    collected_chunks.append(text_chunk)
                                    chunk_audio, chunk_timings = await tts_processor.synthesize_remaining_speech_with_timing(text_chunk)
                                    if chunk_audio is not None and len(chunk_audio) > 0:
                                        base64_chunk = base64.b64encode((chunk_audio * 32767).astype(np.int16).tobytes()).decode("utf-8")
                                        await websocket.send_text(json.dumps({"audio": base64_chunk, "word_timings": chunk_timings, "sample_rate": 24000, "method": "native_kokoro_timing", "chunk": True, "modality": "audio_only"}))
                                except StopAsyncIteration: break
                            qwen_processor.update_history_with_complete_response(transcribed_text, initial_text, "".join(collected_chunks))
                        except asyncio.CancelledError: return
                    else:
                        qwen_processor.update_history_with_complete_response(transcribed_text, initial_text)
                    await websocket.send_text(json.dumps({"audio_complete": True}))

        while True:
            raw_data = await websocket.receive()
            if "bytes" in raw_data and raw_data["bytes"] is not None:
                await manager.cancel_current_tasks(client_id)
                asyncio.create_task(process_audio_segment(raw_data["bytes"]))
            elif "text" in raw_data:
                try:
                    message = json.loads(raw_data["text"])
                    if "audio_segment" in message:
                        await manager.cancel_current_tasks(client_id)
                        asyncio.create_task(process_audio_segment(base64.b64decode(message["audio_segment"])))
                    elif message.get("type") == "image_update":
                        manager.image_manager.save_image(base64.b64decode(message["image"]), client_id, "live_eye")
                        qwen_processor.update_live_vision_context("使用者正站在相機前進行多模態互動對嘴")
                        await websocket.send_text(json.dumps({"type": "status", "message": "AI 眼睛已定時更新鏡頭視野 (Live Eye Sync)"}))
                    elif message.get("type") == "switch_avatar":
                        req_id = message.get("avatarId")
                        if req_id: avatar_mgr.switch_avatar(req_id)
                except Exception: pass
    except WebSocketDisconnect: pass
    finally:
        await manager.cancel_current_tasks(client_id)
        manager.disconnect(client_id)


if os.path.exists(CLIENT_OUT_DIR):
    app.mount("/", StaticFiles(directory=CLIENT_OUT_DIR, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)