import asyncio
import os
import time
from playwright.async_api import async_playwright
from playwright_stealth import Stealth


def convert_cookies_to_netscape(cookies: list) -> str:
    lines = [
        "# Netscape HTTP Cookie File",
        "# http://curl.haxx.se/rfc/cookie_spec.html",
        "# This is a generated file! Do not edit.\n",
    ]

    for c in cookies:
        domain = c.get("domain", "")
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"

        expires = int(c.get("expires", 0))
        if expires <= 0:
            expires = 0

        name = c.get("name", "")
        value = c.get("value", "")

        lines.append(
            f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires}\t{name}\t{value}"
        )

    return "\n".join(lines)


async def generate_youtube_cookies(output_filepath: str = "input/media/yt/cookies.txt", max_age_hours: int = 12, force: bool = False) -> str:
    """
    توليد الكوكيز مع ميزة التخطي إذا كان الملف موجوداً وحديثاً.
    """
    # ميزة الـ Skip: التحقق مما إذا كان الملف موجوداً وضمن المدة المسموحة
    if not force and os.path.exists(output_filepath):
        file_age_hours = (time.time() - os.path.getmtime(output_filepath)) / 3600
        if file_age_hours < max_age_hours:
            print(f"[!] Cookie file is fresh (age: {round(file_age_hours, 1)}h < {max_age_hours}h). Skipping cookie generation.")
            return output_filepath

    os.makedirs(os.path.dirname(output_filepath), exist_ok=True)

    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        page = await context.new_page()

        print("[*] Fetching YouTube session cookies via Playwright Stealth...")
        await page.goto("https://www.youtube.com", wait_until="networkidle")

        cookies = await context.cookies()
        netscape_content = convert_cookies_to_netscape(cookies)

        with open(output_filepath, "w", encoding="utf-8") as f:
            f.write(netscape_content)

        print(f"[+] Cookies successfully generated and saved to: {output_filepath}")
        await browser.close()
        return output_filepath


def update_cookies_sync(output_filepath: str = "input/media/yt/cookies/cookies.txt", max_age_hours: int = 12, force: bool = False):
    return asyncio.run(generate_youtube_cookies(output_filepath, max_age_hours, force))


if __name__ == "__main__":
    update_cookies_sync()