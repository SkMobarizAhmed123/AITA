# voice_utils.py
import tempfile
import os
import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write as write_wav
import whisper
import pyttsx3


class VoiceAssistant:
    def __init__(self, whisper_model="base", sample_rate=16000, voice_rate=185, voice_index=None):
        print(f"Loading Whisper model '{whisper_model}' (first run downloads it)...")
        self.model = whisper.load_model(whisper_model)
        self.sample_rate = sample_rate

        self.tts_engine = pyttsx3.init()
        self.tts_engine.setProperty("rate", voice_rate)
        voices = self.tts_engine.getProperty("voices")
        if voice_index is not None and 0 <= voice_index < len(voices):
            self.tts_engine.setProperty("voice", voices[voice_index].id)

    def list_voices(self):
        voices = self.tts_engine.getProperty("voices")
        for i, v in enumerate(voices):
            print(f"[{i}] {v.name} ({v.languages if v.languages else v.id})")

    def record(self, duration=5) -> str:
        print(f"🎤 Listening for {duration} seconds...")
        audio = sd.rec(
            int(duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
        )
        sd.wait()
        audio_int16 = np.int16(audio * 32767)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_path = tf.name
        write_wav(tmp_path, self.sample_rate, audio_int16)
        return tmp_path

    def transcribe(self, wav_path: str) -> str:
        result = self.model.transcribe(wav_path, fp16=False)
        text = result.get("text", "").strip()
        os.remove(wav_path)
        if text:
            print(f"🗣 You said: {text}")
        else:
            print("🤷 Could not understand audio.")
        return text

    def listen(self, duration=5) -> str:
        wav_path = self.record(duration=duration)
        return self.transcribe(wav_path)

    def speak(self, text: str):
        print(f"🔊 Assistant: {text}")
        self.tts_engine.say(text)
        self.tts_engine.runAndWait()


_va_instance = None


def get_voice_assistant():
    global _va_instance
    if _va_instance is None:
        try:
            _va_instance = VoiceAssistant()
        except Exception as e:
            print(f"⚠️ Could not initialize VoiceAssistant: {e}")
            return None
    return _va_instance