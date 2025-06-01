import asyncio
import uuid
import time
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.requests import Request
from typing import Dict
import aioimaplib
import email
from email.policy import default
import email.utils

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DOMAIN = "trueelement.in"
IMAP_HOST = "mail.trueelement.in"
IMAP_USER = "support@trueelement.in"
IMAP_PASS = "trueelement@123"
IMAP_PORT = 993
EXPIRATION_SECONDS = 600  # 10 minutes

# Store inboxes {email: {created: timestamp, messages: [list of messages]}}
inboxes: Dict[str, Dict] = {}

def cleanup_expired():
    now = time.time()
    expired = [em for em, d in inboxes.items() if now - d["created"] > EXPIRATION_SECONDS]
    for em in expired:
        print(f"Cleaning expired inbox: {em}")
        del inboxes[em]

@app.post("/generate")
async def generate():
    cleanup_expired()
    local = uuid.uuid4().hex[:8]
    em = f"{local}@{DOMAIN}"
    inboxes[em] = {"created": time.time(), "messages": []}
    print(f"Generated email: {em}")
    return {"email": em, "expires_in": EXPIRATION_SECONDS}

@app.get("/inbox/{email_address}")
async def inbox(email_address: str):
    cleanup_expired()
    print(f"Fetching inbox for: {email_address}")
    if email_address not in inboxes:
        raise HTTPException(404, "Inbox expired or not found")
    data = inboxes[email_address]
    expires = int(EXPIRATION_SECONDS - (time.time() - data["created"]))
    return {"messages": data["messages"], "expires_in": expires}

@app.get("/message/{email_address}/{msg_id}")
async def message(email_address: str, msg_id: str):
    cleanup_expired()
    if email_address not in inboxes:
        raise HTTPException(404, "Inbox expired or not found")
    for m in inboxes[email_address]["messages"]:
        if m["id"] == msg_id:
            return m
    raise HTTPException(404, "Message not found")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

async def fetch_mail():
    try:
        client = aioimaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        await client.wait_hello_from_server()
        login_resp = await client.login(IMAP_USER, IMAP_PASS)
        if login_resp.result != "OK":
            print("IMAP login failed")
            await client.logout()
            return
        select_resp = await client.select("INBOX")
        if select_resp.result != "OK":
            print("Failed to select INBOX")
            await client.logout()
            return
        search_resp = await client.search('UNSEEN')
        if search_resp.result != "OK":
            print("Search failed")
            await client.logout()
            return

        uids = search_resp.lines[0].decode().split()
        for uid in uids:
            fetch_resp = await client.fetch(uid, '(RFC822)')
            if fetch_resp.result != "OK":
                continue
            raw = fetch_resp.lines[1]
            msg = email.message_from_bytes(raw, policy=default)

            to_header = msg["To"]
            from_header = msg["From"]
            subject = msg["Subject"] or "(No Subject)"
            date = msg["Date"] or ""
            body = ""

            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_content()
                        break
            else:
                body = msg.get_content()

            to_addrs = []
            if to_header:
                to_addrs = [addr.lower() for name, addr in email.utils.getaddresses([to_header])]

            inbox_emails_set = set(k.lower() for k in inboxes.keys())
            matched_emails = [em for em in to_addrs if em in inbox_emails_set]

            for matched_email in matched_emails:
                real_key = next(k for k in inboxes.keys() if k.lower() == matched_email)
                inboxes[real_key]["messages"].append({
                    "id": str(uuid.uuid4()),
                    "from": from_header,
                    "to": matched_email,
                    "subject": subject,
                    "date": date,
                    "body": body
                })

            await client.store(uid, '+FLAGS', '\\Seen')

        await client.logout()
    except Exception as e:
        print("Error fetching mail:", e)

async def background_fetcher():
    while True:
        await fetch_mail()
        await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_fetcher())
