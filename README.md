# ⚡ TG Extractor

Extrator de membros de grupos do Telegram com interface web embutida.

## 🚀 Como usar (modo rápido)

```bash
pip install fastapi uvicorn telethon python-multipart
python server.py
```

O navegador abrirá automaticamente em `http://127.0.0.1:8777`

## 📦 Gerar .exe (Windows)

```bash
pip install -r requirements.txt
pyinstaller --onefile --name TG-Extractor server.py
```

O executável estará em `dist/TG-Extractor.exe`

Ou simplesmente execute `BUILD_EXE.bat`.

## 🔐 Pré-requisitos

1. Acesse [my.telegram.org](https://my.telegram.org)
2. Faça login com seu número
3. Vá em "API development tools"
4. Copie o **API ID** e **API Hash**

## 📋 Funcionalidades

- ✅ Login via MTProto (conta de usuário)
- ✅ Suporte a 2FA
- ✅ Extração de membros de grupos/canais
- ✅ Filtro de bots e admins
- ✅ Exportação CSV e JSON
- ✅ Interface web embutida (dark theme)
- ✅ Sessão persistente (não precisa logar toda vez)

## ⚠️ Avisos

- Use com responsabilidade
- O Telegram pode limitar/banir contas que abusam da API
- Aguarde intervalos entre extrações de grupos diferentes
