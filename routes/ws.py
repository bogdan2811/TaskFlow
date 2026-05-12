import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from extensions import get_db
from models.chat import ChatParticipant
from models.message import Message
from routes.auth import _decode_token_subject
from ws_manager import manager

router = APIRouter()


def _is_participant(chat_id: int, user_id: int, db: Session) -> bool:
    return db.query(ChatParticipant).filter_by(chat_id=chat_id, user_id=user_id).first() is not None


@router.websocket('/ws/{chat_id}')
async def websocket_endpoint(chat_id: int, websocket: WebSocket, db: Session = Depends(get_db)):
    token = websocket.query_params.get('token')
    if not token:
        await websocket.close(code=4001)
        return

    try:
        user_id = _decode_token_subject(token)
    except RuntimeError:
        await websocket.close(code=1011)
        return
    except (JWTError, KeyError, ValueError):
        await websocket.close(code=4001)
        return

    if not _is_participant(chat_id, user_id, db):
        await websocket.close(code=4003)
        return

    await manager.connect(chat_id, user_id, websocket)

    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                payload = json.loads(raw_message)
            except json.JSONDecodeError:
                payload = {'type': 'send_message', 'content': raw_message}

            event_type = payload.get('type', 'send_message')

            if event_type in ('send_message', 'message'):
                if not _is_participant(chat_id, user_id, db):
                    await websocket.close(code=4003, reason='You are no longer a chat participant')
                    return

                content = str(payload.get('content', '')).strip()
                if not content:
                    await websocket.send_json({'type': 'error', 'error': 'Message cannot be empty'})
                    continue

                message = Message(chat_id=chat_id, sender_id=user_id, content=content)
                db.add(message)
                try:
                    db.commit()
                    db.refresh(message)
                except SQLAlchemyError:
                    db.rollback()
                    await websocket.send_json({'type': 'error', 'error': 'Message could not be saved'})
                    continue

                await manager.broadcast(chat_id, {'type': 'message_created', 'message': message.to_dict()})
                continue

            if event_type == 'typing':
                if _is_participant(chat_id, user_id, db):
                    await manager.broadcast(
                        chat_id,
                        {
                            'type': 'typing',
                            'userId': user_id,
                            'isTyping': bool(payload.get('isTyping', True)),
                        },
                        exclude_user_id=user_id,
                    )
                continue

            if event_type == 'ping':
                await websocket.send_json({'type': 'pong'})
                continue

            await websocket.send_json({'type': 'error', 'error': 'Unsupported websocket event'})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(chat_id, websocket)
