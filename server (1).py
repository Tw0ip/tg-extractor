import os
import json
import asyncio
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import Channel, Chat, User
from telethon.errors import (
    FloodWaitError,
    UserPrivacyRestrictedError,
    UserNotMutualContactError,
    UserChannelsTooMuchError,
    ChatAdminRequiredError,
    UserKickedError,
    UserBannedInChannelError,
    PeerFloodError,
    SessionPasswordNeededError,
)
import psycopg2
from psycopg2.extras import RealDictCursor

# ─── Logging ───────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tg-extractor")

# ─── FastAPI ───────────────────────────────────────────────
app = FastAPI(title="Telegram Extractor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── PostgreSQL (Railway fornece DATABASE_URL) ─────────────
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    """Retorna conexão PostgreSQL"""
    if not DATABASE_URL:
        raise HTTPException(500, "DATABASE_URL não configurada")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn.autocommit = True
    return conn

def init_db():
    """Cria tabelas se não existirem"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id SERIAL PRIMARY KEY,
                api_id INTEGER NOT NULL,
                api_hash TEXT NOT NULL,
                phone TEXT NOT NULL,
                session_string TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS extracted_members (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                phone TEXT,
                is_bot BOOLEAN DEFAULT FALSE,
                is_premium BOOLEAN DEFAULT FALSE,
                status TEXT,
                group_source TEXT,
                extracted_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.close()
        logger.info("✅ Banco de dados inicializado")
    except Exception as e:
        logger.error(f"❌ Erro ao inicializar DB: {e}")

# ─── Telegram Client ──────────────────────────────────────
client: Optional[TelegramClient] = None
current_phone: Optional[str] = None
current_api_id: Optional[int] = None
current_api_hash: Optional[str] = None

# ─── Modelos ───────────────────────────────────────────────
class LoginRequest(BaseModel):
    api_id: int
    api_hash: str
    phone: str

class CodeRequest(BaseModel):
    code: str

class PasswordRequest(BaseModel):
    password: str

class ExtractRequest(BaseModel):
    group: str
    exclude_bots: bool = True
    exclude_admins: bool = False

class AddMembersRequest(BaseModel):
    group: str
    user_ids: list[int]
    delay: int = 35

# ─── Helpers ───────────────────────────────────────────────
async def get_group_entity(group_input: str):
    """Resolve grupo por @username, link t.me, ID ou nome"""
    global client
    if not client or not client.is_connected():
        raise HTTPException(400, "Não conectado ao Telegram")

    group_input = group_input.strip()

    # Link de convite privado (t.me/+XXXX)
    if "t.me/+" in group_input:
        hash_code = group_input.split("t.me/+")[-1].split("?")[0].split("/")[0]
        try:
            result = await client(CheckChatInviteRequest(hash_code))
            if hasattr(result, 'chat'):
                return result.chat
            raise HTTPException(400, "Você precisa entrar no grupo primeiro via este link de convite")
        except Exception as e:
            raise HTTPException(400, f"Link de convite inválido: {str(e)}")

    # Link t.me/username
    if "t.me/" in group_input:
        group_input = group_input.split("t.me/")[-1].split("?")[0].split("/")[0]

    # Adiciona @ se necessário
    if not group_input.startswith("@") and not group_input.lstrip("-").isdigit():
        group_input = f"@{group_input}"

    # ID numérico
    if group_input.lstrip("-").isdigit():
        group_input = int(group_input)

    # Tenta resolver diretamente
    try:
        entity = await client.get_entity(group_input)
        if isinstance(entity, (Channel, Chat)):
            return entity
        raise HTTPException(400, "A entidade encontrada não é um grupo/canal")
    except ValueError:
        pass

    # Busca nos diálogos
    search_name = str(group_input).replace("@", "").lower()
    async for dialog in client.iter_dialogs():
        if dialog.entity and isinstance(dialog.entity, (Channel, Chat)):
            name = (dialog.name or "").lower()
            username = getattr(dialog.entity, 'username', '') or ""
            if search_name in name or search_name == username.lower():
                return dialog.entity

    raise HTTPException(
        404,
        f"Grupo '{group_input}' não encontrado. "
        "Verifique se: 1) Você é membro do grupo, 2) O username/link está correto, "
        "3) Tente usar o @username do grupo"
    )

# ─── Endpoints ─────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()

@app.get("/status")
async def status():
    global client
    if client and client.is_connected():
        try:
            me = await client.get_me()
            return {
                "connected": True,
                "user": {
                    "id": me.id,
                    "name": f"{me.first_name or ''} {me.last_name or ''}".strip(),
                }
            }
        except:
            pass
    return {"connected": False, "user": None}


@app.post("/login")
async def login(req: LoginRequest):
    global client, current_phone, current_api_id, current_api_hash

    current_api_id = req.api_id
    current_api_hash = req.api_hash
    current_phone = req.phone

    # Usa session em arquivo (persistente no volume do Railway)
    session_name = f"session_{req.phone.replace('+', '')}"

    if client and client.is_connected():
        await client.disconnect()

    client = TelegramClient(session_name, req.api_id, req.api_hash)
    await client.connect()

    if await client.is_user_authorized():
        return {"message": "Já autenticado!", "needs_code": False}

    await client.send_code_request(req.phone)
    return {"message": "Código enviado!", "needs_code": True}


@app.post("/verify-code")
async def verify_code(req: CodeRequest):
    global client, current_phone
    if not client:
        raise HTTPException(400, "Faça login primeiro")

    try:
        await client.sign_in(current_phone, req.code)
        return {"message": "Autenticado com sucesso!", "needs_2fa": False}
    except SessionPasswordNeededError:
        return {"message": "Senha 2FA necessária", "needs_2fa": True}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/verify-2fa")
async def verify_2fa(req: PasswordRequest):
    global client
    if not client:
        raise HTTPException(400, "Faça login primeiro")

    try:
        await client.sign_in(password=req.password)
        return {"message": "Autenticado com sucesso!"}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/groups")
async def groups():
    global client
    if not client or not client.is_connected():
        raise HTTPException(400, "Não conectado")

    if not await client.is_user_authorized():
        raise HTTPException(401, "Não autenticado")

    result = []
    async for dialog in client.iter_dialogs():
        if isinstance(dialog.entity, (Channel, Chat)):
            result.append({
                "id": dialog.entity.id,
                "title": dialog.name,
                "username": getattr(dialog.entity, 'username', None),
                "members_count": getattr(dialog.entity, 'participants_count', None),
            })

    return {"groups": result}


@app.post("/extract")
async def extract(req: ExtractRequest):
    global client
    if not client or not client.is_connected():
        raise HTTPException(400, "Não conectado")

    entity = await get_group_entity(req.group)

    members = []
    try:
        async for user in client.iter_participants(entity):
            if req.exclude_bots and user.bot:
                continue

            members.append({
                "id": user.id,
                "username": user.username or "",
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "phone": user.phone or "",
                "is_bot": user.bot or False,
                "is_premium": getattr(user, 'premium', False) or False,
                "status": str(user.status.__class__.__name__).replace("UserStatus", "") if user.status else "Unknown",
            })
    except ChatAdminRequiredError:
        raise HTTPException(403, "Permissão de admin necessária para extrair membros deste grupo")
    except Exception as e:
        raise HTTPException(500, f"Erro ao extrair: {str(e)}")

    # Salva no PostgreSQL
    try:
        conn = get_db()
        cur = conn.cursor()
        for m in members:
            cur.execute("""
                INSERT INTO extracted_members (user_id, username, first_name, last_name, phone, is_bot, is_premium, status, group_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (m["id"], m["username"], m["first_name"], m["last_name"], m["phone"], m["is_bot"], m["is_premium"], m["status"], req.group))
        conn.close()
    except Exception as e:
        logger.warning(f"Erro ao salvar no DB (não crítico): {e}")

    return {"members": members, "count": len(members)}


@app.post("/add-members")
async def add_members(req: AddMembersRequest):
    global client
    if not client or not client.is_connected():
        raise HTTPException(400, "Não conectado")

    entity = await get_group_entity(req.group)

    async def event_stream():
        added = 0
        failed = 0
        total = len(req.user_ids)

        for i, user_id in enumerate(req.user_ids):
            event = {"index": i, "total": total, "id": user_id, "added": added, "failed": failed}

            try:
                user = await client.get_entity(user_id)
                await client(InviteToChannelRequest(entity, [user]))
                added += 1
                event.update({
                    "status": "success",
                    "name": f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip(),
                    "username": getattr(user, 'username', '') or "",
                    "added": added,
                })
                logger.info(f"✅ [{i+1}/{total}] Adicionado: {user_id}")

            except FloodWaitError as e:
                failed += 1
                event.update({
                    "status": "flood",
                    "error": f"FloodWait: aguardar {e.seconds}s",
                    "wait": e.seconds,
                    "failed": failed,
                })
                logger.warning(f"⏳ FloodWait: {e.seconds}s")
                yield f"data: {json.dumps(event)}\n\n"
                # Encerra no flood para segurança
                event_done = {"index": i, "total": total, "id": 0, "status": "done", "added": added, "failed": failed}
                yield f"data: {json.dumps(event_done)}\n\n"
                return

            except PeerFloodError:
                failed += 1
                event.update({"status": "flood", "error": "PeerFlood: muitas requisições", "failed": failed})
                logger.warning("⏳ PeerFlood detectado")
                yield f"data: {json.dumps(event)}\n\n"
                event_done = {"index": i, "total": total, "id": 0, "status": "done", "added": added, "failed": failed}
                yield f"data: {json.dumps(event_done)}\n\n"
                return

            except UserPrivacyRestrictedError:
                failed += 1
                event.update({"status": "error", "error": "Privacidade restrita", "failed": failed})

            except UserNotMutualContactError:
                failed += 1
                event.update({"status": "error", "error": "Não é contato mútuo", "failed": failed})

            except UserChannelsTooMuchError:
                failed += 1
                event.update({"status": "error", "error": "Usuário em muitos canais", "failed": failed})

            except UserKickedError:
                failed += 1
                event.update({"status": "error", "error": "Usuário foi banido do grupo", "failed": failed})

            except UserBannedInChannelError:
                failed += 1
                event.update({"status": "error", "error": "Usuário banido no canal", "failed": failed})

            except ChatAdminRequiredError:
                failed += 1
                event.update({"status": "error", "error": "Precisa ser admin", "failed": failed})
                yield f"data: {json.dumps(event)}\n\n"
                event_done = {"index": i, "total": total, "id": 0, "status": "done", "added": added, "failed": failed}
                yield f"data: {json.dumps(event_done)}\n\n"
                return

            except Exception as e:
                failed += 1
                event.update({"status": "error", "error": str(e)[:100], "failed": failed})
                logger.error(f"❌ [{i+1}/{total}] Erro: {user_id} - {e}")

            yield f"data: {json.dumps(event)}\n\n"

            # Delay entre adições (não aplica no último)
            if i < total - 1:
                await asyncio.sleep(req.delay)

        # Evento final
        event_done = {"index": total - 1, "total": total, "id": 0, "status": "done", "added": added, "failed": failed}
        yield f"data: {json.dumps(event_done)}\n\n"
        logger.info(f"🏁 Finalizado: {added} adicionados, {failed} falharam de {total}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/logout")
async def logout():
    global client
    if client:
        try:
            await client.log_out()
        except:
            pass
        finally:
            await client.disconnect()
            client = None
    return {"message": "Desconectado"}


# ─── Rodar ────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
