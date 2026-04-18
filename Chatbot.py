import sys
import os
import re
import threading
import json
import uuid
from datetime import datetime

# Add src directory to path
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)


# Add src directory to path
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from configuration.Appconfig import Appconfig
from chatbot.stt_handler import listen_to_mic
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QTextBrowser,
    QLineEdit, QPushButton, QLabel, QFileDialog, QMessageBox, QApplication,
    QListWidget, QListWidgetItem, QSplitter, QFrame, QMenu, QInputDialog
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize, QUrl, QTimer
from PyQt5.QtGui import QFont, QColor, QDesktopServices

MANUALS_DIR = os.path.join(os.path.dirname(__file__), "manuals")
NETLIST_CONTRACT = ""

try:
    contract_path = os.path.join(MANUALS_DIR, "esim_netlist_analysis_output_contract.txt")
    with open(contract_path, "r", encoding="utf-8") as f:
        NETLIST_CONTRACT = f.read()
        print(f"[COPILOT] Loaded netlist contract from {contract_path}")
except Exception as e:
    print(f"[COPILOT] WARNING: Could not load netlist contract: {e}")
    NETLIST_CONTRACT = (
        "You are a SPICE netlist analyzer.\n"
        "Use the FACT lines to detect issues.\n"
        "Output sections:\n"
        "1. Syntax / SPICE rule errors\n"
        "2. Topology / connection problems\n"
        "3. Simulation setup issues (.ac/.tran/.op etc.)\n"
        "4. Summary\n"
        "Do NOT invent issues not present in FACT lines.\n"
    )

current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(current_dir)
if src_dir not in sys.path:
    sys.path.append(src_dir)

from chatbot.chatbot_core import ESIMCopilotWrapper, clear_history

import subprocess
import tempfile


# ─────────────────────────────────────────────────────────────
#   All detector / validator helper functions (UNCHANGED)
# ─────────────────────────────────────────────────────────────

def _validate_netlist_with_ngspice(netlist_text: str) -> bool:
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.cir', delete=False, encoding='utf-8'
        ) as tmp:
            tmp.write(netlist_text)
            tmp_path = tmp.name
        result = subprocess.run(
            ['ngspice', '-b', tmp_path],
            capture_output=True, text=True, timeout=5
        )
        try:
            os.unlink(tmp_path)
        except:
            pass
        stderr_lower = result.stderr.lower()
        syntax_errors = ['syntax error', 'unrecognized', 'parse error', 'fatal']
        ignore_patterns = ['model', 'library', 'warning', 'no such file', 'cannot find']
        for line in stderr_lower.split('\n'):
            if any(pattern in line for pattern in ignore_patterns):
                continue
            if any(err in line for err in syntax_errors):
                return False
        return True
    except Exception:
        return True


def _detect_missing_subcircuits(netlist_text: str) -> list:
    referenced_subckts = {}
    defined_subckts = set()
    lines = netlist_text.split('\n')
    for line_num, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith('*'):
            continue
        if line.lower().startswith('.subckt'):
            tokens = line.split()
            if len(tokens) >= 2:
                defined_subckts.add(tokens[1].upper())
        elif line.lower().startswith('.include') or line.lower().startswith('.lib'):
            return []
        elif line[0].upper() == 'X':
            tokens = line.split()
            if len(tokens) < 2:
                continue
            instance_name = tokens[0]
            subckt_name = tokens[-1].upper()
            if '=' in subckt_name:
                for tok in reversed(tokens[1:]):
                    if '=' not in tok:
                        subckt_name = tok.upper()
                        break
            if subckt_name not in referenced_subckts:
                referenced_subckts[subckt_name] = []
            referenced_subckts[subckt_name].append((line_num, instance_name))
    missing = []
    for subckt, occurrences in referenced_subckts.items():
        if subckt not in defined_subckts:
            missing.append((subckt, occurrences))
    return missing


def _detect_voltage_source_conflicts(netlist_text: str) -> list:
    voltage_sources = {}
    lines = netlist_text.split('\n')
    for line_num, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith('*') or line.startswith('.'):
            continue
        tokens = line.split()
        if len(tokens) < 4:
            continue
        elem_name = tokens[0]
        if elem_name[0].upper() != 'V':
            continue
        node_plus = re.sub(r'[^\w\-_]', '', tokens[1])
        node_minus = re.sub(r'[^\w\-_]', '', tokens[2])
        if node_plus.lower() in ['0', 'gnd', 'ground', 'vss']:
            node_plus = '0'
        if node_minus.lower() in ['0', 'gnd', 'ground', 'vss']:
            node_minus = '0'
        node_pair = tuple(sorted([node_plus, node_minus]))
        value = "?"
        for i, tok in enumerate(tokens[3:], start=3):
            tok_upper = tok.upper()
            if tok_upper in ['DC', 'AC', 'PULSE', 'SIN', 'PWL']:
                if i + 1 < len(tokens):
                    value = tokens[i + 1]
                break
            elif not tok_upper.startswith('.'):
                value = tok
                break
        if node_pair not in voltage_sources:
            voltage_sources[node_pair] = []
        voltage_sources[node_pair].append((line_num, elem_name, value))
    return [(np, srcs) for np, srcs in voltage_sources.items() if len(srcs) > 1]


def _netlist_ground_info(netlist_text: str):
    has_node0 = False
    has_gnd_label = False
    lines = netlist_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line or line.startswith('*') or line.startswith('.'):
            continue
        tokens = line.split()
        if len(tokens) < 3:
            continue
        elem_name = tokens[0]
        elem_type = elem_name[0].upper()
        nodes = []
        if elem_type in ['R', 'C', 'L', 'V', 'I', 'D']:
            nodes = [tokens[1], tokens[2]]
        elif elem_type == 'Q' and len(tokens) >= 4:
            nodes = [tokens[1], tokens[2], tokens[3]]
        elif elem_type == 'M' and len(tokens) >= 5:
            nodes = [tokens[1], tokens[2], tokens[3], tokens[4]]
        elif elem_type == 'X' and len(tokens) >= 3:
            nodes = tokens[1:-1]
        for node in nodes:
            node = re.sub(r'[=\(\)].*$', '', node)
            node = re.sub(r'[^\w\-_]', '', node)
            if not node:
                continue
            nl = node.lower()
            if nl == '0':
                has_node0 = True
            if nl in ['gnd', 'ground', 'vss']:
                has_gnd_label = True
    return has_node0, has_gnd_label


def _detect_floating_nodes(netlist_text: str) -> list:
    node_counts = {}
    lines = netlist_text.split('\n')
    for line_num, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith('*') or line.startswith('.'):
            continue
        tokens = line.split()
        if len(tokens) < 3:
            continue
        elem_name = tokens[0]
        elem_type = elem_name[0].upper()
        nodes = []
        if elem_type in ['R', 'C', 'L', 'V', 'I', 'D']:
            nodes = [tokens[1], tokens[2]]
        elif elem_type == 'Q' and len(tokens) >= 4:
            nodes = [tokens[1], tokens[2], tokens[3]]
        elif elem_type == 'M' and len(tokens) >= 5:
            nodes = [tokens[1], tokens[2], tokens[3], tokens[4]]
        elif elem_type == 'X' and len(tokens) >= 3:
            candidate_nodes = tokens[1:-1]
            nodes = [tok for tok in candidate_nodes if '=' not in tok]
        for node in nodes:
            node = re.sub(r'[=\(\)].*$', '', node)
            node = re.sub(r'[^\w\-_]', '', node)
            if not node or node[0].isdigit():
                continue
            if node.upper() in ['VALUE', 'V', 'I', 'IF', 'THEN', 'ELSE']:
                continue
            node_lower = node.lower()
            if node_lower in ['0', 'gnd', 'ground', 'vss']:
                node = '0'
            if node not in node_counts:
                node_counts[node] = []
            node_counts[node].append((line_num, elem_name))
    return [(node, occ[0][0], occ[0][1])
            for node, occ in node_counts.items()
            if len(occ) == 1 and node != '0']


def _detect_missing_models(netlist_text: str) -> list:
    referenced_models = {}
    defined_models = set()
    lines = netlist_text.split('\n')
    for line_num, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith('*'):
            continue
        if line.lower().startswith('.model'):
            tokens = line.split()
            if len(tokens) >= 2:
                defined_models.add(tokens[1].upper())
        elif line.lower().startswith('.include') or line.lower().startswith('.lib'):
            return []
        elif line[0].upper() in ['D', 'Q', 'M', 'J']:
            tokens = line.split()
            elem_name = tokens[0]
            elem_type = elem_name[0].upper()
            if elem_type == 'D' and len(tokens) >= 4:
                model = tokens[3].upper()
                referenced_models.setdefault(model, []).append((line_num, elem_name))
            elif elem_type == 'Q' and len(tokens) >= 5:
                model = tokens[-1].upper()
                if not model[0].isdigit():
                    referenced_models.setdefault(model, []).append((line_num, elem_name))
            elif elem_type == 'M' and len(tokens) >= 5:
                for tok in tokens[4:]:
                    if '=' not in tok and not tok[0].isdigit():
                        model = tok.upper()
                        referenced_models.setdefault(model, []).append((line_num, elem_name))
                        break
            elif elem_type == 'J' and len(tokens) >= 4:
                model = tokens[3].upper()
                referenced_models.setdefault(model, []).append((line_num, elem_name))
    return [(model, occs)
            for model, occs in referenced_models.items()
            if model not in defined_models]


# ─────────────────────────────────────────────────────────────
#   Bubble UI helpers  (NEW)
# ─────────────────────────────────────────────────────────────

def _get_time():
    return datetime.now().strftime("%H:%M")


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _render_inline(text: str) -> str:
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(
        r'`([^`]+)`',
        r'<span style="font-family:Consolas,monospace;background:#e8ecf0;'
        r'padding:1px 4px;border-radius:3px;">\1</span>',
        text
    )
    text = text.replace('\n', '<br>')
    return text


def _render_markdown(text: str) -> str:
    result = []
    pattern = re.compile(r'```(\w*)\n?(.*?)```', re.DOTALL)
    last_end = 0
    for match in pattern.finditer(text):
        before = text[last_end:match.start()]
        if before:
            result.append(_render_inline(before))
        lang = match.group(1) or 'code'
        code = (match.group(2)
                .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                .replace('\n', '<br>').replace(' ', '&nbsp;'))
        label = f'<span style="color:#888;font-size:10px;">{lang}</span><br>' if lang else ''
        result.append(
            '<table width="98%" cellpadding="0" cellspacing="3"><tr>'
            '<td style="padding:0;">'
            '<div style="background:#1e1e1e;color:#d4d4d4;'
            'font-family:Consolas,\'Courier New\',monospace;font-size:12px;'
            'padding:10px 14px;border-radius:10px;border-left:3px solid #0095f6;">'
            f'{label}{code}</div></td></tr></table>'
        )
        last_end = match.end()
    tail = text[last_end:]
    if tail:
        result.append(_render_inline(tail))
    return ''.join(result)


def _user_bubble(text: str, timestamp: str) -> str:
    safe = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    safe = safe.replace('\n', '<br>')
    return (
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td width="20%"></td>'
        '<td align="right" style="padding:4px 10px 0 0;">'
        '<table cellpadding="0" cellspacing="2"><tr>'
        '<td style="background:#0095f6;color:white;padding:11px 16px;'
        'border-radius:20px 20px 5px 20px;font-size:13px;line-height:1.6;">'
        f'{safe}'
        '</td></tr>'
        f'<tr><td align="right" style="color:#bbb;font-size:10px;'
        f'padding:3px 2px 8px 0;">You &nbsp;·&nbsp; {timestamp}</td></tr>'
        '</table></td></tr></table>'
    )


def _bot_bubble(text: str, timestamp: str, response_idx: int) -> str:
    rendered = _render_markdown(text)
    copy_href = f'copy://{response_idx}'
    token_est = _approx_tokens(text)
    return (
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td align="left" style="padding:4px 0 0 10px;">'
        '<table cellpadding="0" cellspacing="2"><tr>'
        '<td style="background:#f0f0f0;color:#1a1a2e;padding:11px 16px;'
        'border-radius:20px 20px 20px 5px;font-size:13px;line-height:1.6;">'
        f'{rendered}'
        '</td></tr>'
        '<tr><td>'
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td align="left" style="color:#999;font-size:10px;padding:3px 0 8px 2px;">'
        f'eSim Copilot &nbsp;·&nbsp; {timestamp} &nbsp;·&nbsp; ~{token_est} tokens</td>'
        f'<td align="right" style="padding:3px 4px 8px 0;">'
        f'<a href="{copy_href}" style="color:#0095f6;font-size:10px;'
        f'text-decoration:none;">Copy</a></td>'
        '</tr></table>'
        '</td></tr></table>'
        '</td><td width="20%"></td></tr></table>'
    )


def _system_bubble(text: str) -> str:
    safe = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return (
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td align="center" style="padding:4px 20px;">'
        '<div style="background:#fff3cd;border-left:4px solid #e0a800;'
        'border-radius:8px;padding:7px 14px;font-size:11px;color:#7a5800;">'
        f'{safe}</div></td></tr></table>'
    )


# Typing animation frames
_TYPING_FRAMES = [
    '&#x25CF;&nbsp;<span style="color:#ccc;">&#x25CF;</span>&nbsp;<span style="color:#ccc;">&#x25CF;</span>',
    '<span style="color:#ccc;">&#x25CF;</span>&nbsp;&#x25CF;&nbsp;<span style="color:#ccc;">&#x25CF;</span>',
    '<span style="color:#ccc;">&#x25CF;</span>&nbsp;<span style="color:#ccc;">&#x25CF;</span>&nbsp;&#x25CF;',
]


def _typing_bubble(frame=0) -> str:
    dots = _TYPING_FRAMES[frame % 3]
    return (
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td align="left" style="padding:3px 0 1px 10px;">'
        '<table cellpadding="0" cellspacing="0"><tr>'
        '<td style="background:#f0f4f8;color:#0078d4;'
        'padding:11px 20px;border-radius:20px 20px 20px 5px;'
        'font-size:18px;line-height:1;border:1px solid #d0dce8;">'
        f'{dots}</td></tr></table></td>'
        '<td width="20%"></td></tr></table>'
    )


WELCOME_HTML = """
<div style="margin:24px 10px 8px 10px;text-align:center;">
  <div style="font-size:36px;margin-bottom:8px;">🤖</div>
  <div style="font-size:16px;font-weight:bold;color:#1a1a2e;margin-bottom:6px;">
    eSim Copilot
  </div>
  <div style="font-size:12px;color:#777;line-height:1.8;margin-bottom:16px;">
    Ask me anything about KiCad, NgSpice,<br>
    netlists, simulation errors, or circuit design.<br>
    Attach an image 📎 or speak 🎤 your question.
  </div>
  <div style="display:inline-block;background:#f5f5f5;border-radius:12px;
    padding:10px 18px;font-size:11px;color:#999;margin:0 auto;">
    Use the sidebar to access past chats
  </div>
</div><br>
"""


# ─────────────────────────────────────────────────────────────
#   Worker threads  (UNCHANGED)
# ─────────────────────────────────────────────────────────────

class ChatWorker(QThread):
    response_ready = pyqtSignal(str)

    def __init__(self, user_input, copilot):
        super().__init__()
        self.user_input = user_input
        self.copilot = copilot

    def run(self):
        response = self.copilot.handle_input(self.user_input)
        self.response_ready.emit(response)


class MicWorker(QThread):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False
        self._lock = threading.Lock()

    def request_stop(self):
        with self._lock:
            self._stop_requested = True

    def should_stop(self):
        with self._lock:
            return self._stop_requested

    def run(self):
        try:
            text = listen_to_mic(should_stop=self.should_stop, max_silence_sec=3)
            self.result_ready.emit(text)
        except Exception as e:
            self.error_occurred.emit(f"[Error: {e}]")


# ─────────────────────────────────────────────────────────────
#   CHAT HISTORY SAVE PATH
# ─────────────────────────────────────────────────────────────

CHAT_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_sessions.json")


# ─────────────────────────────────────────────────────────────
#   ChatbotGUI  –  bubble UI upgrade
# ─────────────────────────────────────────────────────────────

class ChatbotGUI(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.copilot = ESIMCopilotWrapper()
        self.current_image_path = None
        self.worker = None
        self._mic_worker = None
        self._is_listening = False
        self._project_dir = None
        self._generation_id = 0

        # Bubble tracking
        self._response_counter = 0
        self._bot_responses = {}      # idx -> full text, for Copy
        self._typing_frame = 0
        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._tick_typing)
        self._typing_anchor = None    # anchor text to find/replace typing bubble

        # Multi-session storage
        self.chats = {}
        self.current_chat_id = None

        self.initUI()
        self._load_sessions_from_disk()

        if not self.chats:
            self.create_new_chat()

    # ──────────────────────────────────────────────────────────
    #   DISK PERSISTENCE  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def _save_sessions_to_disk(self):
        try:
            with open(CHAT_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "chats": self.chats,
                    "current_chat_id": self.current_chat_id
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[COPILOT] Could not save sessions: {e}")

    def _load_sessions_from_disk(self):
        if not os.path.exists(CHAT_HISTORY_FILE):
            return
        try:
            with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.chats = data.get("chats", {})
            saved_id = data.get("current_chat_id")
            self.sidebar_list.clear()
            for chat_id, chat_data in self.chats.items():
                self._add_sidebar_item(chat_id, chat_data["title"])
            if saved_id and saved_id in self.chats:
                self.current_chat_id = saved_id
                self._highlight_active_chat()
                self._render_chat(saved_id)
            elif self.chats:
                first_id = next(iter(self.chats))
                self.current_chat_id = first_id
                self._highlight_active_chat()
                self._render_chat(first_id)
        except Exception as e:
            print(f"[COPILOT] Could not load sessions: {e}")
            self.chats = {}

    # ──────────────────────────────────────────────────────────
    #   MULTI-SESSION MANAGEMENT  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def create_new_chat(self):
        chat_id = str(uuid.uuid4())
        index = len(self.chats) + 1
        title = f"Chat {index}"
        self.chats[chat_id] = {"title": title, "messages": []}
        self._add_sidebar_item(chat_id, title)
        self._switch_to_chat(chat_id)
        self._save_sessions_to_disk()

    def _add_sidebar_item(self, chat_id: str, title: str):
        item = QListWidgetItem("💬  " + title)
        item.setData(Qt.UserRole, chat_id)
        item.setSizeHint(QSize(0, 38))
        self.sidebar_list.insertItem(0, item)

    def _switch_to_chat(self, chat_id: str):
        self.current_chat_id = chat_id
        self._highlight_active_chat()
        self._response_counter = 0
        self._bot_responses = {}
        self.chat_display.setHtml(WELCOME_HTML)
        self._render_chat(chat_id)

    def _highlight_active_chat(self):
        for i in range(self.sidebar_list.count()):
            item = self.sidebar_list.item(i)
            is_active = item.data(Qt.UserRole) == self.current_chat_id
            item.setBackground(QColor("#2a4a6b") if is_active else QColor("transparent"))
            item.setForeground(QColor("#ffffff") if is_active else QColor("#c5cae9"))

    def load_chat(self, item: QListWidgetItem):
        chat_id = item.data(Qt.UserRole)
        if chat_id == self.current_chat_id:
            return
        self._switch_to_chat(chat_id)
        self._save_sessions_to_disk()

    def _render_chat(self, chat_id: str):
        messages = self.chats.get(chat_id, {}).get("messages", [])
        if not messages:
            return
        for msg in messages:
            ts = msg.get("ts", "")
            if msg["role"] == "user":
                self.chat_display.append(_user_bubble(msg["content"], ts))
            else:
                idx = self._response_counter
                self._response_counter += 1
                self._bot_responses[idx] = msg["content"]
                self.chat_display.append(_bot_bubble(msg["content"], ts, idx))
        self._scroll_to_bottom()

    def save_message(self, role: str, content: str, ts: str = ""):
        if not self.current_chat_id:
            return
        self.chats[self.current_chat_id]["messages"].append({
            "role": role,
            "content": content,
            "ts": ts
        })
        if role == "user":
            msgs = self.chats[self.current_chat_id]["messages"]
            user_msgs = [m for m in msgs if m["role"] == "user"]
            if len(user_msgs) == 1:
                new_title = content[:40] + ("…" if len(content) > 40 else "")
                self.chats[self.current_chat_id]["title"] = new_title
                self._update_sidebar_title(self.current_chat_id, new_title)
        self._save_sessions_to_disk()

    def _update_sidebar_title(self, chat_id: str, title: str):
        for i in range(self.sidebar_list.count()):
            item = self.sidebar_list.item(i)
            if item.data(Qt.UserRole) == chat_id:
                item.setText("💬  " + title)
                break

    def _rename_chat(self, chat_id: str):
        current_title = self.chats.get(chat_id, {}).get("title", "")
        new_title, ok = QInputDialog.getText(self, "Rename Chat", "New name:", text=current_title)
        if ok and new_title.strip():
            self.chats[chat_id]["title"] = new_title.strip()
            self._update_sidebar_title(chat_id, new_title.strip())
            self._save_sessions_to_disk()

    def _delete_chat(self, chat_id: str):
        reply = QMessageBox.question(self, "Delete Chat",
                                     "Delete this chat permanently?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        for i in range(self.sidebar_list.count()):
            item = self.sidebar_list.item(i)
            if item.data(Qt.UserRole) == chat_id:
                self.sidebar_list.takeItem(i)
                break
        del self.chats[chat_id]
        if self.current_chat_id == chat_id:
            if self.chats:
                next_id = next(iter(self.chats))
                self._switch_to_chat(next_id)
            else:
                self.current_chat_id = None
                self.chat_display.setHtml(WELCOME_HTML)
                self.create_new_chat()
        self._save_sessions_to_disk()

    def _sidebar_context_menu(self, pos):
        item = self.sidebar_list.itemAt(pos)
        if not item:
            return
        chat_id = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#1e3a5f; color:white; border:1px solid #2a5788; }
            QMenu::item:selected { background:#2a5788; }
        """)
        rename_action = menu.addAction("✏️  Rename")
        delete_action = menu.addAction("🗑️  Delete")
        action = menu.exec_(self.sidebar_list.mapToGlobal(pos))
        if action == rename_action:
            self._rename_chat(chat_id)
        elif action == delete_action:
            self._delete_chat(chat_id)

    # ──────────────────────────────────────────────────────────
    #   TYPING ANIMATION  (NEW)
    # ──────────────────────────────────────────────────────────

    def _start_thinking(self):
        self._typing_frame = 0
        # Insert typing bubble with a known anchor
        anchor = f"TYPING_{uuid.uuid4().hex[:8]}"
        self._typing_anchor = anchor
        html = (
            f'<a name="{anchor}"></a>'
            + _typing_bubble(0)
        )
        self.chat_display.append(html)
        self._scroll_to_bottom()
        self._typing_timer.start(400)

    def _tick_typing(self):
        self._typing_frame += 1
        # We just scroll; the full re-render happens on response
        self._scroll_to_bottom()

    def _stop_thinking(self):
        self._typing_timer.stop()
        # Remove typing bubble by replacing everything after anchor
        # Simplest reliable approach: grab full HTML, strip last typing bubble
        html = self.chat_display.toHtml()
        # Find and remove the anchor + typing bubble block
        if self._typing_anchor:
            anchor_tag = f'<a name="{self._typing_anchor}"></a>'
            idx = html.rfind(anchor_tag)
            if idx != -1:
                html = html[:idx]
                self.chat_display.setHtml(html)
                self._scroll_to_bottom()
        self._typing_anchor = None

    def _scroll_to_bottom(self):
        sb = self.chat_display.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ──────────────────────────────────────────────────────────
    #   initUI  –  upgraded with QTextBrowser + bubble display
    # ──────────────────────────────────────────────────────────

    def initUI(self):
        outer_layout = QHBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # ═══════════════════════════════════════════════
        #  LEFT PANEL – Sidebar  (style UNCHANGED)
        # ═══════════════════════════════════════════════
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet("background-color: #152a43;")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(8, 12, 8, 12)
        sidebar_layout.setSpacing(8)

        logo_label = QLabel("🔬 eSim Copilot")
        logo_label.setStyleSheet("""
            color: #90caf9; font-weight: bold;
            font-size: 14px; padding: 4px 0 8px 2px;
        """)
        sidebar_layout.addWidget(logo_label)

        self.new_chat_btn = QPushButton("＋  New Chat")
        self.new_chat_btn.setCursor(Qt.PointingHandCursor)
        self.new_chat_btn.setFixedHeight(36)
        self.new_chat_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e3a5f; color: #ffffff;
                border: 1px solid #2a5788; border-radius: 8px;
                font-size: 13px; font-weight: bold; padding-left: 8px;
            }
            QPushButton:hover { background-color: #2a5788; border-color: #4fa3e0; }
        """)
        self.new_chat_btn.clicked.connect(self.create_new_chat)
        sidebar_layout.addWidget(self.new_chat_btn)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("color: #2a5788;")
        sidebar_layout.addWidget(divider)

        self.sidebar_list = QListWidget()
        self.sidebar_list.setStyleSheet("""
            QListWidget {
                background: transparent; border: none;
                color: #c5cae9; font-size: 12px;
            }
            QListWidget::item {
                border-radius: 6px; padding: 6px 8px; margin-bottom: 2px;
            }
            QListWidget::item:hover { background-color: #1e3a5f; }
            QListWidget::item:selected { background-color: #2a4a6b; color: #ffffff; }
            QScrollBar:vertical { width: 5px; background: transparent; }
            QScrollBar::handle:vertical { background: #2a5788; border-radius: 3px; }
        """)
        self.sidebar_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sidebar_list.customContextMenuRequested.connect(self._sidebar_context_menu)
        self.sidebar_list.itemClicked.connect(self.load_chat)
        sidebar_layout.addWidget(self.sidebar_list)
        outer_layout.addWidget(self.sidebar)

        # ═══════════════════════════════════════════════
        #  RIGHT PANEL – Chat area
        # ═══════════════════════════════════════════════
        right_panel = QWidget()
        right_panel.setStyleSheet("background-color: #ffffff;")
        self.layout = QVBoxLayout(right_panel)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(8)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("eSim Copilot")
        title_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #34495e;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        self.analyze_netlist_btn = QPushButton("Netlist ▶")
        self.analyze_netlist_btn.setFixedHeight(30)
        self.analyze_netlist_btn.setToolTip("Analyze active project's netlist")
        self.analyze_netlist_btn.setCursor(Qt.PointingHandCursor)
        self.analyze_netlist_btn.setStyleSheet("""
            QPushButton {
                background-color: #2ecc71; color: white;
                border-radius: 15px; padding: 0 10px; font-size: 12px;
            }
            QPushButton:hover { background-color: #27ae60; }
        """)
        self.analyze_netlist_btn.clicked.connect(self.analyze_current_netlist)
        header_layout.addWidget(self.analyze_netlist_btn)

        self.clear_btn = QPushButton("🗑️")
        self.clear_btn.setFixedSize(30, 30)
        self.clear_btn.setToolTip("Clear Chat History")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; border: 1px solid #ddd; border-radius: 15px; font-size: 14px;
            }
            QPushButton:hover { background-color: #ffebee; border-color: #ef9a9a; }
        """)
        self.clear_btn.clicked.connect(self.clear_chat)
        header_layout.addWidget(self.clear_btn)
        self.layout.addLayout(header_layout)

        # ── Chat display (QTextBrowser for link handling) ──
        self.chat_display = QTextBrowser()
        self.chat_display.setReadOnly(True)
        self.chat_display.setOpenLinks(False)
        self.chat_display.anchorClicked.connect(self._handle_anchor)
        self.chat_display.setFont(QFont("Segoe UI", 10))
        self.chat_display.setStyleSheet("""
            QTextBrowser {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 10px;
                padding: 8px;
            }
            QScrollBar:vertical {
                width: 6px; background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #d0d0d0; border-radius: 3px;
            }
        """)
        self.chat_display.setHtml(WELCOME_HTML)
        self.layout.addWidget(self.chat_display)

        # Input area
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)

        self.attach_btn = QPushButton("📎")
        self.attach_btn.setFixedSize(40, 40)
        self.attach_btn.setToolTip("Attach Circuit Image")
        self.attach_btn.setCursor(Qt.PointingHandCursor)
        self.attach_btn.setStyleSheet("""
            QPushButton {
                border: 1px solid #bdc3c7; border-radius: 20px;
                background-color: #ffffff; color: #555; font-size: 18px;
            }
            QPushButton:hover { background-color: #ecf0f1; border-color: #95a5a6; }
        """)
        self.attach_btn.clicked.connect(self.browse_image)
        input_layout.addWidget(self.attach_btn)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Ask eSim Copilot...")
        self.input_field.setFixedHeight(40)
        self.input_field.setStyleSheet("""
            QLineEdit {
                border: 1px solid #bdc3c7; border-radius: 20px;
                padding-left: 15px; padding-right: 15px;
                background-color: #ffffff; font-size: 14px;
            }
            QLineEdit:focus { border: 2px solid #0095f6; }
        """)
        self.input_field.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.input_field)

        self.mic_btn = QPushButton("🎤")
        self.mic_btn.setFixedSize(40, 40)
        self.mic_btn.setToolTip("Speak to type")
        self.mic_btn.setCursor(Qt.PointingHandCursor)
        self.mic_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff; border: 1px solid #bdc3c7;
                border-radius: 20px; font-size: 18px;
            }
            QPushButton:hover { background-color: #ffebee; border-color: #e74c3c; }
        """)
        self.mic_btn.clicked.connect(self.start_listening)
        input_layout.addWidget(self.mic_btn)

        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(40, 40)
        self.send_btn.setToolTip("Send Message")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #0095f6; color: white;
                border: none; border-radius: 20px; font-size: 16px;
            }
            QPushButton:hover { background-color: #0077cc; }
            QPushButton:pressed { background-color: #005fa3; }
        """)
        self.send_btn.clicked.connect(self.send_message)
        input_layout.addWidget(self.send_btn)
        self.layout.addLayout(input_layout)

        # Image status row
        status_layout = QHBoxLayout()
        status_layout.setSpacing(5)
        status_layout.setContentsMargins(0, 0, 0, 0)

        self.filename_status = QLabel("No image attached")
        self.filename_status.setStyleSheet("color: gray; font-size: 12px;")
        self.filename_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_layout.addWidget(self.filename_status)

        self.remove_btn = QPushButton("×")
        self.remove_btn.setFixedSize(25, 25)
        self.remove_btn.setStyleSheet("""
            QPushButton {
                background: #ff6b6b; color: white; border: none;
                border-radius: 12px; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { background: #ff5252; }
        """)
        self.remove_btn.clicked.connect(self.remove_image)
        self.remove_btn.hide()
        status_layout.addWidget(self.remove_btn)

        status_widget = QWidget()
        status_widget.setLayout(status_layout)
        self.layout.addWidget(status_widget)

        outer_layout.addWidget(right_panel, 1)
        self.setLayout(outer_layout)

    # ──────────────────────────────────────────────────────────
    #   ANCHOR CLICK HANDLER  (Copy button)
    # ──────────────────────────────────────────────────────────

    def _handle_anchor(self, url: QUrl):
        scheme = url.scheme()
        path = url.path() or url.host()
        if scheme == "copy":
            try:
                idx = int(path.lstrip('/'))
                text = self._bot_responses.get(idx, "")
                if text:
                    QApplication.clipboard().setText(text)
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────
    #   PROJECT CONTEXT  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def set_project_context(self, project_dir: str):
        if project_dir and os.path.isdir(project_dir):
            self._project_dir = project_dir
            proj_name = os.path.basename(project_dir)
            self.append_message("eSim",
                                f"Project context set to: {proj_name}\nPath: {project_dir}",
                                is_user=False)
        else:
            self._project_dir = None
            self.append_message("eSim", "Project context cleared or invalid.", is_user=False)

    # ──────────────────────────────────────────────────────────
    #   NETLIST ANALYSIS  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def _run_netlist_analysis(self, netlist_path: str, proj_name: str):
        try:
            with open(netlist_path, "r", encoding="utf-8", errors="ignore") as f:
                netlist_text = f.read()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to read netlist:\n{e}")
            return None

        is_syntax_valid = _validate_netlist_with_ngspice(netlist_text)
        floating_nodes = _detect_floating_nodes(netlist_text)
        missing_models = _detect_missing_models(netlist_text)
        missing_subckts = _detect_missing_subcircuits(netlist_text)
        voltage_conflicts = _detect_voltage_source_conflicts(netlist_text)
        text_lower = netlist_text.lower()
        has_tran = ".tran" in text_lower
        has_ac = ".ac" in text_lower
        has_op = ".op" in text_lower
        has_node0, has_gnd_label = _netlist_ground_info(netlist_text)

        floating_desc = "; ".join([f"{n} (line {l}, {e})" for n, l, e in floating_nodes]) or "NONE"
        missing_desc = "; ".join([f"{m} (used {len(o)} times)" for m, o in missing_models]) or "NONE"
        subckt_desc = "; ".join([f"{s} (used {len(o)} times)" for s, o in missing_subckts]) or "NONE"
        if voltage_conflicts:
            conflict_parts = [f"{np}: {', '.join(f'{nm}={v}' for _, nm, v in srcs)}"
                              for np, srcs in voltage_conflicts]
            voltage_conflict_desc = "; ".join(conflict_parts)
        else:
            voltage_conflict_desc = "NONE"

        facts = [
            f"NET_SYNTAX_VALID={'YES' if is_syntax_valid else 'NO'}",
            f"NET_HAS_NODE_0={'YES' if has_node0 else 'NO'}",
            f"NET_HAS_GND_LABEL={'YES' if has_gnd_label else 'NO'}",
            f"NET_HAS_TRAN={'YES' if has_tran else 'NO'}",
            f"NET_HAS_AC={'YES' if has_ac else 'NO'}",
            f"NET_HAS_OP={'YES' if has_op else 'NO'}",
            f"FLOATING_NODES={floating_desc}",
            f"MISSING_MODELS={missing_desc}",
            f"MISSING_SUBCKTS={subckt_desc}",
            f"VOLTAGE_CONFLICTS={voltage_conflict_desc}",
        ]
        facts_block = "\n".join(f"[FACT {f}]" for f in facts)

        full_query = (
            f"{NETLIST_CONTRACT}\n\n"
            "=== NETLIST FACTS (MACHINE-GENERATED) ===\n"
            "The following lines describe the analyzed netlist in a structured way.\n"
            "Each line has the form [FACT KEY=VALUE].\n"
            "You MUST rely ONLY on these FACTS, not on the raw netlist.\n\n"
            f"{facts_block}\n\n"
            "=== RAW NETLIST (FOR REFERENCE ONLY) ===\n"
            "[ESIM_NETLIST_START]\n"
            f"{netlist_text}\n"
            "[ESIM_NETLIST_END]\n\n"
            "REMINDERS:\n"
            "- Do NOT invent issues that are not present in the FACT lines.\n"
            "- If a FACT says NONE, you MUST NOT report any issue for that category.\n"
            "- Follow the output format and rules described in the contract above.\n"
        )
        return full_query

    def analyze_current_netlist(self):
        if self.is_bot_busy():
            return
        if not self._project_dir:
            try:
                obj_appconfig = Appconfig()
                active_project = obj_appconfig.current_project.get("ProjectName")
                if active_project and os.path.isdir(active_project):
                    self._project_dir = active_project
                    proj_name = os.path.basename(active_project)
                    self.append_message("eSim",
                                        f"Auto-detected project: {proj_name}\nPath: {active_project}",
                                        is_user=False)
            except Exception as e:
                print(f"[COPILOT] Could not auto-detect project: {e}")

        if not self._project_dir:
            QMessageBox.warning(self, "No project", "No active eSim project set for the chatbot.")
            return

        proj_name = os.path.basename(self._project_dir)
        try:
            all_files = os.listdir(self._project_dir)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Cannot read project directory:\n{e}")
            return

        cir_candidates = [f for f in all_files if f.endswith('.cir') or f.endswith('.cir.out')]
        if not cir_candidates:
            QMessageBox.warning(self, "Netlist not found",
                                f"No .cir or .cir.out files in:\n{self._project_dir}")
            return

        netlist_path = None
        preferred_out = proj_name + ".cir.out"
        if preferred_out in cir_candidates:
            netlist_path = os.path.join(self._project_dir, preferred_out)
        else:
            preferred_cir = proj_name + ".cir"
            if preferred_cir in cir_candidates:
                netlist_path = os.path.join(self._project_dir, preferred_cir)
            else:
                if len(cir_candidates) > 1:
                    item, ok = QInputDialog.getItem(
                        self, "Select netlist file",
                        "Multiple .cir/.cir.out files found. Select one:",
                        cir_candidates, 0, False)
                    if ok and item:
                        netlist_path = os.path.join(self._project_dir, item)
                elif len(cir_candidates) == 1:
                    netlist_path = os.path.join(self._project_dir, cir_candidates[0])

        if not netlist_path or not os.path.exists(netlist_path):
            QMessageBox.warning(self, "Netlist not found", "Could not determine which netlist to use.")
            return

        netlist_name = os.path.basename(netlist_path)
        self.append_message("eSim", f"Using netlist file:\n{netlist_name}", is_user=False)

        full_query = self._run_netlist_analysis(netlist_path, proj_name)
        if not full_query:
            return

        display_msg = (f"Analyze current netlist of project '{proj_name}' for design mistakes, "
                       "missing connections, or bad values.")
        self.append_message("You", display_msg, is_user=True)
        self._dispatch_worker(full_query)

    def analyze_specific_netlist(self, netlist_path: str):
        if self.is_bot_busy():
            return
        if not os.path.exists(netlist_path):
            QMessageBox.warning(self, "File not found", f"Netlist does not exist:\n{netlist_path}")
            return
        netlist_name = os.path.basename(netlist_path)
        self.append_message("eSim", f"Analyzing specific netlist:\n{netlist_name}", is_user=False)
        proj_name = os.path.splitext(netlist_name)[0]
        full_query = self._run_netlist_analysis(netlist_path, proj_name)
        if not full_query:
            return
        display_msg = f"Analyze netlist '{netlist_name}' for design mistakes, missing connections, or bad values."
        self.append_message("You", display_msg, is_user=True)
        self._dispatch_worker(full_query)

    # ──────────────────────────────────────────────────────────
    #   WORKER DISPATCH  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def _dispatch_worker(self, full_query: str):
        self._set_ui_busy(True)
        self._start_thinking()
        self._generation_id += 1
        current_gen = self._generation_id
        self.worker = ChatWorker(full_query, self.copilot)
        self.worker.response_ready.connect(
            lambda resp, gen=current_gen: self._handle_response_with_id(resp, gen))
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def _set_ui_busy(self, busy: bool):
        self.input_field.setDisabled(busy)
        self.send_btn.setDisabled(busy)
        for btn in [self.attach_btn, self.mic_btn, self.analyze_netlist_btn, self.clear_btn]:
            if hasattr(self, btn.objectName()) or True:
                btn.setDisabled(busy)

    # ──────────────────────────────────────────────────────────
    #   MIC / VOICE  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def stop_analysis(self):
        try:
            if getattr(self, "_mic_worker", None) and self._mic_worker.isRunning():
                self._mic_worker.request_stop()
                self._mic_worker.quit()
                self._mic_worker.wait(200)
                if self._mic_worker.isRunning():
                    self._mic_worker.terminate()
            self._reset_mic_ui()
            if self.worker and self.worker.isRunning():
                self.worker.quit()
                self.worker.wait(500)
                if self.worker.isRunning():
                    self.worker.terminate()
        except Exception as e:
            print(f"Stop analysis error: {e}")

    def start_listening(self):
        if self._mic_worker and self._mic_worker.isRunning():
            self._mic_worker.request_stop()
            return
        self.mic_btn.setStyleSheet("""
            QPushButton { background-color: #e74c3c; color: white; border-radius: 20px; font-size: 18px; }
        """)
        self.mic_btn.setEnabled(True)
        self.input_field.setPlaceholderText("Listening… (click mic to stop)")
        QApplication.processEvents()
        self._mic_worker = MicWorker()
        self._mic_worker.result_ready.connect(self._on_mic_result)
        self._mic_worker.error_occurred.connect(self._on_mic_error)
        self._mic_worker.finished.connect(self._reset_mic_ui)
        self._mic_worker.start()

    def _on_mic_result(self, text):
        self._reset_mic_ui()
        if text and text.strip():
            current = self.input_field.text()
            self.input_field.setText((current + " " + text).strip())
            self.input_field.setFocus()

    def _on_mic_error(self, error_msg):
        if "[Error:" in error_msg and "No speech" not in error_msg:
            QMessageBox.warning(self, "Microphone Error", error_msg)

    def _reset_mic_ui(self):
        self.mic_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff; border: 1px solid #bdc3c7;
                border-radius: 20px; font-size: 18px;
            }
            QPushButton:hover { background-color: #ffebee; border-color: #e74c3c; }
        """)
        self.mic_btn.setEnabled(True)
        self.input_field.setPlaceholderText("Ask eSim Copilot...")

    # ──────────────────────────────────────────────────────────
    #   IMAGE HANDLING  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def browse_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Circuit Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.gif);;All Files (*)")
        if file_path:
            self.current_image_path = file_path
            short_name = os.path.basename(file_path)
            self.filename_status.setText(f"📎 {short_name} attached")
            self.filename_status.setStyleSheet("color: green; font-weight: bold; font-size: 12px;")
            self.remove_btn.show()
            self.input_field.setFocus()

    def is_bot_busy(self):
        if hasattr(self, "worker") and self.worker is not None:
            if self.worker.isRunning():
                QMessageBox.warning(self, "Busy", "Chatbot is busy. Please wait.")
                return True
        return False

    def remove_image(self):
        self.current_image_path = None
        self.filename_status.setText("No image attached")
        self.filename_status.setStyleSheet("color: gray; font-size: 12px;")
        self.remove_btn.hide()

    # ──────────────────────────────────────────────────────────
    #   CLEAR / EXPORT  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def clear_chat(self):
        self.stop_analysis()
        self._generation_id += 1
        reply = QMessageBox.question(
            self, "Clear History",
            "Clear chat history?\nYes = export first, No = clear without saving.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if reply == QMessageBox.Cancel:
            return
        if reply == QMessageBox.Yes:
            self.export_history()

        self._response_counter = 0
        self._bot_responses = {}
        self.chat_display.setHtml(WELCOME_HTML)
        try:
            clear_history()
        except Exception:
            pass
        if self.current_chat_id and self.current_chat_id in self.chats:
            self.chats[self.current_chat_id]["messages"] = []
            self._save_sessions_to_disk()

    def export_history(self):
        text = self.chat_display.toPlainText()
        if not text.strip():
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat History", "chat_history.txt", "Text Files (*.txt)")
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
            QMessageBox.information(self, "Exported", f"History saved to:\n{file_path}")

    # ──────────────────────────────────────────────────────────
    #   SEND MESSAGE  (updated to use bubble display)
    # ──────────────────────────────────────────────────────────

    def send_message(self):
        user_text = self.input_field.text().strip()
        if not user_text and not self.current_image_path:
            return

        full_query = user_text
        display_text = user_text

        if self.current_image_path:
            short_name = os.path.basename(self.current_image_path)
            full_query = f"[Image: {self.current_image_path}] {user_text}".strip()
            question_part = user_text if user_text else ""
            display_text = f"📎 {short_name}\n\n{question_part}" if question_part else f"📎 {short_name}"
            self.current_image_path = None
            self.filename_status.setText("No image attached")
            self.filename_status.setStyleSheet("color: gray; font-size: 12px;")
            self.remove_btn.hide()

        self.append_message("You", display_text, is_user=True)
        self.input_field.clear()
        self._dispatch_worker(full_query)

    # ──────────────────────────────────────────────────────────
    #   WORKER RESPONSE HANDLERS  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def on_worker_finished(self):
        self._set_ui_busy(False)
        self.input_field.setFocus()

    def _handle_response_with_id(self, response: str, gen_id: int):
        if gen_id != self._generation_id:
            return
        self._stop_thinking()
        self.append_message("eSim Copilot", response, is_user=False)

    def handle_response(self, response):
        self._handle_response_with_id(response, self._generation_id)

    # ──────────────────────────────────────────────────────────
    #   MESSAGE RENDERING  (upgraded to bubble style)
    # ──────────────────────────────────────────────────────────

    def append_message(self, sender, text, is_user, save=True):
        if not text:
            return
        ts = _get_time()

        if is_user:
            html = _user_bubble(text, ts)
        else:
            # Check if it's a system/status message
            if sender in ("eSim", "eSim Copilot") and not is_user:
                idx = self._response_counter
                self._response_counter += 1
                self._bot_responses[idx] = text
                html = _bot_bubble(text, ts, idx)
            else:
                html = _system_bubble(text)

        self.chat_display.append(html)
        self._scroll_to_bottom()

        if save and self.current_chat_id:
            role = "user" if is_user else "bot"
            self.save_message(role, text, ts)

    # ──────────────────────────────────────────────────────────
    #   DEBUG / ERROR LOG  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def debug_error(self, error_log_path: str):
        if not error_log_path or not os.path.exists(error_log_path):
            QMessageBox.warning(self, "Error log missing",
                                f"Could not find error log at:\n{error_log_path}")
            return
        try:
            with open(error_log_path, "r", encoding="utf-8", errors="ignore") as f:
                log_text = f.read()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to read error log:\n{e}")
            return

        tail_lines = "\n".join(log_text.splitlines()[-40:])
        display = (
            "Automatic ngspice error captured from eSim:\n\n"
            f"```\n{tail_lines}\n```"
        )
        self.append_message("eSim", display, is_user=False)

        full_query = (
            "The following is an ngspice error log from an eSim simulation.\n"
            "1) Explain the exact root cause in simple terms.\n"
            "2) Give concrete, step-by-step instructions to fix it INSIDE eSim.\n\n"
            "[NGSPICE_ERROR_LOG_START]\n"
            f"{log_text}\n"
            "[NGSPICE_ERROR_LOG_END]"
        )
        self._dispatch_worker(full_query)

    # ──────────────────────────────────────────────────────────
    #   SHUTDOWN  (UNCHANGED)
    # ──────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.stop_analysis()
        self._save_sessions_to_disk()
        try:
            clear_history()
        except Exception:
            pass
        event.accept()


# ─────────────────────────────────────────────────────────────
#   Dock factory functions  (UNCHANGED)
# ─────────────────────────────────────────────────────────────

from PyQt5.QtWidgets import QDockWidget


def createchatbotdock(parent=None):
    dock = QDockWidget("eSim Copilot", parent)
    dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
    dock.setWidget(ChatbotGUI(parent))
    return dock


def create_chatbot_dock(parent=None):
    dock = QDockWidget("eSim Copilot", parent)
    dock.setAllowedAreas(Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
    dock.setWidget(ChatbotGUI(parent))
    return dock


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = ChatbotGUI()
    w.resize(760, 620)
    w.show()
    sys.exit(app.exec_())