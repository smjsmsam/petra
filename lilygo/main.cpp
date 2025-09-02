const char* ssid = "WiFi Name";
const char* password = "WiFi Password";

const char* upload_url = "ws://xxx.xx.xx.xx:xxxx/ws";

#include <WiFi.h>
#include <TFT_eSPI.h>
#include <ArduinoWebsockets.h>

#include "Petra_closed.h"
#include "Petra_open.h"
#include "hello.h"

#include <driver/i2s.h>
#include "driver/dac.h"

#define MIC_BUTTON_PIN 12
#define LED_PIN 2

#define I2S_BCLK_PIN 26
#define I2S_LRCL_PIN 33
#define I2S_DOUT_PIN 22
#define SAMPLE_RATE_MIC 16000
#define SAMPLE_RATE_SPEAKER 8000
#define I2S_NUM_RX I2S_NUM_0
#define I2S_NUM_TX I2S_NUM_1
#define I2S_READ_LEN 512
#define PLAYBACK_CHUNK_SIZE 512

using namespace websockets;

WebsocketsClient client;

bool isRecording = false;
bool micButtonLast = HIGH;
bool speakerButtonLast = HIGH;
bool isPlaying = false;

int16_t i2s_buffer[I2S_READ_LEN];
TFT_eSPI tft = TFT_eSPI();

#define PLAYBACK_THRESHOLD 4096
#define AUDIO_BUFFER_SIZE 32000
int16_t audio_buffer[AUDIO_BUFFER_SIZE];
volatile size_t write_index = 0;
volatile size_t read_index = 0;
static unsigned long lastAudioReceived = 0;


void handleButtons();
void setupI2SMic();
size_t bufferFreeSpace();
void play_hello();


void setup() {
  Serial.begin(9600);

  pinMode(MIC_BUTTON_PIN, INPUT_PULLUP); 
  pinMode(LED_PIN, OUTPUT);

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("Connected to WiFi.");

  client.onMessage([](WebsocketsMessage message) {
    if (message.isBinary()) {
      size_t sample_count = message.length();
      if (bufferFreeSpace() < sample_count) {
        Serial.println("Not enough space in audio buffer!");
        return;
      }

      const uint8_t* data = (const uint8_t*)message.data().c_str();
      for (size_t i = 0; i < sample_count; ++i) {
        audio_buffer[write_index] = data[i];
        write_index = (write_index + 1) % AUDIO_BUFFER_SIZE;
      }

    } else {
      Serial.print("Received text: ");
      Serial.println(message.data());
      tft.fillRect(0, 130, 135, 110, TFT_BLACK);
      tft.pushImage(0, 0, 128, 128, Petra_open);
      tft.setCursor(0, 130);
      tft.print(message.data());
      memset(audio_buffer, 0, sizeof(audio_buffer));
      write_index = 0;
      read_index = 0;
    }

    lastAudioReceived = millis();
  });


  if (client.connect(upload_url)) {
    Serial.println("WebSocket connected.");
  } else {
    Serial.println("WebSocket connection failed.");
  }

  setupI2SMic();
  dac_output_enable(DAC_CHANNEL_1);

  tft.init();
  tft.setRotation(0);
  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(1);
  tft.setTextFont(2);
  tft.setTextWrap(true, false);
  tft.setSwapBytes(true);
  tft.pushImage(0, 0, 128, 128, Petra_closed);
  tft.setCursor(0, 130);
  tft.print("Hi! My name is Petra!");
  play_hello();
  Serial.println("Ready.");
}

void setupI2SMic() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = SAMPLE_RATE_MIC,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 1024,
    .use_apll = false
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_BCLK_PIN,
    .ws_io_num = I2S_LRCL_PIN,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = I2S_DOUT_PIN
  };

  i2s_driver_install(I2S_NUM_RX, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_NUM_RX, &pin_config);
}

unsigned long lastReconnectAttempt = 0;
const unsigned long reconnectInterval = 5000;

void loop() {
  handleButtons();
  // reconnect if needed
  if (!client.available()) {
    unsigned long now = millis();
    if (now - lastReconnectAttempt > reconnectInterval) {
      Serial.println("WebSocket disconnected, attempting reconnect...");
      if (client.connect(upload_url)) {
        Serial.println("Reconnected!");
      } else {
        Serial.println("Reconnect failed.");
      }
      lastReconnectAttempt = now;
    }
    return;
  }

  // send audio to server
  if (isRecording && client.available()) {
    size_t bytes_read = 0;
    i2s_read(I2S_NUM_RX, (void*)i2s_buffer, I2S_READ_LEN * sizeof(int16_t), &bytes_read, portMAX_DELAY);

    if (bytes_read > 0) {
      bool ok = client.sendBinary((const char*)i2s_buffer, bytes_read);
      if (!ok) {
        Serial.println("Failed to send audio chunk");
      }
    }
  }

  // check for server response
  client.poll();

  // play server audio stream
  size_t buffered = (write_index + AUDIO_BUFFER_SIZE - read_index) % AUDIO_BUFFER_SIZE;

  if (!isPlaying && buffered >= PLAYBACK_THRESHOLD) {
    Serial.println("Starting playback...");
    isPlaying = true;
  }

  // play audio
  if (isPlaying && buffered > 0) {
    size_t samples_to_write = buffered < PLAYBACK_CHUNK_SIZE ? buffered : PLAYBACK_CHUNK_SIZE;

    for (size_t i = 0; i < samples_to_write; ++i) {
      int16_t sample = audio_buffer[read_index];
      read_index = (read_index + 1) % AUDIO_BUFFER_SIZE;

      dac_output_voltage(DAC_CHANNEL_1, sample);
      delayMicroseconds(1000000 / SAMPLE_RATE_SPEAKER);
    }
  }

  // audio is finished
  if (isPlaying && write_index == read_index && millis() - lastAudioReceived > 300) {
    Serial.println("Finished playing server audio.");
    isPlaying = false;
    tft.pushImage(0, 0, 128, 128, Petra_closed);
  }
}

size_t bufferFreeSpace() {
  // circular buffer
  if (write_index >= read_index)
    return AUDIO_BUFFER_SIZE - (write_index - read_index);
  else
    return read_index - write_index;
}

void handleButtons() {
  int micButton = digitalRead(MIC_BUTTON_PIN);

  if (micButton == LOW && micButtonLast == HIGH) {
    Serial.println("Mic button pressed");
    isRecording = !isRecording;
    if (isRecording) {
      tft.fillRect(0, 130, 135, 110, TFT_BLACK);
      tft.pushImage(0, 0, 128, 128, Petra_closed);
      tft.setCursor(0, 130);
      tft.print("I'm listening!");
      digitalWrite(LED_PIN, HIGH);
      Serial.println("Recording started");
    } else {
      Serial.println("Recording stopped.");
      tft.setCursor(0, 130);
      tft.print("Hold on! Let me think.");
      digitalWrite(LED_PIN, LOW);      
    }
  }
  
  micButtonLast = micButton;
}

void play_hello() {
  tft.pushImage(0, 0, 128, 128, Petra_open);
  for (unsigned int i = 0; i < hello_raw_len; i++) {
    dac_output_voltage(DAC_CHANNEL_1, hello_raw[i]);
    delayMicroseconds(125);
  }
  tft.pushImage(0, 0, 128, 128, Petra_closed);
}
