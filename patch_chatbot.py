import re

file_path = "src/frontEnd/Chatbot.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# -------------------------------
# 1. REMOVE TOKEN DISPLAY
# -------------------------------
content = re.sub(
    r'&nbsp;·&nbsp; ~\{tokens\} tokens',
    '',
    content
)

# -------------------------------
# 2. AUTO TITLE FROM FIRST MESSAGE
# -------------------------------
# Replace title logic inside _save_current_session
content = re.sub(
    r'title\s*=\s*next\([\s\S]*?"Chat"\)',
    '''first_user = next((m[5:].strip() for m in self.chat_history if m.startswith("User:")), "")
            title = (first_user[:35] + "...") if len(first_user) > 35 else first_user or "Chat"''',
    content
)

# -------------------------------
# 3. ENSURE NEW CHAT DOESN'T AUTO CREATE MULTIPLE SESSIONS
# (No change needed if already correct — safeguard)
# -------------------------------

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("✅ Chatbot updated successfully!")
