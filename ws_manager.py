from typing import Dict, Optional
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._rooms: Dict[int, Dict[WebSocket, int]] = {}

    async def connect(self, chat_id: int, user_id: int, ws: WebSocket):
        await ws.accept()
        self._rooms.setdefault(chat_id, {})[ws] = user_id

    def disconnect(self, chat_id: int, ws: WebSocket):
        room = self._rooms.get(chat_id)
        if not room:
            return
        room.pop(ws, None)
        if not room:
            self._rooms.pop(chat_id, None)

    async def broadcast(self, chat_id: int, payload: dict, exclude_user_id: Optional[int] = None):
        dead: list[WebSocket] = []
        room = self._rooms.get(chat_id, {})
        for ws, user_id in list(room.items()):
            if exclude_user_id is not None and user_id == exclude_user_id:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(chat_id, ws)

    async def disconnect_user(self, chat_id: int, user_id: int, code: int = 4003, reason: str = ''):
        room = self._rooms.get(chat_id, {})
        for ws, connected_user_id in list(room.items()):
            if connected_user_id != user_id:
                continue
            try:
                await ws.close(code=code, reason=reason)
            finally:
                self.disconnect(chat_id, ws)

    async def disconnect_chat(self, chat_id: int, code: int = 4000, reason: str = ''):
        room = self._rooms.get(chat_id, {})
        for ws in list(room.keys()):
            try:
                await ws.close(code=code, reason=reason)
            finally:
                self.disconnect(chat_id, ws)


manager = ConnectionManager()
