"""OpenRouter chat client with image/CSV support and conversation persistence.

Usage:
    from src.openrouter import Chat

    chat = Chat()
    chat.send("What patterns do you see?", image_path="snapshot.png", csv_path="data.csv")
    chat.save()  # persists to .cache/chats/<id>.json
"""

import base64
import json
import os
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

API_URL = "https://openrouter.ai/api/v1/chat/completions"

MODELS = [
    "google/gemini-3.1-flash-lite-preview",
    "google/gemini-3.1-pro-preview",
    "anthropic/claude-sonnet-4.6",
    "openai/gpt-5.4",
    "z-ai/glm-5",
    "minimax/minimax-m2.5",
]

SYSTEM_PROMPT = (
    "You are a trading chart analyst. The user shares screenshots of a pattern "
    "backtesting chart with candlesticks, volume, and technical indicators "
    "(SMA 50/200, RSI 14, AVWAP, Volume Profile, S/R zones, geometric patterns). "
    "Provide concise, actionable analysis referencing specific price levels, "
    "patterns, and indicators visible in the chart."
)

CHAT_DIR = Path(".cache/chats")


def _get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        env_file = Path("web/.env")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("\"'")
                    break
    if not key:
        raise ValueError("OPENROUTER_API_KEY not set in environment or web/.env")
    return key


def _encode_image(path: str) -> str:
    """Read image file and return base64 data URL."""
    p = Path(path)
    suffix = p.suffix.lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        suffix.lstrip("."), "image/png"
    )
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


class Chat:
    """A conversation with an OpenRouter model."""

    def __init__(self, model: str = MODELS[0], chat_id: str | None = None):
        if model not in MODELS:
            raise ValueError(f"Unknown model: {model}. Choose from: {MODELS}")
        self.model = model
        self.chat_id = chat_id or uuid4().hex[:12]
        self.messages: list[dict] = []
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    def fork(self, new_id: str, model: str | None = None) -> "Chat":
        """Fork this conversation into a new chat with a different ID."""
        forked = Chat(model=model or self.model, chat_id=new_id)
        forked.messages = [msg.copy() for msg in self.messages]
        forked.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        forked.save()
        return forked

    def send(
        self,
        text: str = "",
        image_path: str | None = None,
        image_base64: str | None = None,
        attachment_text: str | None = None,
        model: str | None = None,
    ) -> str:
        """Send a message and return the assistant response.

        Pass model to override the chat's default model for this turn only.
        """
        content = []

        # Image
        if image_path:
            content.append({"type": "image_url", "image_url": {"url": _encode_image(image_path)}})
        elif image_base64:
            content.append({"type": "image_url", "image_url": {"url": image_base64}})

        # Build text part
        msg_text = text or ("What do you see in this chart?" if (image_path or image_base64) else "Analyze this data.")
        if attachment_text:
            msg_text += f"\n\n[Attachment]\n{attachment_text}"
        content.append({"type": "text", "text": msg_text})

        user_msg = {"role": "user", "content": content}

        # API call (model param overrides chat default for this turn)
        use_model = model or self.model
        api_key = _get_api_key()
        # Strip [image] placeholders from history (persisted chats don't store base64)
        def clean_msg(msg):
            if isinstance(msg["content"], list):
                parts = [p for p in msg["content"]
                         if not (p.get("type") == "image_url"
                                 and p.get("image_url", {}).get("url") == "[image]")]
                if not parts:
                    return None
                return {**msg, "content": parts}
            return msg

        history = [m for m in (clean_msg(m) for m in self.messages) if m]
        payload = {
            "model": use_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                *history,
                user_msg,
            ],
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            API_URL,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            raise RuntimeError(f"OpenRouter API error {e.code}: {body}") from e

        if "error" in result:
            raise RuntimeError(f"OpenRouter error: {result['error']}")
        reply = result["choices"][0]["message"]["content"]
        # Only persist after successful response
        self.messages.append(user_msg)
        self.messages.append({"role": "assistant", "content": reply})
        self.save()
        return reply

    def save(self):
        """Persist conversation to disk."""
        CHAT_DIR.mkdir(parents=True, exist_ok=True)
        path = CHAT_DIR / f"{self.chat_id}.json"
        # Strip base64 image data for storage (keep a placeholder)
        messages_clean = []
        for msg in self.messages:
            if isinstance(msg["content"], list):
                parts = []
                for part in msg["content"]:
                    if part.get("type") == "image_url":
                        parts.append({"type": "image_url", "image_url": {"url": "[image]"}})
                    else:
                        parts.append(part)
                messages_clean.append({**msg, "content": parts})
            else:
                messages_clean.append(msg)

        data = {
            "chat_id": self.chat_id,
            "model": self.model,
            "created_at": self.created_at,
            "message_count": len(self.messages),
            "messages": messages_clean,
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, chat_id: str) -> "Chat":
        """Load a persisted conversation."""
        path = CHAT_DIR / f"{chat_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Chat {chat_id} not found at {path}")
        data = json.loads(path.read_text())
        chat = cls(model=data["model"], chat_id=data["chat_id"])
        chat.created_at = data["created_at"]
        chat.messages = data["messages"]
        return chat

    @classmethod
    def list_chats(cls) -> list[dict]:
        """List all persisted chats (id, model, created_at, message_count)."""
        if not CHAT_DIR.exists():
            return []
        chats = []
        for p in sorted(CHAT_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text())
                chats.append({
                    "chat_id": data["chat_id"],
                    "model": data["model"],
                    "created_at": data["created_at"],
                    "message_count": data["message_count"],
                })
            except (json.JSONDecodeError, KeyError):
                continue
        return chats

    def print_history(self):
        """Print conversation history to stdout."""
        for msg in self.messages:
            role = msg["role"].upper()
            if isinstance(msg["content"], list):
                texts = [p.get("text", "[image]") for p in msg["content"]]
                body = "\n".join(t for t in texts if t)
            else:
                body = msg["content"]
            print(f"\n--- {role} ---")
            print(body)
