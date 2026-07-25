#!/usr/bin/env python3
"""
GitHub Token Generator Bot — RAILWAY OPTIMIZED (No Proxies)
=============================================================
Uses Railway's IP rotation on restart + smart delays
"""

import asyncio
import logging
import os
import random
import re
import string
import time
import json
import requests
from datetime import datetime

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

# Railway specific settings
RAILWAY_DEPLOYMENT = os.getenv("RAILWAY_DEPLOYMENT", "false") == "true"
if RAILWAY_DEPLOYMENT:
    SCREENSHOT_DIR = "/tmp/screenshots"
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# ============================================================================
# CRITICAL: RAILWAY IP ROTATION STRATEGY
# ============================================================================

class RailwayIPManager:
    """
    Manages IP rotation on Railway by tracking restart count
    Railway assigns new IP on each restart/deployment
    """
    def __init__(self):
        self.restart_count = 0
        self.last_restart = time.time()
        self.max_attempts_per_ip = 2  # Max attempts before triggering restart
    
    def should_restart(self):
        """Check if we should restart to get new IP"""
        self.restart_count += 1
        if self.restart_count >= self.max_attempts_per_ip:
            return True
        return False
    
    def record_attempt(self, success):
        """Record the result of an attempt"""
        if success:
            self.restart_count = 0  # Reset if successful
        else:
            self.restart_count += 1

ip_manager = RailwayIPManager()

# ============================================================================
# SMART DELAYS TO AVOID DETECTION
# ============================================================================

async def smart_delay(min_seconds=2, max_seconds=8):
    """Human-like random delays"""
    delay = random.uniform(min_seconds, max_seconds)
    await asyncio.sleep(delay)

async def type_like_human(page, selector, text):
    """Type text like a human with random delays"""
    element = page.locator(selector)
    await element.click()
    await asyncio.sleep(random.uniform(0.3, 0.7))
    
    # Type character by character
    for char in text:
        await element.type(char, delay=random.uniform(0.08, 0.25))
        # Random pause between chars
        if random.random() < 0.3:
            await asyncio.sleep(random.uniform(0.05, 0.15))
    
    await asyncio.sleep(random.uniform(0.5, 1.5))

# ============================================================================
# LAUNCH BROWSER (No Proxy, Just Stealth)
# ============================================================================

async def launch_browser(playwright):
    """Launch browser with maximum stealth - no proxy needed"""
    
    # Railway's Chromium path
    chrome_paths = [
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    
    chrome_path = None
    for path in chrome_paths:
        if os.path.exists(path):
            chrome_path = path
            break
    
    if not chrome_path:
        chrome_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "/usr/bin/google-chrome-stable")
    
    launch_args = {
        "headless": True,
        "executable_path": chrome_path,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-features=BlockInsecurePrivateNetworkRequests",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-extensions",
            "--disable-plugins",
            "--disable-images",  # Faster loading
            "--disable-javascript",  # Sometimes helps
            "--disable-notifications",
            "--disable-popup-blocking",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-translate",
            "--disable-session-crashed-bubble",
            "--disable-component-extensions-with-background-pages",
            "--disable-client-side-phishing-detection",
            "--disable-crash-reporter",
            "--disable-component-update",
            "--disable-domain-reliability",
            "--disable-oauth-anonymous-metrics",
            "--disable-print-preview",
            "--disable-prompt-on-repost",
            "--disable-speech-api",
            "--disable-voice-input",
            "--disable-webgl",
            "--disable-webrtc",
            "--ignore-certificate-errors",
            "--ignore-certificate-errors-spki-list",
            "--ignore-ssl-errors",
            "--ignore-certificate-errors",
            "--disable-breakpad",
            "--no-default-browser-check",
            "--no-first-run",
            "--no-crash-upload",
            "--no-pings",
            "--no-zygote",
            "--mute-audio",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ],
    }
    
    # Random user agent to avoid detection
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    ]
    
    browser = await playwright.chromium.launch(**launch_args)
    
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=random.choice(user_agents),
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
    )
    
    # Apply stealth
    stealth = Stealth()
    await stealth.apply_stealth_async(context)
    
    return browser, context

# ============================================================================
# HANDLE IP BLOCK BY RESTARTING DEPLOYMENT
# ============================================================================

async def handle_ip_block(chat_id, bot):
    """
    When IP is blocked, trigger a restart on Railway
    Railway will assign a new IP on restart
    """
    await bot.send_message(
        chat_id,
        "🚫 *IP Blocked!*\n\n"
        "Restarting Railway deployment to get new IP...\n"
        "This takes about 30 seconds.\n\n"
        "The bot will automatically restart with a new IP.",
        parse_mode="Markdown"
    )
    
    # Trigger restart via exit
    # Railway will automatically restart the service
    logging.warning("IP blocked - restarting deployment...")
    
    # Exit with specific code to trigger restart
    os._exit(1)

# ============================================================================
# SMART SIGNUP WITH RETRY ON BLOCK
# ============================================================================

async def github_signup(page, email, username, password, chat_id, send_fn, bot):
    """
    Signup with smart retry and IP block handling
    """
    max_attempts = 3
    
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                await send_fn(f"🔄 Attempt {attempt+1}/{max_attempts}...")
                await smart_delay(5, 10)  # Wait longer between attempts
                
                # Refresh with new identity
                await page.goto("https://github.com", wait_until="domcontentloaded")
                await smart_delay(2, 5)

            # Go to signup
            await send_fn("🌐 Loading GitHub signup...")
            await page.goto("https://github.com/signup", timeout=90000, wait_until="domcontentloaded")
            await smart_delay(3, 6)

            # Check for IP block
            content = await page.content()
            if "Access is temporarily restricted" in content:
                await send_fn("🚫 IP Block detected! Restarting...")
                await handle_ip_block(chat_id, bot)
                return {"status": "restarting"}
            
            # Check for slider CAPTCHA
            if "slide right" in content.lower() or "verification required" in content.lower():
                return await handle_slider_captcha(page, chat_id, bot)
            
            # Check for text CAPTCHA
            if "captcha" in content.lower() or "verify" in content.lower():
                return await handle_text_captcha(page, chat_id, bot)

            # ----- Fill Form -----
            await send_fn("✏️ Entering email...")
            email_field = page.locator('input[name="user[email]"]')
            if await email_field.count() == 0:
                email_field = page.locator('#email')
            await type_like_human(page, email_field, email)
            
            await smart_delay(1, 3)
            
            await send_fn("🔑 Entering password...")
            pwd_field = page.locator('input[name="user[password]"]')
            if await pwd_field.count() == 0:
                pwd_field = page.locator('#password')
            await type_like_human(page, pwd_field, password)
            
            await smart_delay(1, 3)
            
            await send_fn("👤 Entering username...")
            user_field = page.locator('input[name="user[login]"]')
            if await user_field.count() == 0:
                user_field = page.locator('#login')
            await type_like_human(page, user_field, username)
            
            await smart_delay(2, 4)

            # ----- Submit -----
            await send_fn("🚀 Creating account...")
            submit_btn = page.locator('button[type="submit"]')
            if await submit_btn.count() > 0:
                await submit_btn.first.click()
            else:
                await page.keyboard.press("Enter")
            
            await smart_delay(5, 8)

            # ----- Check result -----
            curr_url = page.url
            content = await page.content()
            
            # Check for CAPTCHA/Slider after submit
            if "slide right" in content.lower() or "verification required" in content.lower():
                return await handle_slider_captcha(page, chat_id, bot)
            
            if "captcha" in content.lower() or "verify" in curr_url.lower():
                return await handle_text_captcha(page, chat_id, bot)
            
            # Check if we're on a real page
            if "github.com" in curr_url and "login" not in curr_url:
                return {"status": "success"}
            
            # Check for success indicators
            if "Welcome to GitHub" in content:
                return {"status": "success"}

            return {"status": "unknown", "url": curr_url}
            
        except Exception as e:
            error_msg = str(e)
            if "Access is temporarily restricted" in error_msg:
                await handle_ip_block(chat_id, bot)
                return {"status": "restarting"}
            
            if attempt < max_attempts - 1:
                await send_fn(f"⚠️ Attempt {attempt+1} failed: {error_msg[:50]}. Retrying...")
                # Clear cookies and state before retry
                try:
                    await page.context.clear_cookies()
                except:
                    pass
                continue
            return {"status": "error", "message": error_msg}
    
    return {"status": "error", "message": "All attempts failed"}

# ============================================================================
# CAPTCHA HANDLERS (Same as before)
# ============================================================================

async def handle_slider_captcha(page, chat_id, bot):
    """Handle slider CAPTCHA by sending to user"""
    ss_path = f"{SCREENSHOT_DIR}/slider_{chat_id}_{int(time.time())}.png"
    await page.screenshot(path=ss_path, full_page=True)
    page_url = page.url
    
    await bot.send_photo(
        chat_id,
        ss_path,
        caption="🔒 *SLIDER CAPTCHA DETECTED!*\n\n"
                "Please solve manually:\n"
                "1️⃣ Click the link below to open in YOUR browser\n"
                "2️⃣ Solve the slider CAPTCHA\n"
                "3️⃣ After solving, send ANY message here\n\n"
                f"🔗 [Open CAPTCHA Page]({page_url})",
        parse_mode="Markdown"
    )
    
    sessions[chat_id]["stage"] = "slider_solving"
    sessions[chat_id]["slider_page"] = page
    sessions[chat_id]["slider_url"] = page_url
    sessions[chat_id]["slider_start_time"] = time.time()
    
    return {"status": "waiting_for_slider"}

async def handle_text_captcha(page, chat_id, bot):
    """Handle text CAPTCHA by sending to user"""
    ss_path = f"{SCREENSHOT_DIR}/captcha_{chat_id}_{int(time.time())}.png"
    await page.screenshot(path=ss_path, full_page=True)
    
    await bot.send_photo(
        chat_id,
        ss_path,
        caption="🔒 *TEXT CAPTCHA Detected!*\n\n"
                "Type the CAPTCHA text here:\n"
                "📝 Example: If shows 'ABC123', send: `ABC123`",
        parse_mode="Markdown"
    )
    
    sessions[chat_id]["stage"] = "text_captcha_solving"
    sessions[chat_id]["captcha_page"] = page
    
    return {"status": "waiting_for_text_captcha"}

# ============================================================================
# HEALTH CHECK FOR RAILWAY
# ============================================================================

async def health_check_endpoint():
    """Simple health check for Railway"""
    return {"status": "healthy", "time": time.time()}

# ============================================================================
# MAIN COMMAND HANDLER (Updated)
# ============================================================================

async def handle_command(bot, chat_id, text):
    # ... [Your existing command handlers]
    
    if text == "/create":
        await _clean_session(chat_id)
        
        # Check if we should restart (IP rotation)
        if ip_manager.should_restart():
            await bot.send_message(
                chat_id,
                "🔄 Rotating IP... Restarting service."
            )
            os._exit(1)
        
        # ... [Rest of your /create logic]
        
        # Record attempt result
        # If success, reset counter; if fail, increment
        
        return

# ============================================================================
# RAILWAY DEPLOYMENT FILES
# ============================================================================

# ============================================================================
# 1. RAILWAY.JSON
# ============================================================================

"""
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "NIXPACKS",
    "buildCommand": "pip install -r requirements.txt && playwright install chromium"
  },
  "deploy": {
    "numReplicas": 1,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10,
    "healthcheckPath": "/health",
    "healthcheckTimeout": 300
  }
}
"""

# ============================================================================
# 2. NIXPACKS.TOML
# ============================================================================

"""
[providers]
chromium = "latest"

[phases.setup]
nixPkgs = ["chromium"]

[phases.install]
cmds = ["pip install -r requirements.txt"]

[phases.start]
cmd = "python3 bot.py"
"""

# ============================================================================
# 3. REQUIREMENTS.TXT
# ============================================================================

"""
playwright==1.40.0
playwright-stealth==1.0.6
requests==2.31.0
"""

# ============================================================================
# 4. DOCKERFILE (Alternative)
# ============================================================================

"""
FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/usr/bin
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY . .

CMD ["python3", "bot.py"]
"""

# ============================================================================
# MAIN LOOP
# ============================================================================

if __name__ == "__main__":
    # ... [Your existing main loop]