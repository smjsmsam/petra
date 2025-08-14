from fastapi import FastAPI, WebSocket
import numpy as np
from TTS.api import TTS
import whisper
import uvicorn
import asyncio
import logging

SAMPLE_RATE = 16000
app = FastAPI()
logger = logging.getLogger(__name__)

tts = TTS("tts_models/en/vctk/vits")
stt_model = whisper.load_model("base")

@app.websocket("/ws")
async def audio_processor(websocket: WebSocket):
    await websocket.accept()
    logger.info("Client connected")
    buffer = np.array([], dtype=np.float32)
    
    try:
        while True:
            try:
                audio_bytes = await asyncio.wait_for(
                    websocket.receive_bytes(),
                    timeout=30.0
                )
                
                # audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
                # if len(audio) < 1600:  # Minimum 100ms
                #     continue

                audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
                buffer = np.concatenate([buffer, audio])

                if len(buffer) > SAMPLE_RATE * 3:
                    result = stt_model.transcribe(buffer, fp16=False, language='English')
                    text = result["text"].strip()
                    buffer = np.array([], dtype=np.float32)
                
                    if text:
                        logger.info(f"Transcribed: {text}")
                        tts_output = np.array(tts.tts(text=f"AI says: {text}", speaker="p339", speaker_wav=None))
                        await websocket.send_bytes((tts_output * 32767).astype(np.int16).tobytes())
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Processing error: {e}")
                break
                
    finally:
        try:
            await websocket.close()
        except:
            pass
        logger.info("Connection closed")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)