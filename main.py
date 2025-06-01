import asyncio
import uuid
import time
import threading
import signal
import sys
from flask import Flask, request, jsonify, render_template, abort, make_response
from typing import Dict
import aioimaplib
import email
from email.policy import default
import email.utils

app = Flask(__name__)

DOMAIN = "trueelement.in"
IMAP_HOST = "mail.trueelement.in"
IMAP_USER = "support@trueelement.in"
IMAP_PASS = "trueelement@123"
IMAP_PORT = 993
EXPIRATION_SECONDS = 600  # 10 minutes

inboxes: Dict[str, Dict] = {}

def cleanup_expired():
    now = time.time()
    expired = [em for em, d in inboxes.items() if now - d["created"] > EXPIRATION_SECONDS]
    for em in expired:
        print(f"Cleaning expired inbox: {em}")
        del inboxes[em]

@app.after_request
def add_cors_headers(response):
    # Optional: allows cross-origin requests (remove if not needed)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/generate", methods=["POST"])
def generate():
    cleanup_expired()
    local = uuid.uuid4().hex[:8]
    em = f"{local}@{DOMAIN}"
    inboxes[em] = {"created": time.time(), "messages": []}
    print(f"Generated email: {em}")
    return jsonify({"email": em, "expires_in": EXPIRATION_SECONDS})

@app.route("/inbox/<email_address>", methods=["GET"])
def inbox(email_address):
    cleanup_expired()
    print(f"Fetching inbox for: {email_address}")
    if email_address not in inboxes:
        abort(404, description="Inbox expired or not found")
    data = inboxes[email_address]
    expires = int(EXPIRATION_SECONDS - (time.time() - data["created"]))
    return jsonify({"messages": data["messages"], "expires_in": expires})

@app.route("/message/<email_address>/<msg_id>", methods=["GET"])
def message(email_address, msg_id):
    cleanup_expired()
    if email_address not in inboxes:
        abort(404, description="Inbox expired or not found")
    for m in inboxes[email_address]["messages"]:
        if m["id"] == msg_id:
            return jsonify(m)
    abort(404, description="Message not found")

@app.route("/")
def home():
    return render_template("index.html")

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

def background_fetcher(stop_event: threading.Event):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while not stop_event.is_set():
        loop.run_until_complete(fetch_mail())
        stop_event.wait(10)

stop_event = threading.Event()

def start_background_thread():
    thread = threading.Thread(target=background_fetcher, args=(stop_event,), daemon=True)
    thread.start()
    return thread

def signal_handler(sig, frame):
    print("Shutting down...")
    stop_event.set()
    sys.exit(0)

if __name__ == "__main__":
    # Register signal handler to cleanly shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    start_background_thread()
    app.run(debug=True)
