from typing import Dict, Set
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._rooms: Dict[int, Set[WebSocket]] = {}

    async def connect(self, chat_id: int, ws: WebSocket):
        await ws.accept()
        self._rooms.setdefault(chat_id, set()).add(ws)

    def disconnect(self, chat_id: int, ws: WebSocket):
        self._rooms.get(chat_id, set()).discard(ws)

    async def broadcast(self, chat_id: int, payload: dict):
        dead: Set[WebSocket] = set()
        for ws in list(self._rooms.get(chat_id, set())):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._rooms.get(chat_id, set()).discard(ws)


manager = ConnectionManager()
