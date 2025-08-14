import websockets
import asyncio
import sounddevice as sd
import numpy as np
import logging
import time
from threading import Thread, Event

SAMPLE_RATE = 16000
CHUNK_SIZE = 2048
WS_URI = "ws://localhost:8000/ws"
RECONNECT_DELAY = 3
PING_INTERVAL = 20
PING_TIMEOUT = 30

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioClient:
    def __init__(self):
        self.running = False
        self.connection_event = Event()
        self.loop = None
        self.audio_queue = asyncio.Queue()
        self.last_ping = time.time()

    async def audio_producer(self):
        def callback(indata, frames, time, status):
            if status:
                logger.warning(f"Audio status: {status}")
            audio_bytes = (indata * 32767).astype(np.int16).tobytes()
            asyncio.run_coroutine_threadsafe(
                self.audio_queue.put(audio_bytes),
                self.loop
            )

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype=np.float32,
            blocksize=CHUNK_SIZE,
            callback=callback
        ):
            while self.running:
                await asyncio.sleep(0.1)

    async def connection_handler(self):
        while self.running:
            try:
                async with websockets.connect(
                    WS_URI,
                    ping_interval=PING_INTERVAL,
                    ping_timeout=PING_TIMEOUT
                ) as ws:
                    logger.info("Connected to server")
                    self.connection_event.set()

                    if not hasattr(self, "producer_task") or self.producer_task.done():
                        self.producer_task = asyncio.create_task(self.audio_producer())

                    while self.running:
                        try:
                            audio_chunk = await asyncio.wait_for(
                                self.audio_queue.get(),
                                timeout=0.1
                            )
                            await ws.send(audio_chunk)
                        except asyncio.TimeoutError:
                            pass

                        try:
                            response = await asyncio.wait_for(ws.recv(), timeout=0.1)
                            if isinstance(response, bytes):
                                audio = np.frombuffer(response, dtype=np.int16)
                                sd.play(audio.astype(np.float32) / 32767.0, SAMPLE_RATE)
                        except asyncio.TimeoutError:
                            pass

            except Exception as e:
                logger.error(f"Connection error: {e}")
                self.connection_event.clear()
                if self.running:
                    await asyncio.sleep(RECONNECT_DELAY)


    def run(self):
        self.running = True
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        try:
            self.loop.run_until_complete(self.connection_handler())
        except Exception as e:
            logger.error(f"Runtime error: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        self.running = False
        self.connection_event.set()
        if self.loop:
            self.loop.close()
        logger.info("Client stopped")

if __name__ == "__main__":
    client = AudioClient()
    client_thread = Thread(target=client.run)
    client_thread.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        client.cleanup()
        client_thread.join()