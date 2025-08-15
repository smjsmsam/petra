import websockets
import asyncio
import sounddevice as sd
import numpy as np
import logging
import time
from threading import Thread, Event
from pynput.keyboard import Key, Listener

SAMPLE_RATE = 22050
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
        self.is_playing = False
        self.is_recording = False
        self.audio_buffer = []
        self.keyboard_listener = None


    def on_press(self, key):
        if key == Key.space and not self.is_recording:
            asyncio.run_coroutine_threadsafe(self.start_recording(), self.loop)


    def on_release(self, key):
        if key == Key.space and self.is_recording:
            asyncio.run_coroutine_threadsafe(self.stop_recording(), self.loop)


    async def start_recording(self):
        self.is_recording = True
        self.audio_buffer = []
        logger.info("Recording STARTED")


    async def stop_recording(self):
        self.is_recording = False
        logger.info("Recording STOPPED")
        if self.audio_buffer:
            combined_audio = np.concatenate(self.audio_buffer)
            audio_bytes = (combined_audio * 32767).astype(np.int16).tobytes()
            await self.audio_queue.put(audio_bytes)
            self.audio_buffer = []


    async def audio_producer(self):
        def callback(indata, frames, time, status):
            if status:
                logger.warning(f"Audio status: {status}")
            if self.is_recording and not self.is_playing:
                self.audio_buffer.append(indata.copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype=np.float32,
            blocksize=CHUNK_SIZE,
            callback=callback
        ):
            self.keyboard_listener = Listener(on_press=self.on_press, on_release=self.on_release)
            self.keyboard_listener.start()

            while self.running:
                await asyncio.sleep(0.05)


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

                    self.producer_task = asyncio.create_task(self.audio_producer())

                    while self.running:
                        
                        if not self.is_recording:
                            try:
                                audio_chunk = await asyncio.wait_for(self.audio_queue.get(), timeout=0.1)
                                await ws.send(audio_chunk)
                                logger.info("Audio chunk sent to server")
                            except asyncio.TimeoutError:
                                pass

                        try:
                            response = await asyncio.wait_for(ws.recv(), timeout=0.1)
                            if isinstance(response, bytes):
                                logger.info("Received TTS audio")
                                self.is_playing = True
                                audio = np.frombuffer(response, dtype=np.int16)
                                sd.play(audio.astype(np.float32) / 32767.0, SAMPLE_RATE)
                                sd.wait()
                                self.is_playing = False
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
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        if self.loop:
            self.loop.stop()
            self.loop.close()
        logger.info("Client stopped")


if __name__ == "__main__":
    client = AudioClient()
    client_thread = Thread(target=client.run)
    client_thread.start()

    try:
        print("Hold SPACEBAR to record, release to send")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        client.cleanup()
        client_thread.join()
