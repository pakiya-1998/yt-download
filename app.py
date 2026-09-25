import streamlit as st
from pathlib import Path
import tempfile
import time
import re

st.set_page_config(page_title="AMP4 Cookie Test", page_icon="🧪")

st.title("🧪 AMP4 Cookie Test")
st.warning(
    "Use this only for videos you are authorized to download and only for personal/noncommercial "
    "testing where AMP4 permits automated use. Do not paste cookie values into chat."
)

st.markdown("""
This test loads **your own AMP4 cookies.txt locally** and opens AMP4 in a browser session.
It does **not** bypass CAPTCHA, anti-bot checks, or YouTube restrictions.
""")

cookie_file = st.file_uploader(
    "Upload AMP4 cookies.txt (Netscape format)",
    type=["txt", "cookies"],
)

url = st.text_input(
    "YouTube URL",
    placeholder="https://www.youtube.com/watch?v=..."
)

headless = st.checkbox("Run browser headless", value=True)

def parse_netscape_cookies(raw: str):
    cookies = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue

        domain, include_subdomains, path, secure, expires, name, value = parts
        domain = domain.lstrip(".")
        try:
            expires_num = int(float(expires))
        except Exception:
            expires_num = 0

        item = {
            "name": name,
            "value": value,
            "domain": "." + domain if not domain.startswith(".") else domain,
            "path": path or "/",
        }
        if secure.upper() == "TRUE":
            item["secure"] = True
        if expires_num > 0:
            item["expires"] = expires_num
        cookies.append(item)

    return cookies

def run_test(cookie_bytes: bytes, youtube_url: str, is_headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        st.error("Playwright is not installed. Install it with: pip install playwright")
        return

    raw = cookie_bytes.decode("utf-8", errors="replace")
    cookies = parse_netscape_cookies(raw)

    if not cookies:
        st.error("No valid Netscape cookies were found.")
        return

    amp4_cookies = [
        c for c in cookies
        if "amp4.cc" in c.get("domain", "")
    ]

    if not amp4_cookies:
        st.warning(
            "No amp4.cc cookies were found in the uploaded file. "
            "The file may contain cookies for another domain."
        )

    # Never display cookie names/values.
    st.write(f"Loaded {len(amp4_cookies)} AMP4 cookie entries locally.")

    with sync_playwright() as p:
        # Prefer Streamlit Cloud / Linux system Chromium when available.
        # Otherwise Playwright's bundled Chromium is used.
        chromium_candidates = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ]
        executable = next((x for x in chromium_candidates if Path(x).exists()), None)

        launch_kwargs = {"headless": is_headless}
        if executable:
            launch_kwargs["executable_path"] = executable

        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            accept_downloads=True,
            ignore_https_errors=False,
        )

        # Only inject AMP4 cookies; never send unrelated cookies to AMP4.
        if amp4_cookies:
            context.add_cookies(amp4_cookies)

        page = context.new_page()

        status = st.empty()
        status.info("Opening AMP4…")
        page.goto("https://amp4.cc/", wait_until="domcontentloaded", timeout=60000)

        status.info("AMP4 opened. Checking session/cookie state…")
        time.sleep(2)

        title = page.title()
        current_url = page.url

        st.success("AMP4 page opened.")
        st.write("Page title:", title)
        st.write("Current URL:", current_url)

        if youtube_url:
            status.info("Submitting the YouTube URL…")

            # AMP4 currently exposes a YouTube URL input on the page.
            inputs = page.locator("input")
            target = None

            for i in range(min(inputs.count(), 20)):
                el = inputs.nth(i)
                try:
                    ph = (el.get_attribute("placeholder") or "").lower()
                    typ = (el.get_attribute("type") or "").lower()
                    value = (el.get_attribute("value") or "").lower()
                    if (
                        "youtube" in ph
                        or "url" in ph
                        or typ == "url"
                        or "youtube.com" in value
                    ):
                        target = el
                        break
                except Exception:
                    pass

            if target is None and inputs.count() > 0:
                target = inputs.nth(0)

            if target is None:
                st.error("Could not identify the AMP4 URL input.")
            else:
                target.fill(youtube_url)

                # Find a likely download/submit button.
                buttons = page.get_by_role("button")
                clicked = False

                for i in range(min(buttons.count(), 30)):
                    b = buttons.nth(i)
                    try:
                        txt = (b.inner_text() or "").strip().lower()
                        if any(x in txt for x in ["download", "convert", "start"]):
                            b.click()
                            clicked = True
                            break
                    except Exception:
                        pass

                if not clicked:
                    st.warning(
                        "URL was filled, but no download/convert button was identified automatically. "
                        "You can use the opened browser manually."
                    )
                else:
                    status.info("AMP4 conversion request submitted. Waiting for response…")

                    # Do not try to bypass CAPTCHA. Just observe normal page state.
                    deadline = time.time() + 90
                    while time.time() < deadline:
                        time.sleep(2)
                        txt = page.locator("body").inner_text().lower()

                        if "captcha" in txt:
                            st.warning(
                                "AMP4 requested a CAPTCHA. The test will not bypass it."
                            )
                            break

                        if "conversion failed" in txt or "failed" in txt:
                            st.error("AMP4 reported a conversion failure.")
                            break

                        if "download" in txt and (
                            "completed" in txt or "ready" in txt
                        ):
                            st.success("AMP4 appears to have completed the conversion.")
                            break
                    else:
                        st.info(
                            "No final result detected within 90 seconds. "
                            "AMP4 may still be processing or may have changed its page flow."
                        )

        # Save a screenshot without exposing cookie values.
        screenshot_path = Path(tempfile.gettempdir()) / "amp4_test.png"
        page.screenshot(path=str(screenshot_path), full_page=False)
        st.image(str(screenshot_path), caption="AMP4 test page")

        browser.close()

if st.button("🚀 Test AMP4 with Cookies", type="primary"):
    if not cookie_file:
        st.error("Please upload your own AMP4 cookies.txt.")
    elif not url.strip():
        st.error("Please enter a YouTube URL.")
    else:
        run_test(cookie_file.getvalue(), url.strip(), headless)
