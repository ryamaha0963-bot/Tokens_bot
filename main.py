#!/usr/bin/env python3
"""
GitHub Token Generator Bot — Fixed & Deployable
================================================
All known bugs fixed. Works with 2026 GitHub layout.
"""

import asyncio
import logging
import os
import random
import re
import string
import time
import json
from datetime import datetime

import requests
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from playwright_stealth import Stealth

# ============================================================================
# CONFIGURATION
# ============================================================================

# BOT_TOKEN must be set in environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN environment variable is not set. Please set it and restart.")

MAIL_TM_BASE = "https://api.mail.tm"
SCREENSHOT_DIR = "/tmp"
PROXY_URL = None  # e.g. "http://user:pass@host:port"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

sessions = {}  # chat_id -> session dict

# ============================================================================
# CUSTOM TELEGRAM CLIENT
# ============================================================================

class TelegramBot:
    def __init__(self, token):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}/"

    async def send_message(self, chat_id, text, parse_mode="Markdown"):
        url = self.base_url + "sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        try:
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, lambda: requests.post(url, json=payload, timeout=10))
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"send_message error: {e}")
            return None

    async def send_photo(self, chat_id, photo_path, caption=None, parse_mode="Markdown"):
        url = self.base_url + "sendPhoto"
        payload = {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode}
        try:
            with open(photo_path, "rb") as photo_file:
                files = {"photo": photo_file}
                loop = asyncio.get_event_loop()
                r = await loop.run_in_executor(None, lambda: requests.post(url, data=payload, files=files, timeout=20))
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.error(f"send_photo error: {e}")
            return None

    async def get_updates(self, offset=None, timeout=30):
        url = self.base_url + "getUpdates"
        payload = {"timeout": timeout}
        if offset:
            payload["offset"] = offset
        try:
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, lambda: requests.get(url, params=payload, timeout=timeout + 5))
            r.raise_for_status()
            return r.json().get("result", [])
        except Exception as e:
            logger.error(f"get_updates error: {e}")
            return []

# ============================================================================
# HELPERS
# ============================================================================

def gen_username(length=10):
    chars = string.ascii_lowercase + string.digits
    u = random.choice(string.ascii_lowercase)
    u += "".join(random.choice(chars) for _ in range(length - 1))
    return u

def gen_password(length=16):
    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits + "!@#$%^&*"
    pwd = random.choice(string.ascii_lowercase) + random.choice(string.ascii_uppercase)
    pwd += random.choice(string.digits) + random.choice("!@#$%^&*")
    pwd += "".join(random.choice(chars) for _ in range(length - 4))
    pwd_list = list(pwd)
    random.shuffle(pwd_list)
    return "".join(pwd_list)

def gen_email_username():
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10))

def gen_email_password():
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(12))

# ============================================================================
# TEMPORARY EMAIL (mail.tm)
# ============================================================================

def get_domain():
    try:
        r = requests.get(f"{MAIL_TM_BASE}/domains", timeout=10)
        if r.status_code == 200:
            domains = r.json().get("hydra:member", [])
            if domains:
                return domains[0]["domain"]
    except Exception as e:
        logger.warning(f"Domain error: {e}")
    return "mail.tm"

def create_temp_email():
    domain = get_domain()
    user = gen_email_username()
    pwd = gen_email_password()
    email = f"{user}@{domain}"
    try:
        r = requests.post(
            f"{MAIL_TM_BASE}/accounts",
            json={"address": email, "password": pwd},
            timeout=10,
        )
        if r.status_code in (200, 201):
            tr = requests.post(
                f"{MAIL_TM_BASE}/token",
                json={"address": email, "password": pwd},
                timeout=10,
            )
            if tr.status_code == 200:
                td = tr.json()
                return {
                    "email": email,
                    "username": user,
                    "domain": domain,
                    "password": pwd,
                    "bearer_token": td.get("token", ""),
                    "account_id": td.get("id", ""),
                }
    except Exception as e:
        logger.error(f"Temp email error: {e}")
    return None

def poll_email_messages(bearer_token, timeout=90):
    headers = {"Authorization": f"Bearer {bearer_token}"}
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{MAIL_TM_BASE}/messages", headers=headers, timeout=10)
            if r.status_code == 200:
                msgs = r.json().get("hydra:member", [])
                if msgs:
                    return msgs
        except Exception as e:
            logger.warning(f"Poll error: {e}")
        time.sleep(3)
    return None

def read_message(msg_id, bearer_token):
    headers = {"Authorization": f"Bearer {bearer_token}"}
    try:
        r = requests.get(f"{MAIL_TM_BASE}/messages/{msg_id}", headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning(f"Read msg error: {e}")
    return None

# ============================================================================
# GITHUB BROWSER AUTOMATION
# ============================================================================

async def launch_browser(playwright):
    launch_args = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    if PROXY_URL:
        launch_args["proxy"] = {"server": PROXY_URL}

    browser = await playwright.chromium.launch(**launch_args)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    )
    stealth = Stealth()
    await stealth.apply_stealth_async(context)
    return browser, context

async def github_signup(page, email, username, password, chat_id, send_fn):
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                await send_fn(f"🔄 Retry {attempt}/{max_retries}...")

            await page.goto("https://github.com/signup", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            content = await page.content()
            if "Access is temporarily restricted" in content or "unusual activity" in content:
                return {"status": "blocked", "message": "GitHub ne IP block kar diya hai."}

            # --- Fill Email (use name-based first) ---
            await send_fn("✏️ Email fill kar raha hoon...")
            email_sel = page.locator('input[name="user[email]"]')
            if await email_sel.count() == 0:
                email_sel = page.locator("#email")
            if await email_sel.count() == 0:
                email_sel = page.locator('input[type="email"]')
            if await email_sel.count() > 0:
                await email_sel.first.fill(email)
            else:
                if attempt < max_retries: continue
                return {"status": "error", "message": "Email field nahi mila"}
            await page.wait_for_timeout(1000)

            # --- Fill Password ---
            await send_fn("🔑 Password fill kar raha hoon...")
            pwd_sel = page.locator('input[name="user[password]"]')
            if await pwd_sel.count() == 0:
                pwd_sel = page.locator("#password")
            if await pwd_sel.count() == 0:
                pwd_sel = page.locator('input[type="password"]')
            if await pwd_sel.count() > 0:
                await pwd_sel.first.fill(password)
            else:
                if attempt < max_retries: continue
                return {"status": "error", "message": "Password field nahi mila"}
            await page.wait_for_timeout(1000)

            # --- Fill Username ---
            await send_fn("👤 Username fill kar raha hoon...")
            user_sel = page.locator('input[name="user[login]"]')
            if await user_sel.count() == 0:
                user_sel = page.locator("#login")
            if await user_sel.count() == 0:
                user_sel = page.locator('input[type="text"]')
            if await user_sel.count() > 0:
                await user_sel.first.fill(username)
            else:
                if attempt < max_retries: continue
                return {"status": "error", "message": "Username field nahi mila"}
            await page.wait_for_timeout(1000)

            # --- Submit ---
            await send_fn("🚀 Account create kar raha hoon...")
            submit_btn = page.locator('button[type="submit"]')
            if await submit_btn.count() > 0:
                await submit_btn.first.click()
            else:
                # fallback: button with text Continue / Create account
                btn_text = page.locator('button:has-text("Continue")')
                if await btn_text.count() > 0:
                    await btn_text.first.click()
                else:
                    await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)

            # Check result
            curr_url = page.url
            if "verify" in curr_url or "verification" in curr_url:
                return {"status": "verify"}

            if "captcha" in (await page.content()).lower() or await page.locator("iframe").count() > 0:
                ss_path = f"{SCREENSHOT_DIR}/captcha_{chat_id}_{int(time.time())}.png"
                await page.screenshot(path=ss_path)
                return {"status": "captcha", "url": page.url, "screenshot": ss_path}

            return {"status": "success"}
        except Exception as e:
            if attempt < max_retries: continue
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "All retries failed"}

async def github_login(page, username, password):
    try:
        await page.goto("https://github.com/login", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        await page.locator("#login_field").fill(username)
        await page.locator("#password").fill(password)
        await page.locator('input[type="submit"]').click()
        await page.wait_for_timeout(5000)
        return {"status": "success"} if "login" not in page.url else {"status": "failed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def create_pat(page):
    try:
        token_url = "https://github.com/settings/tokens/new?description=bot-token&scopes=workflow"
        await page.goto(token_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        if "login" in page.url:
            return {"status": "not_logged_in"}

        # Set expiry to "No expiration" via dropdown
        try:
            await page.evaluate('''
                const sel = document.querySelector('select[name="expires_at"]');
                if (sel) {
                    for (let opt of sel.options) {
                        if (opt.text.toLowerCase().includes("no expiration")) {
                            sel.value = opt.value;
                            break;
                        }
                    }
                }
            ''')
            await page.wait_for_timeout(500)
        except:
            pass

        # Click generate button
        gen_btn = page.locator('button:has-text("Generate token")')
        if await gen_btn.count() > 0:
            await gen_btn.first.click()
        else:
            sub = page.locator('input[type="submit"]')
            if await sub.count() > 0:
                await sub.first.click()
        await page.wait_for_timeout(5000)

        content = await page.content()
        m = re.search(r'ghp_[a-zA-Z0-9]{36}', content)
        if m:
            return {"status": "success", "token": m.group(0)}

        return {"status": "error", "message": "Token not found"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def after_captcha(page, captcha_url):
    try:
        await page.goto(captcha_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        url = page.url
        content = await page.content()
        # If we are on github.com and not login page, consider success
        if "github.com" in url and "login" not in url:
            if "settings" in url or "dashboard" in url:
                return {"status": "success"}
            if "verification" in url.lower():
                return {"status": "verify"}
        if "check your email" in content.lower():
            return {"status": "verify"}
        return {"status": "unknown"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def do_verify(page, verify_url):
    try:
        await page.goto(verify_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        url = page.url
        content = await page.content()
        if "settings" in url or ("github.com" in url and "login" not in url):
            return {"status": "success"}
        if "your account has been verified" in content.lower():
            return {"status": "success"}
        return {"status": "done"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ============================================================================
# BOT COMMAND HANDLERS
# ============================================================================

async def _clean_session(chat_id):
    """Close browser and playwright if exists."""
    sd = sessions.get(chat_id)
    if sd:
        try:
            if sd.get("browser"):
                await sd["browser"].close()
            if sd.get("playwright"):
                await sd["playwright"].stop()
        except Exception as e:
            logger.warning(f"Cleanup error for {chat_id}: {e}")
        sessions.pop(chat_id, None)

async def _gen_token_flow(bot, chat_id):
    try:
        sd = sessions.get(chat_id)
        if not sd:
            await bot.send_message(chat_id, "❌ Session missing. Start again with /create.")
            return

        page = sd.get("page")
        info = sd.get("account_info", {})
        if not page:
            await bot.send_message(chat_id, "❌ Browser session lost. /create se restart karo.")
            await _clean_session(chat_id)
            return

        if "login" in page.url:
            await bot.send_message(chat_id, "🔐 Login kar raha hoon...")
            lr = await github_login(page, info.get("username"), info.get("password"))
            if lr["status"] != "success":
                await bot.send_message(chat_id, f"❌ Login fail: {lr.get('message')}")
                await _clean_session(chat_id)
                return

        await bot.send_message(chat_id, "🔑 Token generate kar raha hoon...")
        tr = await create_pat(page)
        if tr["status"] == "success":
            token = tr["token"]
            sd["token"] = token
            await bot.send_message(
                chat_id,
                f"🎉 *Token Generated!*\n\n`{token}`\n\n"
                f"👤 User: `{info.get('username')}`\n"
                f"🔐 Pass: `{info.get('password')}`\n"
                f"📧 Email: `{info.get('email')}`"
            )
        else:
            await bot.send_message(chat_id, f"❌ Token error: {tr.get('message')}")
    except Exception as e:
        logger.exception("Token flow error")
        await bot.send_message(chat_id, f"❌ Error: {str(e)}")
    finally:
        await _clean_session(chat_id)

async def handle_verification(bot, chat_id):
    sd = sessions.get(chat_id)
    if not sd:
        await bot.send_message(chat_id, "❌ Session missing.")
        return

    bearer = sd.get("temp_email_info", {}).get("bearer_token")
    if not bearer:
        await bot.send_message(chat_id, "❌ Email token missing. /create se restart karo.")
        await _clean_session(chat_id)
        return

    await bot.send_message(chat_id, "⏳ Inbox check kar raha hoon... (90 seconds)")
    msgs = poll_email_messages(bearer, timeout=90)
    if msgs:
        for msg in msgs:
            detail = read_message(msg["id"], bearer)
            if detail:
                # mail.tm returns html as string, not list
                html_content = detail.get("html", "")
                text_content = detail.get("text", "")
                body = html_content if html_content else text_content
                links = re.findall(r'https?://github\.com/[^\s"\'<>]+', body)
                if links:
                    vurl = links[0].rstrip(".")
                    sd["stage"] = "email_verify_link"
                    sd["verify_url"] = vurl
                    await bot.send_message(
                        chat_id,
                        f"🔗 Verification link mil gaya:\n`{vurl}`\n\n"
                        f"Is URL ko open karo (browser mein) aur phir yahan koi bhi message bhejo."
                    )
                    return
    await bot.send_message(chat_id, "❌ Verification email nahi mili. Check mail.tm manually.")
    await _clean_session(chat_id)

async def handle_command(bot, chat_id, text):
    # --- /start ---
    if text == "/start":
        await bot.send_message(
            chat_id,
            "🤖 *GitHub Token Bot*\n"
            "Ye bot automatically GitHub account bana kar token generate karta hai.\n\n"
            "👇 *Commands:*\n"
            "/create - Naya token banao\n"
            "/help - Help dekho\n"
            "/status - Status check karo\n"
            "/cancel - Process cancel karo"
        )
        return

    if text == "/help":
        await bot.send_message(
            chat_id,
            "📖 *Help Guide*\n"
            "1. `/create` bhejo\n"
            "2. Bot account banayega\n"
            "3. Agar CAPTCHA aaye toh screenshot milega, solve karke *uske baad wala page* ka URL bhejo\n"
            "4. Token generate hone par yahan milega."
        )
        return

    if text == "/status":
        sd = sessions.get(chat_id)
        if not sd:
            await bot.send_message(chat_id, "❌ Koi active session nahi hai.")
            return
        msg = f"📊 *Status*\n• In Progress: {'Yes' if sd.get('in_progress') else 'No'}\n• Stage: `{sd.get('stage', 'None')}`"
        if sd.get("account_info"):
            msg += f"\n• User: `{sd['account_info'].get('username')}`"
        await bot.send_message(chat_id, msg)
        return

    if text == "/cancel":
        await _clean_session(chat_id)
        await bot.send_message(chat_id, "❌ Cancel ho gaya.")
        return

    if text == "/create":
        # Clean existing session first
        await _clean_session(chat_id)

        # Create new session
        sessions[chat_id] = {"in_progress": True, "stage": "signup"}
        await bot.send_message(chat_id, "⏳ Process shuru...")

        # Temp email
        temp_info = create_temp_email()
        if not temp_info:
            await bot.send_message(chat_id, "❌ Temporary email nahi bana. Retry karo.")
            sessions[chat_id]["in_progress"] = False
            return

        email = temp_info["email"]
        password = gen_password()
        username = gen_username()

        sessions[chat_id]["account_info"] = {"email": email, "username": username, "password": password}
        sessions[chat_id]["temp_email_info"] = temp_info

        await bot.send_message(
            chat_id,
            f"✅ Temp email: `{email}`\n👤 User: `{username}`\n🔐 Pass: `{password}`\n\n⏳ GitHub signup load ho raha hai..."
        )

        # Launch browser
        try:
            pw = await async_playwright().start()
            browser, context = await launch_browser(pw)
            page = await context.new_page()
            sessions[chat_id].update({"playwright": pw, "browser": browser, "page": page})
        except Exception as e:
            await bot.send_message(chat_id, f"❌ Browser launch failed: {str(e)}")
            await _clean_session(chat_id)
            return

        async def send_fn(t):
            await bot.send_message(chat_id, t)

        res = await github_signup(page, email, username, password, chat_id, send_fn)

        if res["status"] == "success":
            await _gen_token_flow(bot, chat_id)
        elif res["status"] == "captcha":
            sessions[chat_id]["stage"] = "captcha_solve"
            sessions[chat_id]["captcha_url"] = res["url"]
            await bot.send_photo(
                chat_id,
                res["screenshot"],
                "🔒 *CAPTCHA aa gaya!*\n\n"
                "Screenshot mein CAPTCHA hai. Manually solve karo.\n"
                "Solve karne ke baad *jo page aata hai (dashboard/settings)* uska URL yahan bhejo.",
                parse_mode="Markdown"
            )
        elif res["status"] == "verify":
            sessions[chat_id]["stage"] = "email_verify"
            await bot.send_message(chat_id, "📧 Verification email bheji gayi. Inbox check kar raha hoon...")
            await handle_verification(bot, chat_id)
        else:
            await bot.send_message(chat_id, f"❌ Error: {res.get('message')}")
            await _clean_session(chat_id)
        return

    # --- If in progress, handle user message ---
    sd = sessions.get(chat_id)
    if sd and sd.get("in_progress"):
        stage = sd.get("stage")

        if stage == "captcha_solve":
            await bot.send_message(chat_id, "🔒 Processing CAPTCHA URL...")
            url = text.strip() if text.startswith("http") else sd.get("captcha_url")
            if not url:
                await bot.send_message(chat_id, "❌ Valid URL bhejo.")
                return
            res = await after_captcha(sd["page"], url)
            if res["status"] == "success":
                await _gen_token_flow(bot, chat_id)
            elif res["status"] == "verify":
                await handle_verification(bot, chat_id)
            else:
                await bot.send_message(chat_id, f"❌ CAPTCHA solve fail: {res.get('status')}. Dobara try karo.")
            return

        elif stage == "email_verify_link":
            await bot.send_message(chat_id, "📧 Verifying link...")
            vurl = text.strip() if text.startswith("http") else sd.get("verify_url")
            if not vurl:
                await bot.send_message(chat_id, "❌ Verification URL bhejo.")
                return
            res = await do_verify(sd["page"], vurl)
            if res["status"] == "success":
                await _gen_token_flow(bot, chat_id)
            else:
                await bot.send_message(chat_id, f"❌ Verification fail: {res.get('status')}. Token try karta hoon...")
                await _gen_token_flow(bot, chat_id)  # try token anyway
            return

    # If nothing matches, ignore or send help
    await bot.send_message(chat_id, "Koi active process nahi hai. /create se start karo.")

# ============================================================================
# MAIN LOOP
# ============================================================================

async def main_loop():
    print("🚀 GitHub Token Bot starting...")
    bot = TelegramBot(BOT_TOKEN)
    offset = 0
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30)
            for u in updates:
                offset = u["update_id"] + 1
                if "message" in u and "text" in u["message"]:
                    chat_id = u["message"]["chat"]["id"]
                    text = u["message"]["text"]
                    # Run handler in background
                    asyncio.create_task(handle_command(bot, chat_id, text))
        except Exception as e:
            logger.error(f"Main loop error: {e}")
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("Shutting down...")
