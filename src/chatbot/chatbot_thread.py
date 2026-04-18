import os
import re
import io
import socket
import subprocess
import time
import tempfile
import ollama
from PyQt5.QtCore import QThread, pyqtSignal

# ── Optional imports — checked at runtime, never crash on missing ─────────────

try:
    import speech_recognition as sr
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False

try:
    import vosk
    import json as _json
    _VOSK_AVAILABLE = True
except ImportError:
    _VOSK_AVAILABLE = False


# ── Offline STT engine singleton (loaded once, reused) ───────────────────────
# faster-whisper "tiny" model — ~75 MB download, runs on CPU, very fast
_whisper_model = None

def _get_whisper_model():
    """Load the faster-whisper tiny model once and reuse it."""
    global _whisper_model
    if _whisper_model is None:
        # 'tiny' = ~75 MB, fast on CPU.  Use 'base' (~150 MB) for better accuracy.
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper_model


def _check_internet(host="8.8.8.8", port=53, timeout=2):
    """Quick check for internet connectivity."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except Exception:
        return False


def get_stt_backend() -> str:
    """
    Returns which STT backend is available.
    Priority: faster-whisper (offline) → vosk (offline) → google (online) → none
    """
    if _WHISPER_AVAILABLE:
        return "whisper"
    if _VOSK_AVAILABLE:
        return "vosk"
    if _SR_AVAILABLE:
        return "google"
    return "none"


# ── Ollama health helpers ─────────────────────────────────────────────────────

def is_ollama_running():
    """Check if Ollama is listening on port 11434."""
    try:
        sock = socket.create_connection(("localhost", 11434), timeout=0.5)
        sock.close()
        return True
    except Exception:
        return False


def start_ollama():
    """Start ollama serve in a new terminal window."""
    if os.name == 'nt':
        subprocess.Popen('start cmd /k "ollama serve"', shell=True)
    else:
        subprocess.Popen(
            ['bash', '-c',
             'x-terminal-emulator -e "ollama serve" || '
             'gnome-terminal -- ollama serve || '
             'xterm -e "ollama serve"']
        )
    for _ in range(30):
        time.sleep(1)
        if is_ollama_running():
            return True
    return False


# ── Semantic topic-switch detector ───────────────────────────────────────────

_STOP_WORDS = {
    'a', 'an', 'the', 'is', 'in', 'it', 'of', 'to', 'do', 'i',
    'me', 'my', 'we', 'on', 'at', 'by', 'be', 'as', 'or', 'if',
    'how', 'what', 'why', 'can', 'for', 'with', 'this', 'that',
    'are', 'was', 'and', 'you', 'he', 'she', 'they', 'have', 'has',
}
_TOPIC_SWITCH_THRESHOLD = 0.15


def _word_set(text: str) -> set:
    words = re.findall(r'[a-z]+', text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 2}


def detect_topic_switch(prev_text: str, curr_text: str) -> bool:
    if not prev_text:
        return False
    a, b = _word_set(prev_text), _word_set(curr_text)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) < _TOPIC_SWITCH_THRESHOLD


# ── Background workers ────────────────────────────────────────────────────────

class OllamaStatusWorker(QThread):
    """Background Ollama health check — never blocks UI."""
    result_signal = pyqtSignal(bool)

    def run(self):
        self.result_signal.emit(is_ollama_running())


class ModelFetchWorker(QThread):
    """Fetches ollama model list in background."""
    result_signal = pyqtSignal(list)

    def run(self):
        try:
            models_data = ollama.list()
            raw = (models_data.get('models', [])
                   if isinstance(models_data, dict)
                   else getattr(models_data, 'models', []))
            names = []
            for m in raw:
                name = (m.get('name') or m.get('model', '')
                        if isinstance(m, dict)
                        else getattr(m, 'model', str(m)))
                if name:
                    names.append(name)
            self.result_signal.emit(names if names else ['qwen2.5-coder:3b'])
        except Exception:
            self.result_signal.emit(['qwen2.5-coder:3b'])


# ── eSim-specific system prompt ──────────────────────────────────────────────
_SYSTEM_PROMPT = """You are an expert electronics engineer and the AI assistant embedded inside eSim, an open-source EDA tool developed by FOSSEE at IIT Bombay.

Your domain expertise covers:
- KiCad schematic design (components, footprints, ERC errors, netlist export)
- NgSpice SPICE simulation (transient .tran, AC .ac, DC .dc, noise .noise analyses)
- SPICE netlist syntax: R resistors, C capacitors, L inductors, V/I sources,
  D diodes, Q BJTs, M MOSFETs, X subcircuits, .model, .subckt, .include, .param
- Common NgSpice errors: convergence failures, undefined nodes, singular matrix,
  timestep too small, missing models
- Circuit debugging: reading simulation output, interpreting plots, fixing netlists
- eSim-specific workflow: KiCad → KiCad-to-NgSpice conversion → simulation → plotting

Response guidelines:
- Match response length to the question complexity. Simple questions get 1-3 sentences.
  Debugging questions, netlist analysis, and design explanations deserve full detail.
- When showing SPICE syntax or code, always use a code block with the spice language tag.
- If you identify an error, always explain WHY it happens and HOW to fix it.
- Be direct and practical — users are engineers, not students.
- Do not pad responses or repeat yourself."""


class OllamaWorker(QThread):
    """
    Runs Ollama chat in a background thread with streaming.
    Fully offline — no internet needed once model is downloaded.

    Parameters
    ----------
    chat_history : list[str]
        Lines in "User: …" / "Bot: …" format.
    model : str
        Ollama model name.
    temperature : float
        0.0 = deterministic, 1.0 = creative. Default 0.35 for technical accuracy.
    num_predict : int
        Maximum tokens to generate. -1 = model default (unlimited).
    top_p : float
        Nucleus sampling. 0.9 is a good balance.
    """
    response_signal = pyqtSignal(str)
    status_signal   = pyqtSignal(str)

    def __init__(self, chat_history, model="qwen2.5-coder:3b",
                 temperature=0.35, num_predict=1024, top_p=0.9):
        super().__init__()
        self.chat_history    = chat_history
        self.model           = model
        self.temperature     = temperature
        self.num_predict     = num_predict
        self.top_p           = top_p
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            if not is_ollama_running():
                self.status_signal.emit("Starting Ollama server — please wait…")
                started = start_ollama()
                if not started:
                    self.response_signal.emit(
                        "❌ Could not start Ollama automatically.\n"
                        "Please open a terminal and run: ollama serve"
                    )
                    return
                self.status_signal.emit("Ollama started! Getting response…")
                time.sleep(1)

            # Build message list — keep up to last 20 history entries
            messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
            for line in self.chat_history[-20:]:
                if line.startswith("User:"):
                    messages.append({"role": "user",      "content": line[5:].strip()})
                elif line.startswith("Bot:"):
                    messages.append({"role": "assistant", "content": line[4:].strip()})

            stream = ollama.chat(
                model=self.model,
                messages=messages,
                stream=True,
                options={
                    "temperature": self.temperature,
                    "num_predict": self.num_predict,
                    "top_p":       self.top_p,
                    # Penalise repeating the same phrases
                    "repeat_penalty": 1.1,
                    # Keep context window — important for long debugging sessions
                    "num_ctx": 4096,
                }
            )
            bot_response = ""
            for chunk in stream:
                if self._stop_requested:
                    bot_response += "\n\n⏹ *Generation stopped.*"
                    break
                bot_response += chunk['message']['content']

            bot_response = bot_response.strip()
            if not bot_response:
                bot_response = (
                    "⚠️ Received an empty response. "
                    "The model may still be loading — please try again."
                )
        except Exception as e:
            bot_response = (
                f"❌ Error: {str(e)}\n"
                "Make sure Ollama is installed and 'ollama serve' is running."
            )
        self.response_signal.emit(bot_response)


class OllamaVisionWorker(QThread):
    """
    Sends one or more schematic images to a vision model (llava) — fully offline.
    Accepts image_paths as a list of file paths (supports multiple images).
    For backward compatibility also accepts image_path (single string).
    """
    response_signal = pyqtSignal(str)
    status_signal   = pyqtSignal(str)

    def __init__(self, image_paths=None, extra_prompt: str = "",
                 model: str = "llava", image_path: str = ""):
        super().__init__()
        # Support both single path (old API) and list of paths (new API)
        if image_paths:
            self.image_paths = image_paths if isinstance(image_paths, list) else [image_paths]
        elif image_path:
            self.image_paths = [image_path]
        else:
            self.image_paths = []
        self.extra_prompt    = extra_prompt
        self.model           = model
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            if not is_ollama_running():
                self.status_signal.emit("Starting Ollama server — please wait…")
                started = start_ollama()
                if not started:
                    self.response_signal.emit(
                        "❌ Could not start Ollama automatically.\n"
                        "Please open a terminal and run: ollama serve"
                    )
                    return
                self.status_signal.emit("Ollama started!")
                time.sleep(1)

            if not self.image_paths:
                self.response_signal.emit("❌ No image paths provided.")
                return

            # Load all images and check they exist
            image_bytes_list = []
            for path in self.image_paths:
                if not os.path.exists(path):
                    self.response_signal.emit(f"❌ Image file not found: {path}")
                    return
                with open(path, "rb") as f:
                    image_bytes_list.append(f.read())

            n = len(image_bytes_list)
            img_word = "image" if n == 1 else f"{n} images"
            self.status_signal.emit(f"Analysing {img_word}…")

            # Build prompt — adapt based on image count
            if n == 1:
                prompt = (
                    "You are an expert electronics engineer. "
                    "Analyse this circuit schematic image carefully.\n"
                    "Identify: (1) circuit topology, (2) all components and their "
                    "approximate values if visible, (3) what the circuit does, "
                    "(4) any potential design issues.\nBe concise and structured."
                )
            else:
                prompt = (
                    f"You are an expert electronics engineer. "
                    f"I am sending you {n} schematic images. "
                    f"Analyse each one in sequence.\n"
                    f"For each image identify: circuit topology, components, "
                    f"what the circuit does, and any design issues.\n"
                    f"Then compare the images if they appear to be related circuits."
                )

            if self.extra_prompt.strip():
                prompt += f"\n\nUser question: {self.extra_prompt.strip()}"

            stream = ollama.chat(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": image_bytes_list,   # pass all images in one message
                }],
                stream=True,
            )
            response = ""
            for chunk in stream:
                if self._stop_requested:
                    response += "\n\n⏹ *Generation stopped.*"
                    break
                response += chunk['message']['content']

            response = response.strip()
            if not response:
                response = "⚠️ Vision model returned an empty response. Try again."

        except Exception as e:
            err = str(e)
            if "model" in err.lower() and ("not found" in err.lower() or "pull" in err.lower()):
                response = (
                    "❌ Vision model not found.\n"
                    "Run this in a terminal to install it:\n"
                    "```bash\nollama pull llava\n```"
                )
            else:
                response = (
                    f"❌ Vision error: {err}\n"
                    "Make sure a vision model like `llava` is installed."
                )
        self.response_signal.emit(response)


class MicWorker(QThread):
    """
    Records audio from the microphone and converts speech to text.

    STT backend priority (fully automatic):
      1. faster-whisper  — runs Whisper locally, 100% offline (recommended)
      2. vosk            — lightweight offline model
      3. Google STT      — online fallback (requires internet)

    Install offline backend (one-time, ~75 MB download):
        pip install faster-whisper

    Signals:
        text_signal(str)   — recognised text ready to send
        error_signal(str)  — human-readable error
        status_signal(str) — intermediate status updates
    """
    text_signal   = pyqtSignal(str)
    error_signal  = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    def run(self):
        backend = get_stt_backend()

        if backend == "none":
            self.error_signal.emit(
                "No speech recognition library found.\n"
                "Install offline STT with:\n"
                "  pip install faster-whisper\n\n"
                "Or basic online STT with:\n"
                "  pip install SpeechRecognition pyaudio"
            )
            return

        # ── Record audio (common to all backends) ─────────────────────
        if not _SR_AVAILABLE:
            self.error_signal.emit(
                "SpeechRecognition library not installed.\n"
                "Run:  pip install SpeechRecognition pyaudio"
            )
            return

        try:
            r = sr.Recognizer()
            r.energy_threshold = 300
            r.dynamic_energy_threshold = True

            with sr.Microphone() as source:
                self.status_signal.emit("🎤 Adjusting for ambient noise…")
                r.adjust_for_ambient_noise(source, duration=0.6)
                self.status_signal.emit("🎤 Listening… speak now")
                audio = r.listen(source, timeout=8, phrase_time_limit=25)

        except sr.WaitTimeoutError:
            self.error_signal.emit("🎤 No speech detected — please try again.")
            return
        except OSError:
            self.error_signal.emit(
                "🎤 Microphone not found.\n"
                "Run:  pip install pyaudio"
            )
            return
        except Exception as e:
            self.error_signal.emit(f"🎤 Microphone error: {str(e)}")
            return

        # ── Transcribe ────────────────────────────────────────────────
        if backend == "whisper":
            self._transcribe_whisper(audio)
        elif backend == "vosk":
            self._transcribe_vosk(audio)
        else:
            self._transcribe_google(audio)

    # ── faster-whisper (offline, ~75 MB tiny model) ───────────────────

    def _transcribe_whisper(self, audio):
        """Transcribe using faster-whisper running entirely on-device."""
        try:
            self.status_signal.emit("🎤 Loading Whisper model (first run only)…")
            model = _get_whisper_model()

            # Convert audio to a raw WAV bytes buffer
            wav_bytes = audio.get_wav_data(convert_rate=16000, convert_width=2)

            # faster-whisper needs a file path or numpy array;
            # write to a temp file so we don't need numpy as a dependency
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav_bytes)
                tmp_path = tmp.name

            self.status_signal.emit("🎤 Transcribing offline…")
            try:
                segments, info = model.transcribe(
                    tmp_path,
                    language="en",
                    beam_size=1,          # fastest setting
                    vad_filter=True,      # skip silence
                )
                text = " ".join(seg.text for seg in segments).strip()
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

            if text:
                self.text_signal.emit(text)
            else:
                self.error_signal.emit("🎤 Could not understand — please try again.")

        except Exception as e:
            # Whisper failed — try Google as fallback if online
            err = str(e)
            if _SR_AVAILABLE and _check_internet():
                self.status_signal.emit("🎤 Whisper failed, trying online fallback…")
                self._transcribe_google(audio)
            else:
                self.error_signal.emit(f"🎤 Whisper error: {err}")

    # ── vosk (offline, needs vosk + language model downloaded) ────────

    def _transcribe_vosk(self, audio):
        """Transcribe using vosk offline model."""
        try:
            # Vosk requires a model folder; check common locations
            model_paths = [
                os.path.join(os.path.expanduser("~"), ".vosk", "model"),
                os.path.join(os.path.expanduser("~"), "vosk-model"),
                "vosk-model",
            ]
            model_dir = next((p for p in model_paths if os.path.isdir(p)), None)

            if model_dir is None:
                # Graceful fallback to Google if online
                if _check_internet():
                    self.status_signal.emit(
                        "🎤 Vosk model not found, using online fallback…"
                    )
                    self._transcribe_google(audio)
                else:
                    self.error_signal.emit(
                        "🎤 Vosk model not found.\n"
                        "Download a model from https://alphacephei.com/vosk/models\n"
                        "and extract it to ~/vosk-model/"
                    )
                return

            self.status_signal.emit("🎤 Transcribing offline (vosk)…")
            vmodel = vosk.Model(model_dir)
            rec    = vosk.KaldiRecognizer(vmodel, 16000)

            wav_bytes = audio.get_wav_data(convert_rate=16000, convert_width=2)
            rec.AcceptWaveform(wav_bytes)
            result = _json.loads(rec.FinalResult())
            text   = result.get("text", "").strip()

            if text:
                self.text_signal.emit(text)
            else:
                self.error_signal.emit("🎤 Could not understand — please try again.")

        except Exception as e:
            if _check_internet():
                self.status_signal.emit("🎤 Vosk failed, using online fallback…")
                self._transcribe_google(audio)
            else:
                self.error_signal.emit(f"🎤 Vosk error: {str(e)}")

    # ── Google STT (online fallback) ──────────────────────────────────

    def _transcribe_google(self, audio):
        """Online fallback — requires internet connection."""
        try:
            r = sr.Recognizer()
            self.status_signal.emit("🎤 Processing speech (online)…")
            text = r.recognize_google(audio)
            if text:
                self.text_signal.emit(text)
            else:
                self.error_signal.emit("🎤 Could not understand — please try again.")
        except sr.UnknownValueError:
            self.error_signal.emit("🎤 Could not understand speech — please try again.")
        except sr.RequestError as e:
            self.error_signal.emit(
                f"🎤 Online STT failed: {e}\n"
                "Install offline STT:  pip install faster-whisper"
            )
        except Exception as e:
            self.error_signal.emit(f"🎤 STT error: {str(e)}")