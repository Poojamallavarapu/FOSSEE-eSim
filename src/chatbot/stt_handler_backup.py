import os
from faster_whisper import WhisperModel

# 🔥 Use better model (you can change to small if slow)
model = WhisperModel("small")

def listen_to_mic(*args, **kwargs):
    print("🟢 Listening... Speak freely (auto stop after silence)")

    # 🎤 Record longer audio (10 sec max)
    os.system("arecord -r 16000 -c 1 -f S16_LE -d 7 input.wav > /dev/null 2>&1")

    print("🧠 Transcribing full sentence...")

    segments, _ = model.transcribe("input.wav")

    text = ""
    for segment in segments:
        text += segment.text + " "

    text = text.strip()

    print("✅ You said:", text)

    return text if text else ""
