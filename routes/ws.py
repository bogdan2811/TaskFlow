from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from extensions import get_db
from models.chat import ChatParticipant
from routes.auth import JWT_SECRET, JWT_ALGORITHM
from ws_manager import manager

router = APIRouter()


@router.websocket('/ws/{chat_id}')
async def websocket_endpoint(chat_id: int, websocket: WebSocket, db: Session = Depends(get_db)):
    token = websocket.query_params.get('token')
    if not token:
        await websocket.close(code=4001)
        return

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload['sub'])
    except (JWTError, KeyError, ValueError):
        await websocket.close(code=4001)
        return

    if not db.query(ChatParticipant).filter_by(chat_id=chat_id, user_id=user_id).first():
        await websocket.close(code=4003)
        return

    await manager.connect(chat_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(chat_id, websocket)
