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
from typing import Optional, Dict, Any
import uvicorn

# FastAPI imports
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Import Kokoro TTS library
from kokoro import KPipeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

try:
    anext
except NameError:
    async def anext(iterator):
        try:
            return await iterator.__anext__()
        except StopAsyncIteration:
            raise


class ImageManager:
    """Manages image saving and verification"""
    def __init__(self, save_directory="received_images"):
        self.save_directory = Path(save_directory)
        self.save_directory.mkdir(exist_ok=True)
        logger.info(f"Image save directory: {self.save_directory.absolute()}")

    def save_image(self, image_data: bytes, client_id: str, prefix: str = "img") -> str:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename = f"{prefix}_{client_id}_{timestamp}.jpg"
            filepath = self.save_directory / filename
            with open(filepath, "wb") as f:
                f.write(image_data)
            logger.info(f"💾 Saved image: {filename} ({len(image_data):,} bytes)")
            return str(filepath)
        except Exception as e:
            logger.error(f"❌ Error saving image: {e}")
            return None

    def verify_image(self, filepath: str) -> dict:
        try:
            if not os.path.exists(filepath):
                return {"error": "File not found"}
            stat = os.stat(filepath)
            with Image.open(filepath) as img:
                info = {
                    "filepath": filepath,
                    "file_size": stat.st_size,
                    "format": img.format,
                    "mode": img.mode,
                    "size": img.size,
                    "valid": True,
                }
            logger.info(f"✅ Image verified: {info}")
            return info
        except Exception as e:
            logger.error(f"❌ Error verifying image {filepath}: {e}")
            return {"error": str(e), "valid": False}


class WhisperProcessor:
    """Handles speech-to-text using Whisper model on CPU"""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        # 💡 強制鎖定為 CPU 運算，避開顯示卡架構引起的相容性報錯
        self.device = "cpu"
        self.torch_dtype = torch.float32
        logger.info(f"Using device for Whisper: {self.device}")

        model_id = "openai/whisper-tiny"
        logger.info(f"Loading {model_id}...")

        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_id,
            torch_dtype=self.torch_dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
        )
        self.model.to(self.device)
        self.processor = AutoProcessor.from_pretrained(model_id)

        self.pipe = pipeline(
            "automatic-speech-recognition",
            model=self.model,
            tokenizer=self.processor.tokenizer,
            feature_extractor=self.processor.feature_extractor,
            torch_dtype=self.torch_dtype,
            device=self.device,
        )
        logger.info("Whisper model ready for transcription")
        self.transcription_count = 0

    async def transcribe_audio(self, audio_bytes):
        try:
            # 💡 數據歸一化防禦：將 PCM int16 轉成 float32 並進行振幅歸一化
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            
            if len(audio_array) == 0:
                logger.warning("⚠️ Received empty audio array")
                return ""

            inputs = self.processor(audio_array, sampling_rate=16000, return_tensors="pt")
            input_features = inputs.input_features.to(self.device, dtype=torch.float32)
            
            with torch.no_grad():  # 💡 關閉梯度計算，加快 CPU 推理速度
                predicted_ids = self.model.generate(input_features)
                
            transcription = self.processor.batch_decode(predicted_ids, skip_special_tokens=True)[0]
            return transcription.strip()
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return None


# 💡 【進階模組二專用】在後端導入並使用 Qwen2.5 1.5B 滿血智商腦袋
class QwenLLMProcessor:
    """純文本 LLM 大腦處理器 — 點名 Qwen 1.5B 提供極高對話品質"""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.device = "cpu"
        model_id = "Qwen/Qwen2.5-1.5B-Instruct" # 💡 15 億參數地端最強小鋼砲
        logger.info(f"Loading {model_id} on {self.device}...")
        
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True
        ).to(self.device)
        
        logger.info("Qwen2.5-1.5B LLM Engine Ready!")
        self.message_history = []
        self.max_history = 6 # 保留最近 3 次對話歷史

    def generate_chat_stream(self, text: str):
        # 💡 設計「專業外語家教」提示詞，引導學員進行口說練習
        system_instruction = (
            "You are a friendly, highly professional American English tutor named TalkMate. "
            "Your goal is to help the user practice spoken English. "
            "Respond in lively, conversational English. Keep answers under 2 sentences, "
            "and end with an engaging question to keep the conversation going."
        )

        # 彙整對話歷史
        messages = [{"role": "system", "content": system_instruction}]
        for hist in self.message_history:
            messages.append(hist)
        messages.append({"role": "user", "content": text})

        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer([prompt], return_tensors="pt").to(self.device)
        
        streamer = TextIteratorStreamer(self.tokenizer, skip_queue=True, skip_special_tokens=True)
        
        generation_kwargs = dict(
            **inputs,
            do_sample=True,
            temperature=0.7,
            top_p=0.9,
            repetition_penalty=1.15,
            max_new_tokens=100, # CPU 推理 100 個 token 僅需 1~2 秒，反應極快！
            streamer=streamer,
        )

        thread = Thread(target=self.model.generate, kwargs=generation_kwargs)
        thread.start()
        
        return streamer

    def update_history_with_complete_response(self, user_text, initial_response, remaining_text=None):
        complete_response = initial_response
        if remaining_text:
            complete_response = initial_response + remaining_text
        self.message_history.append({"role": "user", "content": user_text})
        self.message_history.append({"role": "assistant", "content": complete_response})
        if len(self.message_history) > self.max_history:
            self.message_history = self.message_history[-self.max_history:]


class KokoroTTSProcessor:
    """Handles text-to-speech conversion using Kokoro model on CPU"""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        logger.info("Initializing Kokoro TTS processor on CPU...")
        try:
            # 💡 終極修復：明確指定 device='cpu'，防止內建邏輯呼叫不位相容的 CUDA 核心影像
            self.pipeline = KPipeline(lang_code="a", device="cpu")
            self.default_voice = "af_sarah"
            logger.info("Kokoro TTS processor initialized successfully on CPU")
            self.synthesis_count = 0
        except Exception as e:
            logger.error(f"Error initializing Kokoro TTS: {e}")
            self.pipeline = None

    async def synthesize_initial_speech_with_timing(self, text):
        if not text or not self.pipeline:
            return None, []
        try:
            logger.info(f"Synthesizing initial speech for text: '{text}'")
            audio_segments, all_word_timings, time_offset = [], [], 0

            generator = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.pipeline(text, voice=self.default_voice, speed=1, split_pattern=None)
            )

            for i, result in enumerate(generator):
                audio = result.audio.cpu().numpy()
                tokens = result.tokens
                logger.info(f"Segment {i}: {len(tokens)} tokens, audio shape: {audio.shape}")

                for token in tokens:
                    if token.start_ts is not None and token.end_ts is not None:
                        word_timing = {
                            "word": token.text,
                            "start_time": (token.start_ts + time_offset) * 1000,
                            "end_time": (token.end_ts + time_offset) * 1000,
                        }
                        all_word_timings.append(word_timing)

                if len(audio) > 0:
                    time_offset += len(audio) / 24000
                audio_segments.append(audio)

            if audio_segments:
                return np.concatenate(audio_segments), all_word_timings
            return None, []
        except Exception as e:
            logger.error(f"Initial speech synthesis with timing error: {e}")
            return None, []

    async def synthesize_remaining_speech_with_timing(self, text):
        if not text or not self.pipeline:
            return None, []
        try:
            logger.info(f"Synthesizing chunk speech for text: '{text[:50]}'")
            audio_segments, all_word_timings, time_offset = [], [], 0
            split_pattern = None if len(text) < 100 else r"[.!?。！？]+"

            generator = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.pipeline(text, voice=self.default_voice, speed=1, split_pattern=split_pattern)
            )

            for i, result in enumerate(generator):
                audio = result.audio.cpu().numpy()
                tokens = result.tokens
                for token in tokens:
                    if token.start_ts is not None and token.end_ts is not None:
                        word_timing = {
                            "word": token.text,
                            "start_time": (token.start_ts + time_offset) * 1000,
                            "end_time": (token.end_ts + time_offset) * 1000,
                        }
                        all_word_timings.append(word_timing)
                if len(audio) > 0:
                    time_offset += len(audio) / 24000
                audio_segments.append(audio)

            if audio_segments:
                return np.concatenate(audio_segments), all_word_timings
            return None, []
        except Exception as e:
            logger.error(f"Chunk speech synthesis with timing error: {e}")
            return None, []


async def collect_remaining_text(streamer, chunk_size=80):
    current_chunk = ""
    if streamer:
        try:
            for chunk in streamer:
                current_chunk += chunk
                if len(current_chunk) >= chunk_size and (
                    current_chunk.endswith(".") or current_chunk.endswith("!") or current_chunk.endswith("?") or "." in current_chunk[-15:]
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
    """Manages WebSocket connections and maps active pipeline status"""
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.current_tasks: Dict[str, Dict[str, asyncio.Task]] = {}
        self.image_manager = ImageManager()
        self.stats = {
            "audio_segments_received": 0,
            "images_received": 0,
            "audio_with_image_received": 0,
            "last_reset": datetime.now(),
        }

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        self.current_tasks[client_id] = {"processing": None, "tts": None}
        logger.info(f"Client {client_id} connected and mapped in ConnectionManager")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        if client_id in self.current_tasks:
            del self.current_tasks[client_id]
        logger.info(f"Client {client_id} completely disconnected from Manager")

    async def cancel_current_tasks(self, client_id: str):
        if client_id in self.current_tasks:
            tasks = self.current_tasks[client_id]
            if tasks["processing"] and not tasks["processing"].done():
                tasks["processing"].cancel()
            if tasks["tts"] and not tasks["tts"].done():
                tasks["tts"].cancel()
            self.current_tasks[client_id] = {"processing": None, "tts": None}

    def set_task(self, client_id: str, task_type: str, task: asyncio.Task):
        if client_id in self.current_tasks:
            self.current_tasks[client_id][task_type] = task

    def update_stats(self, event_type: str):
        if event_type in self.stats:
            self.stats[event_type] += 1

    def get_stats(self) -> dict:
        uptime = datetime.now() - self.stats["last_reset"]
        return {**self.stats, "uptime_seconds": uptime.total_seconds(), "active_connections": len(self.active_connections)}


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing models on startup...")
    try:
        WhisperProcessor.get_instance()
        QwenLLMProcessor.get_instance()
        KokoroTTSProcessor.get_instance()
        logger.info("All models initialized successfully on CPU")
    except Exception as e:
        logger.error(f"Error initializing models: {e}")
        raise
    yield
    logger.info("Shutting down server...")
    for client_id in list(manager.active_connections.keys()):
        try:
            await manager.active_connections[client_id].close()
        except Exception:
            pass
        manager.disconnect(client_id)


app = FastAPI(title="Whisper + Qwen2.5 Voice Assistant", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/stats")
async def get_stats():
    return manager.get_stats()


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)

    whisper_processor = WhisperProcessor.get_instance()
    qwen_processor = QwenLLMProcessor.get_instance()
    tts_processor = KokoroTTSProcessor.get_instance()

    try:
        # 💡 通訊防禦對策：發送前端框架期待的初始化 JSON 認證包，缺少此段前端會因超時自斷連線
        await websocket.send_text(
            json.dumps({"status": "connected", "client_id": client_id})
        )

        async def send_keepalive():
            """💡 關鍵心跳機制：每 10 秒發送 Ping 包，防止 Windows 網路層過期自斷連線"""
            while True:
                try:
                    await websocket.send_text(
                        json.dumps({"type": "ping", "timestamp": time.time()})
                    )
                    await asyncio.sleep(10)
                except Exception:
                    break

        async def process_audio_segment(audio_data, image_data=None):
            try:
                if image_data:
                    logger.info("🎥 Processing audio+image segment")
                    manager.update_stats("audio_with_image_received")
                    saved_path = manager.image_manager.save_image(image_data, client_id, "multimodal")
                    if saved_path:
                        manager.image_manager.verify_image(saved_path)
                else:
                    logger.info("🎤 Processing audio-only segment")
                    manager.update_stats("audio_segments_received")

                await websocket.send_text(json.dumps({"interrupt": True}))

                logger.info("Starting Whisper transcription")
                transcribed_text = await whisper_processor.transcribe_audio(audio_data)

                if transcribed_text in ["NOISE_DETECTED", "NO_SPEECH", None]:
                    return

                if image_data:
                    logger.warning("⚠️ Image received but Qwen2.5 is a pure text LLM and does not support image analysis.")

                logger.info("Starting Qwen2.5-1.5B generation")
                streamer = qwen_processor.generate_chat_stream(transcribed_text)

                def get_initial_text(st):
                    initial_txt = ""
                    min_chars = 50
                    sentence_end_pattern = re.compile(r"[.!?。！？]")
                    has_sentence_end = False
                    initial_stopped_early = False

                    for chunk in st:
                        initial_txt += chunk
                        logger.info(f"Streaming chunk: '{chunk}'")

                        if sentence_end_pattern.search(chunk):
                            has_sentence_end = True
                            if len(initial_txt) >= min_chars / 2:
                                initial_stopped_early = True
                                break
                        if len(initial_txt) >= min_chars and (has_sentence_end or "," in initial_txt):
                            initial_stopped_early = True
                            break
                        if len(initial_txt) >= min_chars * 2:
                            initial_stopped_early = True
                            break
                    return initial_txt, initial_stopped_early

                initial_text, initial_collection_stopped_early = await asyncio.get_event_loop().run_in_executor(
                    None, get_initial_text, streamer
                )

                if initial_text.startswith("NOISE:"):
                    return

                if initial_text:
                    logger.info("Starting TTS for initial text")
                    tts_task = asyncio.create_task(
                        tts_processor.synthesize_initial_speech_with_timing(initial_text)
                    )
                    manager.set_task(client_id, "tts", tts_task)

                    initial_audio, initial_timings = await tts_task

                    if initial_audio is not None and len(initial_audio) > 0:
                        audio_bytes = (initial_audio * 32767).astype(np.int16).tobytes()
                        base64_audio = base64.b64encode(audio_bytes).decode("utf-8")

                        # 💡 前端播放器組件預期的完整 JSON 回傳格式 (含 word_timings 嘴型時間戳)
                        audio_message = {
                            "audio": base64_audio,
                            "word_timings": initial_timings,
                            "sample_rate": 24000,
                            "method": "native_kokoro_timing",
                            "modality": "multimodal" if image_data else "audio_only",
                        }
                        await websocket.send_text(json.dumps(audio_message))

                        if initial_collection_stopped_early:
                            collected_chunks = []
                            try:
                                text_iterator = collect_remaining_text(streamer)
                                while True:
                                    try:
                                        text_chunk = await anext(text_iterator)
                                        collected_chunks.append(text_chunk)

                                        chunk_tts_task = asyncio.create_task(
                                            tts_processor.synthesize_remaining_speech_with_timing(text_chunk)
                                        )
                                        manager.set_task(client_id, "tts", chunk_tts_task)
                                        chunk_audio, chunk_timings = await chunk_tts_task

                                        if chunk_audio is not None and len(chunk_audio) > 0:
                                            chunk_bytes = (chunk_audio * 32767).astype(np.int16).tobytes()
                                            base64_chunk = base64.b64encode(chunk_bytes).decode("utf-8")

                                            chunk_message = {
                                                "audio": base64_chunk,
                                                "word_timings": chunk_timings,
                                                "sample_rate": 24000,
                                                "method": "native_kokoro_timing",
                                                "chunk": True,
                                                "modality": "multimodal" if image_data else "audio_only",
                                            }
                                            await websocket.send_text(json.dumps(chunk_message))
                                    except StopAsyncIteration:
                                        break
                                if collected_chunks:
                                    qwen_processor.update_history_with_complete_response(
                                        transcribed_text, initial_text, "".join(collected_chunks)
                                    )
                            except asyncio.CancelledError:
                                if collected_chunks:
                                    qwen_processor.update_history_with_complete_response(
                                        transcribed_text, initial_text, "".join(collected_chunks)
                                    )
                                return
                        else:
                            qwen_processor.update_history_with_complete_response(transcribed_text, initial_text)

                        await websocket.send_text(json.dumps({"audio_complete": True}))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error processing segment: {e}")

        async def receive_and_process():
            """💡 多重資料攔截防漏機制：同時相容原始位元組 (Raw Bytes) 與標準封裝的 JSON 欄位字串"""
            try:
                while True:
                    raw_data = await websocket.receive()
                    
                    # 情況 A：收到二進位原始位元組流
                    if "bytes" in raw_data and raw_data["bytes"] is not None:
                        await manager.cancel_current_tasks(client_id)
                        processing_task = asyncio.create_task(process_audio_segment(raw_data["bytes"], None))
                        manager.set_task(client_id, "processing", processing_task)
                        continue

                    # 情況 B：收到標準 JSON 字串包
                    if "text" in raw_data:
                        try:
                            message = json.loads(raw_data["text"])
                            
                            if "audio_segment" in message:
                                await manager.cancel_current_tasks(client_id)
                                audio_data = base64.b64decode(message["audio_segment"])
                                image_data = base64.b64decode(message["image"]) if "image" in message else None
                                processing_task = asyncio.create_task(process_audio_segment(audio_data, image_data))
                                manager.set_task(client_id, "processing", processing_task)

                            elif "image" in message:
                                if not (client_id in manager.current_tasks and manager.current_tasks[client_id]["processing"] and not manager.current_tasks[client_id]["processing"].done()):
                                    image_data = base64.b64decode(message["image"])
                                    manager.image_manager.save_image(image_data, client_id, "standalone")
                        except json.JSONDecodeError:
                            pass

            except WebSocketDisconnect:
                logger.info("WebSocket loop disconnect caught.")

        receive_task = asyncio.create_task(receive_and_process())
        keepalive_task = asyncio.create_task(send_keepalive())

        done, pending = await asyncio.wait([receive_task, keepalive_task], return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()

    except WebSocketDisconnect:
        pass
    finally:
        await manager.cancel_current_tasks(client_id)
        manager.disconnect(client_id)


def main():
    config = uvicorn.Config(app=app, host="127.0.0.1", port=8000, log_level="info")
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
