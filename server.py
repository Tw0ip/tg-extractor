"""
TG Extractor Backend — FastAPI + Telethon
Extrai membros de grupos do Telegram via MTProto.

Uso:
  pip install fastapi uvicorn telethon python-multipart
  python server.py

Ou gere um .exe:
  pip install pyinstaller
  pyinstaller --onefile --name TG-Extractor server.py
"""

import asyncio
import csv
import io
import os
import sys
import json
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel

from telethon import TelegramClient
from telethon.tl.functions.channels import GetParticipantsRequest, InviteToChannelRequest
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import (
    ChannelParticipantsSearch,
    InputPeerEmpty,
    UserStatusOnline,
    UserStatusRecently,
    UserStatusLastWeek,
    UserStatusLastMonth,
    UserStatusOffline,
    Channel,
    Chat,
)
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    ChatAdminRequiredError,
    FloodWaitError,
    UserPrivacyRestrictedError,
    UserNotMutualContactError,
    UserChannelsTooMuchError,
    PeerFloodError,
    UserBotError,
)

# ─── App ─────────────────────────────────────────────────────────────────
app = FastAPI(title="TG Extractor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── State ───────────────────────────────────────────────────────────────
client: Optional[TelegramClient] = None
phone_hash: Optional[str] = None
extracted_members: list[dict] = []

# Usa /data se existir (volume do Railway), senão ~/.tg-extractor
VOLUME_PATH = Path("/data")
if VOLUME_PATH.exists() and os.access(str(VOLUME_PATH), os.W_OK):
    SESSION_DIR = VOLUME_PATH / "tg-extractor"
else:
    SESSION_DIR = Path(os.path.expanduser("~")) / ".tg-extractor"
SESSION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = str(SESSION_DIR / "session")

# ─── Models ──────────────────────────────────────────────────────────────

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
    group: str  # seu grupo destino
    user_ids: list[int]  # IDs dos usuários a adicionar
    delay: float = 35  # segundos entre cada adição (evitar flood)

# ─── Helpers ─────────────────────────────────────────────────────────────

def get_status(user) -> str:
    s = user.status
    if isinstance(s, UserStatusOnline):
        return "online"
    elif isinstance(s, UserStatusRecently):
        return "recently"
    elif isinstance(s, UserStatusLastWeek):
        return "within_week"
    elif isinstance(s, UserStatusLastMonth):
        return "within_month"
    elif isinstance(s, UserStatusOffline):
        return "offline"
    return "long_ago"

def user_to_dict(user) -> dict:
    return {
        "id": user.id,
        "username": user.username or "",
        "first_name": getattr(user, "first_name", "") or "",
        "last_name": getattr(user, "last_name", "") or "",
        "phone": getattr(user, "phone", "") or "",
        "is_bot": user.bot or False,
        "is_premium": getattr(user, "premium", False) or False,
        "status": get_status(user),
    }

# ─── Embedded Frontend ──────────────────────────────────────────────────

FRONTEND_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TG Extractor</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0a0f; color: #e2e8f0; min-height: 100vh; }
  .container { max-width: 700px; margin: 0 auto; padding: 40px 20px; }
  h1 { text-align: center; font-size: 28px; margin-bottom: 8px; }
  h1 span { color: #0ea5e9; }
  .subtitle { text-align: center; color: #64748b; font-size: 14px; margin-bottom: 40px; }
  .card { background: #111119; border: 1px solid #1e293b; border-radius: 12px; padding: 28px; margin-bottom: 20px; }
  .card h2 { font-size: 18px; margin-bottom: 16px; color: #f1f5f9; }
  label { display: block; font-size: 13px; color: #94a3b8; margin-bottom: 6px; margin-top: 14px; }
  input, select { width: 100%; padding: 10px 14px; background: #1e293b; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; font-size: 14px; outline: none; }
  input:focus { border-color: #0ea5e9; }
  button { width: 100%; padding: 12px; background: #0ea5e9; color: white; border: none; border-radius: 8px; font-size: 15px; font-weight: 600; cursor: pointer; margin-top: 18px; transition: background .2s; }
  button:hover { background: #0284c7; }
  button:disabled { background: #334155; cursor: not-allowed; }
  .btn-outline { background: transparent; border: 1px solid #0ea5e9; color: #0ea5e9; }
  .btn-outline:hover { background: #0ea5e910; }
  .status { display: flex; align-items: center; gap: 8px; padding: 10px 16px; background: #0ea5e910; border: 1px solid #0ea5e930; border-radius: 8px; margin-bottom: 16px; font-size: 13px; color: #0ea5e9; }
  .status .dot { width: 8px; height: 8px; border-radius: 50%; background: #10b981; }
  .error { background: #ef444410; border-color: #ef444430; color: #ef4444; }
  .hidden { display: none; }
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 20px; }
  .stat { text-align: center; background: #1e293b; padding: 14px 8px; border-radius: 8px; }
  .stat .num { font-size: 22px; font-weight: 700; color: #0ea5e9; }
  .stat .lbl { font-size: 11px; color: #64748b; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 10px 12px; color: #64748b; font-size: 12px; border-bottom: 1px solid #1e293b; }
  td { padding: 10px 12px; border-bottom: 1px solid #1e293b10; }
  tr:hover { background: #1e293b40; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
  .badge-bot { background: #64748b20; color: #64748b; }
  .badge-premium { background: #eab30820; color: #eab308; }
  .online-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
  .online-dot.online { background: #10b981; }
  .online-dot.recently { background: #eab308; }
  .online-dot.offline { background: #64748b; }
  .checkbox-row { display: flex; align-items: center; gap: 8px; margin-top: 14px; }
  .checkbox-row input { width: auto; }
  #log { background: #0a0a0f; border: 1px solid #1e293b; border-radius: 8px; padding: 14px; font-family: monospace; font-size: 12px; color: #64748b; max-height: 200px; overflow-y: auto; margin-top: 14px; white-space: pre-wrap; }
  .actions { display: flex; gap: 10px; margin-top: 16px; }
  .actions button { flex: 1; }
</style>
</head>
<body>
<div class="container">
  <h1>⚡ <span>TG Extractor</span></h1>
  <p class="subtitle">Telegram Group Member Extractor</p>

  <div id="msg" class="status hidden"></div>

  <!-- Step 1: Login -->
  <div id="step-login" class="card">
    <h2>🔐 Conectar ao Telegram</h2>
    <label>API ID</label>
    <input id="api_id" type="number" placeholder="Obtenha em my.telegram.org">
    <label>API Hash</label>
    <input id="api_hash" type="text" placeholder="Obtenha em my.telegram.org">
    <label>Número de Telefone</label>
    <input id="phone" type="text" placeholder="+5511999999999">
    <button onclick="doLogin()">Conectar</button>
  </div>

  <!-- Step 2: Code -->
  <div id="step-code" class="card hidden">
    <h2>📱 Código de Verificação</h2>
    <p style="color:#64748b;font-size:13px;margin-bottom:12px;">Insira o código enviado ao seu Telegram</p>
    <input id="code" type="text" placeholder="12345" maxlength="10">
    <button onclick="doCode()">Verificar</button>
  </div>

  <!-- Step 2b: 2FA Password -->
  <div id="step-2fa" class="card hidden">
    <h2>🔑 Senha 2FA</h2>
    <p style="color:#64748b;font-size:13px;margin-bottom:12px;">Sua conta tem verificação em duas etapas</p>
    <input id="password" type="password" placeholder="Sua senha 2FA">
    <button onclick="do2FA()">Verificar</button>
  </div>

  <!-- Step 3: Extract -->
  <div id="step-extract" class="card hidden">
    <h2>🔍 Extrair Membros</h2>
    <label>Link ou Username do Grupo</label>
    <input id="group" type="text" placeholder="https://t.me/grupo ou @grupo">
    <div class="checkbox-row">
      <input type="checkbox" id="exclude_bots" checked>
      <label for="exclude_bots" style="margin:0">Excluir bots</label>
    </div>
    <div class="checkbox-row">
      <input type="checkbox" id="exclude_admins">
      <label for="exclude_admins" style="margin:0">Excluir admins</label>
    </div>
    <button onclick="doExtract()">Extrair Membros</button>
    <div id="log" class="hidden"></div>
  </div>

  <!-- Step 4: Results -->
  <div id="step-results" class="card hidden">
    <h2>📊 Resultados</h2>
    <div id="stats" class="stats"></div>
    <div class="actions">
      <button class="btn-outline" onclick="downloadCSV()">📥 Exportar CSV</button>
      <button class="btn-outline" onclick="downloadJSON()">📥 Exportar JSON</button>
      <button onclick="showStep('step-extract')">🔄 Novo Grupo</button>
    </div>
    <div style="overflow-x:auto;margin-top:16px;">
      <table>
        <thead><tr><th>ID</th><th>Nome</th><th>Username</th><th>Status</th><th>Tags</th></tr></thead>
        <tbody id="table-body"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
const API = window.location.origin;
let members = [];

function showMsg(text, isError) {
  const el = document.getElementById('msg');
  el.className = 'status' + (isError ? ' error' : '');
  el.innerHTML = (isError ? '❌ ' : '<div class="dot"></div>') + text;
  el.classList.remove('hidden');
  if (!isError) setTimeout(() => el.classList.add('hidden'), 5000);
}

function showStep(id) {
  ['step-login','step-code','step-2fa','step-extract','step-results'].forEach(s =>
    document.getElementById(s).classList.toggle('hidden', s !== id));
}

async function doLogin() {
  try {
    const res = await fetch(API + '/login', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        api_id: parseInt(document.getElementById('api_id').value),
        api_hash: document.getElementById('api_hash').value,
        phone: document.getElementById('phone').value
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Erro');
    showMsg(data.message);
    if (data.needs_code) showStep('step-code');
    else showStep('step-extract');
  } catch(e) { showMsg(e.message, true); }
}

async function doCode() {
  try {
    const res = await fetch(API + '/verify-code', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ code: document.getElementById('code').value })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Erro');
    showMsg(data.message);
    if (data.needs_2fa) showStep('step-2fa');
    else showStep('step-extract');
  } catch(e) { showMsg(e.message, true); }
}

async function do2FA() {
  try {
    const res = await fetch(API + '/verify-2fa', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ password: document.getElementById('password').value })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Erro');
    showMsg(data.message);
    showStep('step-extract');
  } catch(e) { showMsg(e.message, true); }
}

async function doExtract() {
  const log = document.getElementById('log');
  log.classList.remove('hidden');
  log.textContent = '⏳ Iniciando extração...\\n';
  try {
    const res = await fetch(API + '/extract', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        group: document.getElementById('group').value,
        exclude_bots: document.getElementById('exclude_bots').checked,
        exclude_admins: document.getElementById('exclude_admins').checked
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Erro');
    members = data.members;
    log.textContent += '✅ ' + members.length + ' membros extraídos!\\n';
    showMsg(members.length + ' membros extraídos com sucesso!');
    renderResults();
    showStep('step-results');
  } catch(e) {
    log.textContent += '❌ Erro: ' + e.message + '\\n';
    showMsg(e.message, true);
  }
}

function renderResults() {
  const stats = document.getElementById('stats');
  const total = members.length;
  const online = members.filter(m => m.status === 'online').length;
  const premium = members.filter(m => m.is_premium).length;
  const withUser = members.filter(m => m.username).length;
  stats.innerHTML = [
    {n: total, l: 'Total'}, {n: online, l: 'Online'},
    {n: premium, l: 'Premium'}, {n: withUser, l: 'Com @'}
  ].map(s => '<div class="stat"><div class="num">'+s.n+'</div><div class="lbl">'+s.l+'</div></div>').join('');

  const tbody = document.getElementById('table-body');
  tbody.innerHTML = members.map(m => {
    const dotClass = m.status === 'online' ? 'online' : m.status === 'recently' ? 'recently' : 'offline';
    const tags = (m.is_bot ? '<span class="badge badge-bot">BOT</span> ' : '') +
                 (m.is_premium ? '<span class="badge badge-premium">⭐ Premium</span>' : '');
    return '<tr><td>'+m.id+'</td><td>'+m.first_name+' '+(m.last_name||'')+'</td>' +
           '<td>'+(m.username ? '@'+m.username : '<span style="color:#475569">—</span>')+'</td>' +
           '<td><span class="online-dot '+dotClass+'"></span>'+m.status+'</td><td>'+tags+'</td></tr>';
  }).join('');
}

function downloadCSV() {
  const header = 'ID,Username,First Name,Last Name,Phone,Is Bot,Is Premium,Status';
  const rows = members.map(m =>
    [m.id,m.username,m.first_name,m.last_name,m.phone,m.is_bot,m.is_premium,m.status].join(','));
  const blob = new Blob([header+'\\n'+rows.join('\\n')], {type:'text/csv'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'members.csv'; a.click();
}

function downloadJSON() {
  const blob = new Blob([JSON.stringify(members, null, 2)], {type:'application/json'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'members.json'; a.click();
}
</script>
</body>
</html>"""

# ─── Routes ──────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return FRONTEND_HTML

@app.get("/status")
async def status():
    connected = client is not None and client.is_connected()
    me = None
    if connected:
        try:
            user = await client.get_me()
            me = {"id": user.id, "name": f"{user.first_name} {user.last_name or ''}".strip()}
        except:
            pass
    return {"connected": connected, "user": me}

@app.post("/login")
async def login(req: LoginRequest):
    global client, phone_hash

    client = TelegramClient(SESSION_FILE, req.api_id, req.api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        return {"message": f"Já conectado como {me.first_name}", "needs_code": False}

    result = await client.send_code_request(req.phone)
    phone_hash = result.phone_code_hash
    return {"message": "Código enviado ao Telegram", "needs_code": True}

@app.post("/verify-code")
async def verify_code(req: CodeRequest):
    global client
    if not client:
        raise HTTPException(400, "Faça login primeiro")

    phone = (await client.get_me()) if await client.is_user_authorized() else None
    if phone:
        return {"message": "Já autenticado", "needs_2fa": False}

    try:
        # Precisamos do phone para sign_in
        await client.sign_in(code=req.code, phone_code_hash=phone_hash)
        me = await client.get_me()
        return {"message": f"Conectado como {me.first_name}!", "needs_2fa": False}
    except SessionPasswordNeededError:
        return {"message": "Senha 2FA necessária", "needs_2fa": True}
    except PhoneCodeInvalidError:
        raise HTTPException(400, "Código inválido")

@app.post("/verify-2fa")
async def verify_2fa(req: PasswordRequest):
    global client
    if not client:
        raise HTTPException(400, "Faça login primeiro")
    try:
        await client.sign_in(password=req.password)
        me = await client.get_me()
        return {"message": f"Conectado como {me.first_name}!"}
    except Exception as e:
        raise HTTPException(400, f"Senha incorreta: {str(e)}")

@app.get("/groups")
async def list_groups():
    if not client or not await client.is_user_authorized():
        raise HTTPException(401, "Não autenticado")

    dialogs = await client.get_dialogs()
    groups = []
    for d in dialogs:
        if isinstance(d.entity, (Channel, Chat)):
            groups.append({
                "id": d.entity.id,
                "title": d.title,
                "username": getattr(d.entity, "username", None),
                "members_count": getattr(d.entity, "participants_count", None),
            })
    return {"groups": groups}

@app.post("/extract")
async def extract(req: ExtractRequest):
    global extracted_members
    if not client or not await client.is_user_authorized():
        raise HTTPException(401, "Não autenticado")

    try:
        entity = await client.get_entity(req.group)
    except Exception as e:
        raise HTTPException(400, f"Grupo não encontrado: {str(e)}")

    all_users = []
    offset = 0
    limit = 200

    try:
        while True:
            participants = await client(GetParticipantsRequest(
                channel=entity,
                filter=ChannelParticipantsSearch(""),
                offset=offset,
                limit=limit,
                hash=0,
            ))

            if not participants.users:
                break

            for user in participants.users:
                if req.exclude_bots and user.bot:
                    continue
                all_users.append(user_to_dict(user))

            offset += len(participants.users)

            if len(participants.users) < limit:
                break

            # Rate limit — esperar um pouco entre requests
            await asyncio.sleep(0.5)

    except ChatAdminRequiredError:
        raise HTTPException(403, "Você precisa ser admin do grupo ou o grupo precisa ter membros visíveis")
    except Exception as e:
        raise HTTPException(500, f"Erro na extração: {str(e)}")

    extracted_members = all_users
    return {"members": all_users, "count": len(all_users)}

@app.get("/export/csv")
async def export_csv():
    if not extracted_members:
        raise HTTPException(400, "Nenhum membro extraído ainda")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["id", "username", "first_name", "last_name", "phone", "is_bot", "is_premium", "status"])
    writer.writeheader()
    writer.writerows(extracted_members)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=members.csv"},
    )

@app.get("/export/json")
async def export_json():
    if not extracted_members:
        raise HTTPException(400, "Nenhum membro extraído ainda")
    return {"members": extracted_members, "count": len(extracted_members), "exported_at": datetime.now().isoformat()}

@app.post("/add-members")
async def add_members(req: AddMembersRequest):
    if not client or not await client.is_user_authorized():
        raise HTTPException(401, "Não autenticado")

    try:
        target = await client.get_entity(req.group)
    except Exception as e:
        raise HTTPException(400, f"Grupo destino não encontrado: {str(e)}")

    async def event_stream():
        added = 0
        failed = 0
        total = len(req.user_ids)

        for i, uid in enumerate(req.user_ids):
            progress = {"index": i, "total": total, "id": uid, "added": added, "failed": failed}

            try:
                user = await client.get_entity(uid)
                name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '') or ''}".strip()
                username = getattr(user, "username", "") or ""

                await client(InviteToChannelRequest(channel=target, users=[user]))
                added += 1
                progress.update({"status": "success", "name": name, "username": username, "added": added})
                yield f"data: {json.dumps(progress)}\n\n"
                await asyncio.sleep(req.delay)

            except FloodWaitError as e:
                failed += 1
                progress.update({"status": "flood", "error": f"FloodWait: aguardar {e.seconds}s", "wait": e.seconds, "failed": failed})
                yield f"data: {json.dumps(progress)}\n\n"
                break

            except UserPrivacyRestrictedError:
                failed += 1
                progress.update({"status": "error", "error": "Privacidade restringe adição", "failed": failed})
                yield f"data: {json.dumps(progress)}\n\n"

            except UserNotMutualContactError:
                failed += 1
                progress.update({"status": "error", "error": "Não é contato mútuo", "failed": failed})
                yield f"data: {json.dumps(progress)}\n\n"

            except UserChannelsTooMuchError:
                failed += 1
                progress.update({"status": "error", "error": "Usuário em muitos canais", "failed": failed})
                yield f"data: {json.dumps(progress)}\n\n"

            except PeerFloodError:
                failed += 1
                progress.update({"status": "flood", "error": "PeerFlood — muitas requisições", "failed": failed})
                yield f"data: {json.dumps(progress)}\n\n"
                break

            except UserBotError:
                failed += 1
                progress.update({"status": "error", "error": "É um bot", "failed": failed})
                yield f"data: {json.dumps(progress)}\n\n"

            except Exception as e:
                failed += 1
                progress.update({"status": "error", "error": str(e), "failed": failed})
                yield f"data: {json.dumps(progress)}\n\n"

        # Final summary
        yield f"data: {json.dumps({'status': 'done', 'added': added, 'failed': failed, 'total': total})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
    })

@app.post("/logout")
async def logout():
    global client
    if client:
        await client.disconnect()
        client = None
    return {"message": "Desconectado"}

# ─── Main ────────────────────────────────────────────────────────────────

def main():
    import uvicorn
    port = int(os.environ.get("PORT", 8777))
    host = "0.0.0.0"
    print("\n" + "=" * 50)
    print(f"  ⚡ TG Extractor v1.0")
    print(f"  🌐 Rodando em: http://{host}:{port}")
    print("=" * 50 + "\n")

    # Só abrir navegador se rodando local
    if os.environ.get("RAILWAY_ENVIRONMENT") is None:
        webbrowser.open(f"http://127.0.0.1:{port}")

    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    main()
