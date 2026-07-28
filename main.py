#!/usr/bin/env python3
"""
GitHub Token Generator Bot — SLIDER CAPTCHA via Telegram
=========================================================
Handles GitHub's "Slide right to verify" CAPTCHA by sending
screenshot to user and waiting for them to solve it manually.
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
from urllib.parse import urlparse

import requests
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from playwright_stealth import Stealth

# ============================================================================
# CONFIGURATION
# ============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN environment variable is not set.")

MAIL_TM_BASE = "https://api.mail.tm"
SCREENSHOT_DIR = "/tmp"
PROXY_URL = None

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/usr/bin")
os.environ.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
os.environ.setdefault("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "/usr/bin/google-chrome-stable")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

sessions = {}

# ============================================================================
# TELEGRAM BOT
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
    chrome_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "/usr/bin/google-chrome-stable")
    
    launch_args = {
        "headless": True,
        "executable_path": chrome_path,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
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

# ============================================================================
# SLIDER CAPTCHA HANDLING - USER SOLVES VIA TELEGRAM
# ============================================================================

async def handle_slider_captcha(page, chat_id, bot):
    """
    Handle GitHub's slider CAPTCHA:
    1. Take screenshot
    2. Send to user with URL
    3. User opens URL in their browser
    4. User solves slider manually
    5. User sends any message to bot when done
    """
    try:
        # Take full page screenshot
        ss_path = f"{SCREENSHOT_DIR}/slider_{chat_id}_{int(time.time())}.png"
        await page.screenshot(path=ss_path, full_page=True)
        
        # Get the current page URL
        page_url = page.url
        
        # Send to user with instructions
        await bot.send_photo(
            chat_id,
            ss_path,
            caption="🔒 *SLIDER CAPTCHA DETECTED!*\n\n"
                    "❌ This is a slider CAPTCHA (slide right to verify).\n"
                    "It cannot be solved by typing text.\n\n"
                    "📱 *Please solve it manually:*\n"
                    "1️⃣ Click the link below to open in YOUR browser\n"
                    "2️⃣ Solve the slider CAPTCHA\n"
                    "3️⃣ After solving, send ANY message here\n\n"
                    f"🔗 [Open CAPTCHA Page]({page_url})\n\n"
                    "⏱️ You have 120 seconds\n"
                    "🔄 The bot will check if you've solved it.",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        
        # Also send the URL as plain text (for users who can't click links)
        await bot.send_message(
            chat_id,
            f"🌐 *URL to solve:*\n`{page_url}`\n\n"
            "Copy this URL and open in your browser.\n"
            "Solve the slider, then come back and send any message.",
            parse_mode="Markdown"
        )
        
        # Store state
        sessions[chat_id]["stage"] = "slider_solving"
        sessions[chat_id]["slider_page"] = page
        sessions[chat_id]["slider_url"] = page_url
        sessions[chat_id]["slider_start_time"] = time.time()
        sessions[chat_id]["slider_timeout"] = 120
        
        return {"status": "waiting_for_slider"}
        
    except Exception as e:
        logger.error(f"Slider captcha handler error: {e}")
        return {"status": "error", "message": str(e)}

async def check_slider_solved(page):
    """
    Check if the slider CAPTCHA has been solved
    """
    try:
        # Refresh the page to see if CAPTCHA is gone
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        content = await page.content()
        
        # Check for slider indicators
        if "slide right" in content.lower():
            return {"status": "not_solved"}
        
        if "verification required" in content.lower():
            return {"status": "not_solved"}
        
        # Check if we're on a real GitHub page
        if "github.com" in page.url and "login" not in page.url:
            return {"status": "solved"}
        
        # Check for error pages
        if "access is temporarily restricted" in content.lower():
            return {"status": "blocked"}
        
        # If we can't determine, assume it might be solved
        return {"status": "unknown", "url": page.url}
        
    except Exception as e:
        logger.error(f"Check slider error: {e}")
        return {"status": "error", "message": str(e)}

# ============================================================================
# CORE SIGNUP FUNCTION WITH SLIDER CAPTCHA HANDLING
# ============================================================================

async def github_signup(page, email, username, password, chat_id, send_fn, bot):
    """
    Handles GitHub signup with slider CAPTCHA detection
    """
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                await send_fn(f"🔄 Retry {attempt}/{max_retries}...")
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)

            # Go to signup page
            await page.goto("https://github.com/signup", timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            # Check for blocking
            content = await page.content()
            if "Access is temporarily restricted" in content:
                return {"status": "blocked", "message": "GitHub IP blocked - try different proxy"}
            
            # ----- CHECK FOR SLIDER CAPTCHA IMMEDIATELY -----
            if "slide right" in content.lower() or "verification required" in content.lower():
                await send_fn("🔒 Slider CAPTCHA detected! Sending to you for solving...")
                return await handle_slider_captcha(page, chat_id, bot)
            
            # Check for regular captcha (text)
            if "captcha" in content.lower() or "verify" in content.lower():
                # This is a text captcha, handle with text input
                return await handle_text_captcha(page, chat_id, bot)

            # ----- Fill Email -----
            await send_fn("✏️ Entering email...")
            email_found = False
            for sel in ['input[name="user[email]"]', '#email', 'input[type="email"]']:
                try:
                    el = page.locator(sel)
                    if await el.count() > 0:
                        await el.first.fill(email)
                        email_found = True
                        break
                except:
                    continue
            if not email_found:
                return {"status": "error", "message": "Email field not found"}
            await page.wait_for_timeout(1000)

            # ----- Fill Password -----
            pwd_found = False
            for sel in ['input[name="user[password]"]', '#password', 'input[type="password"]']:
                try:
                    el = page.locator(sel)
                    if await el.count() > 0:
                        await el.first.fill(password)
                        pwd_found = True
                        break
                except:
                    continue
            if not pwd_found:
                return {"status": "error", "message": "Password field not found"}
            await page.wait_for_timeout(1000)

            # ----- Fill Username -----
            user_found = False
            for sel in ['input[name="user[login]"]', '#login', 'input[type="text"]']:
                try:
                    el = page.locator(sel)
                    if await el.count() > 0:
                        await el.first.fill(username)
                        user_found = True
                        break
                except:
                    continue
            if not user_found:
                return {"status": "error", "message": "Username field not found"}
            await page.wait_for_timeout(1000)

            # ----- Submit -----
            await send_fn("🚀 Creating account...")
            submit_btn = page.locator('button[type="submit"]')
            if await submit_btn.count() > 0:
                await submit_btn.first.click()
            else:
                await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)

            # ----- Check result -----
            curr_url = page.url
            content = await page.content()
            
            # Check for SLIDER CAPTCHA after submit
            if "slide right" in content.lower() or "verification required" in content.lower():
                await send_fn("🔒 Slider CAPTCHA detected! Sending to you for solving...")
                return await handle_slider_captcha(page, chat_id, bot)
            
            # Check for text CAPTCHA after submit
            if "captcha" in content.lower() or "verify" in curr_url.lower():
                return await handle_text_captcha(page, chat_id, bot)
            
            if "github.com" in curr_url and "login" not in curr_url:
                return {"status": "success"}

            return {"status": "unknown", "url": curr_url}
            
        except Exception as e:
            if attempt < max_retries:
                await send_fn(f"⚠️ Attempt {attempt+1} failed: {str(e)[:50]}. Retrying...")
                continue
            return {"status": "error", "message": str(e)}
    
    return {"status": "error", "message": "All retries failed"}

# ============================================================================
# TEXT CAPTCHA HANDLING (Fallback for text-based CAPTCHAs)
# ============================================================================

async def handle_text_captcha(page, chat_id, bot):
    """
    Handle text-based CAPTCHA (if GitHub uses it)
    """
    # Take screenshot
    ss_path = f"{SCREENSHOT_DIR}/captcha_{chat_id}_{int(time.time())}.png"
    await page.screenshot(path=ss_path, full_page=True)
    
    # Send to user with instructions
    await bot.send_photo(
        chat_id,
        ss_path,
        caption="🔒 *TEXT CAPTCHA Detected!*\n\n"
                "Please solve the CAPTCHA in the image above.\n"
                "Type the CAPTCHA text here and send it to me.\n\n"
                "📝 *Example:* If CAPTCHA shows 'ABC123', send: `ABC123`\n\n"
                "⏱️ You have 60 seconds.",
        parse_mode="Markdown"
    )
    
    # Store state
    sessions[chat_id]["stage"] = "text_captcha_solving"
    sessions[chat_id]["captcha_page"] = page
    sessions[chat_id]["captcha_timeout"] = time.time() + 60
    
    return {"status": "waiting_for_text_captcha"}

async def enter_text_captcha_and_submit(page, chat_id, captcha_text, bot):
    """
    Enter text CAPTCHA provided by user
    """
    try:
        # Look for CAPTCHA input field
        captcha_selectors = [
            'input[name="captcha"]',
            '#captcha',
            'input[placeholder*="captcha" i]',
            'input[aria-label*="captcha" i]',
            '//input[@type="text"]',
        ]
        
        captcha_found = False
        for sel in captcha_selectors:
            try:
                if sel.startswith('//'):
                    el = page.locator(f'xpath={sel}')
                else:
                    el = page.locator(sel)
                if await el.count() > 0:
                    await el.first.fill(captcha_text)
                    captcha_found = True
                    await bot.send_message(chat_id, "✅ CAPTCHA text entered!")
                    break
            except:
                continue
        
        if not captcha_found:
            await bot.send_message(chat_id, "❌ CAPTCHA input field not found. Trying to find it...")
            inputs = await page.locator('input[type="text"]').all()
            for inp in inputs:
                if await inp.is_visible():
                    await inp.fill(captcha_text)
                    captcha_found = True
                    await bot.send_message(chat_id, "✅ CAPTCHA text entered!")
                    break
        
        if not captcha_found:
            return {"status": "error", "message": "Could not find CAPTCHA input"}
        
        # Submit
        await page.wait_for_timeout(1000)
        submit_btn = page.locator('button[type="submit"]')
        if await submit_btn.count() > 0:
            await submit_btn.first.click()
        else:
            btn = page.locator('button:has-text("Verify")')
            if await btn.count() > 0:
                await btn.first.click()
            else:
                await page.keyboard.press("Enter")
        
        await page.wait_for_timeout(5000)
        
        # Check result
        curr_url = page.url
        content = await page.content()
        
        # Check for slider again
        if "slide right" in content.lower():
            return {"status": "slider_detected"}
        
        if "captcha" in content.lower() or "verify" in curr_url.lower():
            return {"status": "captcha_incorrect"}
        
        if "github.com" in curr_url and "login" not in curr_url:
            return {"status": "success"}
        
        return {"status": "unknown", "url": curr_url}
        
    except Exception as e:
        logger.error(f"Error entering CAPTCHA: {e}")
        return {"status": "error", "message": str(e)}

# ============================================================================
# OTHER GITHUB FUNCTIONS
# ============================================================================

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

        # Set expiry to "No expiration"
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
# SESSION MANAGEMENT
# ============================================================================

async def _clean_session(chat_id):
    """Clean up browser session."""
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

        # Login if needed
        if "login" in page.url:
            await bot.send_message(chat_id, "🔐 Logging in...")
            lr = await github_login(page, info.get("username"), info.get("password"))
            if lr["status"] != "success":
                await bot.send_message(chat_id, f"❌ Login fail: {lr.get('message')}")
                await _clean_session(chat_id)
                return

        await bot.send_message(chat_id, "🔑 Generating token...")
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
            await _clean_session(chat_id)
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

    await bot.send_message(chat_id, "⏳ Checking inbox... (90 seconds)")
    msgs = poll_email_messages(bearer, timeout=90)
    if msgs:
        for msg in msgs:
            detail = read_message(msg["id"], bearer)
            if detail:
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
                        f"🔗 Verification link found:\n`{vurl}`\n\n"
                        f"Opening link automatically...",
                    )
                    res = await do_verify(sd["page"], vurl)
                    if res["status"] == "success":
                        await bot.send_message(chat_id, "✅ Email verified!")
                        await _gen_token_flow(bot, chat_id)
                    else:
                        await bot.send_message(chat_id, "⚠️ Verification might need manual action.")
                    return
    await bot.send_message(chat_id, "❌ Verification email not found. Check mail.tm manually.")
    await _clean_session(chat_id)

# ============================================================================
# COMMAND HANDLER - WITH SLIDER CAPTCHA INPUT HANDLING
# ============================================================================

async def handle_command(bot, chat_id, text):
    # --- /start ---
    if text == "/start":
        await bot.send_message(
            chat_id,
            "🤖 *GitHub Token Bot*\n"
            "Generates GitHub accounts and tokens.\n\n"
            "👇 *Commands:*\n"
            "/create - Generate new token\n"
            "/help - Help\n"
            "/status - Check status\n"
            "/cancel - Cancel process"
        )
        return

    if text == "/help":
        await bot.send_message(
            chat_id,
            "📖 *Help Guide*\n"
            "1. Send `/create`\n"
            "2. Bot will start GitHub signup\n"
            "3. If SLIDER CAPTCHA appears:\n"
            "   - Bot sends screenshot and URL\n"
            "   - Open URL in YOUR browser\n"
            "   - Solve the slider\n"
            "   - Send ANY message to bot\n"
            "4. Bot continues automatically\n"
            "5. Token delivered here"
        )
        return

    if text == "/status":
        sd = sessions.get(chat_id)
        if not sd:
            await bot.send_message(chat_id, "❌ No active session.")
            return
        msg = f"📊 *Status*\n• In Progress: {'Yes' if sd.get('in_progress') else 'No'}\n• Stage: `{sd.get('stage', 'None')}`"
        if sd.get("account_info"):
            msg += f"\n• User: `{sd['account_info'].get('username')}`"
        await bot.send_message(chat_id, msg)
        return

    if text == "/cancel":
        await _clean_session(chat_id)
        await bot.send_message(chat_id, "❌ Cancelled.")
        return

    # ============================================================
    # CHECK: USER IS SOLVING SLIDER CAPTCHA
    # ============================================================
    sd = sessions.get(chat_id)
    if sd and sd.get("stage") == "slider_solving":
        # User has sent a message after solving slider
        await bot.send_message(chat_id, "🔍 Checking if slider is solved...")
        
        page = sd.get("slider_page") or sd.get("page")
        if not page:
            await bot.send_message(chat_id, "❌ Browser page lost. Start again with /create.")
            await _clean_session(chat_id)
            return
        
        # Check if slider is solved
        result = await check_slider_solved(page)
        
        if result["status"] == "solved":
            await bot.send_message(chat_id, "✅ Slider solved successfully! Continuing...")
            sd["stage"] = "signup_complete"
            await _gen_token_flow(bot, chat_id)
            return
            
        elif result["status"] == "not_solved":
            elapsed = time.time() - sd.get("slider_start_time", time.time())
            timeout = sd.get("slider_timeout", 120)
            
            if elapsed > timeout:
                await bot.send_message(
                    chat_id,
                    "⏰ *Timeout!* Slider not solved within 120 seconds.\n"
                    "Please try again with `/create`",
                    parse_mode="Markdown"
                )
                await _clean_session(chat_id)
                return
            else:
                remaining = int(timeout - elapsed)
                await bot.send_message(
                    chat_id,
                    f"⏳ Slider still not solved. ({remaining} seconds left)\n\n"
                    f"🌐 Open this URL in your browser:\n`{sd.get('slider_url')}`\n\n"
                    "Solve the slider and send ANY message when done.",
                    parse_mode="Markdown"
                )
                return
                
        elif result["status"] == "blocked":
            await bot.send_message(
                chat_id,
                "🚫 *GitHub IP Blocked!*\n"
                "The IP address is blocked. Try using a different network or proxy.\n"
                "Start again with `/create`",
                parse_mode="Markdown"
            )
            await _clean_session(chat_id)
            return
        else:
            await bot.send_message(
                chat_id,
                f"⚠️ Unknown status: {result.get('status')}\n"
                f"URL: {result.get('url', 'N/A')}\n\n"
                "Please solve the slider and send ANY message when done."
            )
            return

    # ============================================================
    # CHECK: USER IS SOLVING TEXT CAPTCHA
    # ============================================================
    if sd and sd.get("stage") == "text_captcha_solving":
        captcha_text = text.strip()
        
        # Validate: CAPTCHA is usually alphanumeric, 4-8 characters
        if not re.match(r'^[A-Za-z0-9]{4,8}$', captcha_text):
            await bot.send_message(
                chat_id,
                "⚠️ CAPTCHA usually has 4-8 alphanumeric characters.\n"
                f"Try again or send `/cancel` to stop."
            )
            return
        
        await bot.send_message(chat_id, "🔍 Entering CAPTCHA and submitting...")
        
        page = sd.get("captcha_page") or sd.get("page")
        if not page:
            await bot.send_message(chat_id, "❌ Browser page lost. Start again with /create.")
            await _clean_session(chat_id)
            return
        
        result = await enter_text_captcha_and_submit(page, chat_id, captcha_text, bot)
        
        if result["status"] == "success":
            await bot.send_message(chat_id, "✅ CAPTCHA solved successfully! Continuing...")
            await _gen_token_flow(bot, chat_id)
            return
        elif result["status"] == "slider_detected":
            await bot.send_message(chat_id, "🔄 Slider CAPTCHA appeared. Sending to you...")
            await handle_slider_captcha(page, chat_id, bot)
            return
        elif result["status"] == "captcha_incorrect":
            await bot.send_message(
                chat_id,
                "❌ CAPTCHA was incorrect. Please try again.\n"
                "Send the correct CAPTCHA text or `/cancel` to stop."
            )
            sd["stage"] = "text_captcha_solving"
            return
        else:
            await bot.send_message(
                chat_id,
                f"❌ Error: {result.get('message', 'Unknown error')}\n"
                f"Try sending the CAPTCHA text again."
            )
            return

    # ============================================================
    # /create COMMAND
    # ============================================================
    if text == "/create":
        await _clean_session(chat_id)

        sessions[chat_id] = {"in_progress": True, "stage": "signup"}
        await bot.send_message(chat_id, "⏳ Starting process...")

        # Create temp email
        temp_info = create_temp_email()
        if not temp_info:
            await bot.send_message(chat_id, "❌ Failed to create temp email. Retry.")
            sessions[chat_id]["in_progress"] = False
            return

        email = temp_info["email"]
        password = gen_password()
        username = gen_username()

        sessions[chat_id]["account_info"] = {"email": email, "username": username, "password": password}
        sessions[chat_id]["temp_email_info"] = temp_info

        await bot.send_message(
            chat_id,
            f"✅ Temp email: `{email}`\n👤 User: `{username}`\n🔐 Pass: `{password}`\n\n⏳ Loading GitHub signup..."
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

        # Start signup
        res = await github_signup(page, email, username, password, chat_id, send_fn, bot)

        if res["status"] == "success":
            await _gen_token_flow(bot, chat_id)
        elif res["status"] == "waiting_for_slider":
            # User is solving slider, will send message when done
            await bot.send_message(
                chat_id,
                "📌 *Waiting for you to solve the slider.*\n"
                "Open the URL in your browser, solve the slider, then send any message here.",
                parse_mode="Markdown"
            )
        elif res["status"] == "waiting_for_text_captcha":
            # User is solving text CAPTCHA
            pass
        elif res["status"] == "verify":
            sessions[chat_id]["stage"] = "email_verify"
            await bot.send_message(chat_id, "📧 Verification email sent. Checking inbox...")
            await handle_verification(bot, chat_id)
        else:
            await bot.send_message(chat_id, f"❌ Error: {res.get('message')}")
            await _clean_session(chat_id)
        return

    # ============================================================
    # DEFAULT RESPONSE
    # ============================================================
    if not sd or not sd.get("in_progress"):
        await bot.send_message(chat_id, "No active process. Start with /create")

# ============================================================================
# MAIN LOOP
# ============================================================================

async def main_loop():
    print("🚀 GitHub Token Bot starting... (SLIDER CAPTCHA via Telegram)")
    print("📌 This bot supports both slider and text CAPTCHAs")
    print("📌 User solves CAPTCHA manually in their own browser")
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
                    asyncio.create_task(handle_command(bot, chat_id, text))
        except Exception as e:
            logger.error(f"Main loop error: {e}")
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("Shutting down...")
