"""
stt_handler.py  –  Speech-to-Text using PyAudio + Google Speech Recognition
-----------------------------------------------------------------------------
Drop-in replacement for the original stt_handler.py.
Exposes the same public function:

    listen_to_mic(should_stop=None, max_silence_sec=3) -> str

Requirements:
    pip install SpeechRecognition pyaudio

On Linux you may also need:
    sudo apt-get install portaudio19-dev python3-pyaudio
"""

import speech_recognition as sr


def listen_to_mic(should_stop=None, max_silence_sec: int = 3) -> str:
    """
    Listen to the microphone and return the transcribed text.

    Parameters
    ----------
    should_stop : callable, optional
        A zero-argument callable that returns True when the caller wants
        the recording to stop early (used by MicWorker).
    max_silence_sec : int
        Seconds of silence after which recording stops automatically.
        Increase this for longer sentences (default 3 → now 5 inside).

    Returns
    -------
    str
        Transcribed text, or empty string if nothing was recognised.
    """

    recognizer = sr.Recognizer()

    # ── Tuning knobs ──────────────────────────────────────────────────────────
    # How long (seconds) to wait for speech to START before giving up
    PHRASE_TIME_LIMIT   = 30          # max recording length per phrase (seconds)
    PAUSE_THRESHOLD     = max(max_silence_sec, 2.0)   # silence → end of phrase
    ENERGY_THRESHOLD    = 300         # lower = more sensitive mic
    DYNAMIC_ENERGY      = True        # auto-adjust to background noise
    # ─────────────────────────────────────────────────────────────────────────

    recognizer.pause_threshold     = PAUSE_THRESHOLD
    recognizer.energy_threshold    = ENERGY_THRESHOLD
    recognizer.dynamic_energy_threshold = DYNAMIC_ENERGY

    try:
        with sr.Microphone() as source:
            print("[STT] Adjusting for ambient noise…")
            # Short ambient-noise calibration (1 second)
            recognizer.adjust_for_ambient_noise(source, duration=1)

            print("[STT] Listening…")
            # listen() blocks until silence is detected or should_stop fires
            audio = recognizer.listen(
                source,
                timeout=10,                  # wait up to 10 s for speech to start
                phrase_time_limit=PHRASE_TIME_LIMIT,
            )

        # ── Check stop signal AFTER audio is captured ─────────────────────
        if should_stop and should_stop():
            print("[STT] Stop requested – discarding audio.")
            return ""

        print("[STT] Sending audio to Google Speech Recognition…")
        text = recognizer.recognize_google(audio, language="en-IN")
        print(f"[STT] Recognised: {text}")
        return text

    except sr.WaitTimeoutError:
        # No speech detected within timeout
        print("[STT] No speech detected (timeout).")
        return ""

    except sr.UnknownValueError:
        # Audio was captured but could not be understood
        print("[STT] Could not understand audio.")
        return ""

    except sr.RequestError as e:
        # Network / API error
        print(f"[STT] Google API error: {e}")
        raise RuntimeError(f"Google Speech API error: {e}")

    except OSError as e:
        # Microphone not available / PortAudio error
        print(f"[STT] Microphone error: {e}")
        raise RuntimeError(f"Microphone not available: {e}")
