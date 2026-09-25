import streamlit as st
import requests
import re
from html.parser import HTMLParser
from urllib.parse import urljoin
from http.cookiejar import Cookie

st.set_page_config(page_title="AMP4 HTTP Cookie Test", page_icon="🧪")
st.title("🧪 AMP4 HTTP Cookie Test")

st.warning(
    "Use this only for videos you are authorized to download and where AMP4 permits the intended use. "
    "Never paste cookie values into chat."
)
st.write("Lightweight HTTP-only test: no Playwright, Selenium, Chromium, or browser driver.")


class AMP4Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.scripts = []
        self.current_form = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        tag = tag.lower()

        if tag == "form":
            self.current_form = {
                "action": a.get("action") or "/",
                "method": (a.get("method") or "GET").upper(),
                "fields": []
            }
            self.forms.append(self.current_form)

        elif tag in ("input", "select", "textarea") and self.current_form:
            name = a.get("name")
            if name:
                self.current_form["fields"].append({
                    "name": name,
                    "type": a.get("type", ""),
                    "value_present": bool(a.get("value"))
                })

        elif tag == "script" and a.get("src"):
            self.scripts.append(a["src"])


def load_amp4_cookies(uploaded_file):
    raw = uploaded_file.getvalue().decode("utf-8", errors="replace")
    session = requests.Session()
    count = 0

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue

        line = line.replace("#HttpOnly_", "", 1)
        parts = line.split("\t")
        if len(parts) != 7:
            continue

        domain, _, path, secure, expires, name, value = parts

        if "amp4.cc" not in domain.lower():
            continue

        try:
            exp = int(float(expires))
        except Exception:
            exp = None

        cookie = Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path=path or "/",
            path_specified=True,
            secure=secure.upper() == "TRUE",
            expires=exp if exp and exp > 0 else None,
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
        session.cookies.set_cookie(cookie)
        count += 1

    return session, count


def headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://amp4.cc/",
    }


def find_endpoints(html):
    found = set()

    for x in re.findall(r'https?://[^"\'\s<>]+', html, re.I):
        if "amp4.cc" in x.lower():
            found.add(x)

    for x in re.findall(
        r'["\'](\/(?:api|ajax|convert|download|youtube|process|convert-url)[^"\']*)["\']',
        html,
        re.I,
    ):
        found.add(urljoin("https://amp4.cc/", x))

    return sorted(found)


cookies = st.file_uploader(
    "Upload AMP4 cookies.txt",
    type=["txt", "cookies"]
)

youtube_url = st.text_input(
    "YouTube URL",
    placeholder="https://www.youtube.com/watch?v=..."
)

if st.button("🚀 Test AMP4", type="primary"):
    if not cookies:
        st.error("Please upload your AMP4 cookies.txt")
        st.stop()

    if not youtube_url.strip():
        st.error("Please enter a YouTube URL")
        st.stop()

    try:
        session, count = load_amp4_cookies(cookies)
        st.info(f"Loaded {count} AMP4 cookies locally.")

        with st.spinner("Connecting to AMP4..."):
            r = session.get(
                "https://amp4.cc/",
                headers=headers(),
                timeout=30,
                allow_redirects=True,
            )

        st.write("### 1. AMP4 Connection")

        if r.status_code == 200:
            st.success(f"AMP4 connection OK — HTTP {r.status_code}")
        else:
            st.warning(f"AMP4 returned HTTP {r.status_code}")

        st.write("Final URL:", r.url)

        html = r.text
        lower = html.lower()

        st.write("### 2. Page Analysis")

        if "youtube" in lower:
            st.success("YouTube-related interface detected.")
        else:
            st.warning("YouTube-related interface was not clearly detected.")

        anti_bot = [
            x for x in (
                "captcha", "recaptcha", "hcaptcha",
                "cloudflare", "challenge-platform"
            ) if x in lower
        ]

        if anti_bot:
            st.warning("Anti-bot/CAPTCHA content detected: " + ", ".join(anti_bot))
            st.info("This test does not bypass CAPTCHA or anti-bot protection.")

        parser = AMP4Parser()
        parser.feed(html)

        st.write("### 3. HTML Forms")

        if parser.forms:
            for i, form in enumerate(parser.forms, 1):
                with st.expander(f"Form {i}"):
                    st.write("Action:", urljoin(r.url, form["action"]))
                    st.write("Method:", form["method"])
                    st.json(form["fields"])
        else:
            st.info("No normal HTML form detected.")

        st.write("### 4. Possible AMP4 Endpoints")

        endpoints = find_endpoints(html)

        if endpoints:
            for endpoint in endpoints[:50]:
                st.code(endpoint)
        else:
            st.info("No obvious API/conversion endpoint found in initial HTML.")

        st.write("### 5. JavaScript Files")

        if parser.scripts:
            st.write(f"Detected {len(parser.scripts)} external JavaScript files.")
            for src in parser.scripts[:30]:
                st.code(urljoin(r.url, src))
        else:
            st.info("No external JavaScript files detected.")

        st.write("### 6. Result")
        st.success("AMP4 HTTP session test completed.")
        st.info(
            "This version only makes normal HTTP requests. It does not bypass "
            "CAPTCHA, anti-bot protection, login controls, or other access restrictions."
        )

    except requests.RequestException as e:
        st.error(f"Network error: {e}")
    except Exception as e:
        st.error(f"Unexpected error: {e}")
