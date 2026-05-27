import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from extensions import get_db
from models.chat import ChatParticipant
from models.message import Message
from models.user import User
from routes.auth import _decode_token_subject
from ws_manager import manager

router = APIRouter()


def _is_participant(chat_id: int, user_id: int, db: Session) -> bool:
    return db.query(ChatParticipant).filter_by(chat_id=chat_id, user_id=user_id).first() is not None


def _participant_ids(chat_id: int, db: Session) -> list[int]:
    return [
        row.user_id
        for row in db.query(ChatParticipant.user_id).filter_by(chat_id=chat_id).all()
    ]


@router.websocket('/ws/user')
async def user_websocket_endpoint(websocket: WebSocket):
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

    await manager.connect_user(user_id, websocket)

    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                payload = json.loads(raw_message)
            except json.JSONDecodeError:
                payload = {'type': raw_message}

            if payload.get('type') == 'ping':
                await websocket.send_json({'type': 'pong'})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect_user_socket(user_id, websocket)


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
    await manager.broadcast(
        chat_id,
        {'type': 'presence_updated', 'chatId': chat_id, 'onlineCount': manager.online_user_count(chat_id)},
    )

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

                sender = db.query(User).filter_by(user_id=user_id).first()
                message_payload = message.to_dict(sender)
                await manager.broadcast(chat_id, {'type': 'message_created', 'message': message_payload})
                await manager.broadcast_users(
                    _participant_ids(chat_id, db),
                    {'type': 'chat_message_created', 'chatId': chat_id, 'message': message_payload},
                )
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
        await manager.broadcast(
            chat_id,
            {'type': 'presence_updated', 'chatId': chat_id, 'onlineCount': manager.online_user_count(chat_id)},
        )
