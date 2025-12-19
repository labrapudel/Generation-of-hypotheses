from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from groq import Groq
from langchain_community.document_loaders import PyPDFLoader

import asyncio
import tempfile
import requests
import os
import re

from sqlalchemy.orm import Session

from database import SessionLocal, engine
from models import Base, User, Generation

# ------------------ APP ------------------

app = FastAPI()

app.add_middleware(
    SessionMiddleware,
    secret_key="SUPER_SECRET_KEY_CHANGE_ME"
)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ------------------ AI ------------------

API_KEY = "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"

clients = {
    "llama-3.3-70b-versatile": Groq(api_key=API_KEY)
}
MODEL = "llama-3.3-70b-versatile"

async def generate_hypotheses(client, model, text: str):
    prompt = f"""
Ты — ведущий научный исследователь с широким пониманием современных исследований и трендов в науке. 
Твоя задача — проанализировать массив научных статей и придумать **оригинальные, проверяемые и перспективные научные гипотезы**, 
которые могут быть основой для будущих экспериментов или исследований. 

Руководствуйся следующими правилами:
- Формулируй гипотезы кратко, ясно и конкретно.
- Гипотезы должны быть **проверяемыми экспериментально или аналитически**.
- Старайся быть **креативным и нестандартным**, но при этом обоснованным.
- Используй данные и идеи из текста статей для генерации новых связей и идей.
- Выводи ТОЛЬКО **нумерованный список**, без лишнего текста.

Текст статей (сокращён до 12000 символов):
{text[:12000]}

Выведи в формате:
1. Гипотеза 1
2. Гипотеза 2
"""

    # Синхронный вызов, убираем await
    resp = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.7,  
        max_tokens=800
    )

    lines = resp.choices[0].message.content.strip().split("\n")
    hypotheses = [l.strip() for l in lines if re.match(r"^\d+\.", l.strip())][:2]
    return hypotheses

# ------------------ AUTH ------------------

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/app")
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(request: Request):
    request.session["user"] = "logged"
    return RedirectResponse("/app", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


def require_auth(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/", status_code=303)


# ------------------ APP PAGE ------------------

@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    auth = require_auth(request)
    if auth:
        return auth
    return templates.TemplateResponse("app.html", {"request": request})

# ------------------ GENERATE ------------------

from bs4 import BeautifulSoup

@app.post("/generate", response_class=HTMLResponse)
async def generate(
    request: Request,
    url: str = Form(None),
    file: UploadFile = File(None)
):
    auth = require_auth(request)
    if auth:
        return auth

    full_text = ""
    source = "Неизвестный источник"

    # PDF-файл загружен пользователем
    if file and file.filename.endswith(".pdf"):
        source = file.filename
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        loader = PyPDFLoader(tmp_path)
        full_text = " ".join(p.page_content for p in loader.load())
        os.unlink(tmp_path)

    # URL
    elif url:
        source = url
        if url.endswith(".pdf"):
            r = requests.get(url)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(r.content)
                tmp_path = tmp.name
            loader = PyPDFLoader(tmp_path)
            full_text = " ".join(p.page_content for p in loader.load())
            os.unlink(tmp_path)
        else:
            # Получаем текст с веб-страницы
            r = requests.get(url)
            soup = BeautifulSoup(r.text, "html.parser")
            for s in soup(["script", "style"]):
                s.decompose()
            full_text = soup.get_text(separator="\n")

    # Если текст пустой, выдаем предупреждение
    if not full_text.strip():
        hypotheses = ["Нет текста для анализа"]
    else:
        hypotheses = await generate_hypotheses(clients[MODEL], MODEL, full_text[:12000])

    return templates.TemplateResponse(
        "app.html",
        {
            "request": request,
            "hypotheses": hypotheses,
            "source": source
        }
    )

from database import engine
from models import Base

Base.metadata.create_all(bind=engine)
