import streamlit as st
import requests
import re
import json
from urllib.parse import urljoin
from http.cookiejar import Cookie
from bs4 import BeautifulSoup

st.set_page_config(
    page_title="AMP4 HTTP Cookie Test",
    page_icon="🧪",
    layout="centered"
)

st.title("🧪 AMP4 HTTP Cookie Test")

st.warning(
    "Use this only for videos you are authorized to download and where "
    "AMP4 permits the intended use. Never paste cookie values into chat."
)

st.write(
    "This version does NOT use Playwright, Selenium, Chromium, or browser drivers."
)

# ---------------------------------------------------------
# COOKIE PARSER
# ---------------------------------------------------------

def load_netscape_cookies(uploaded_file):
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

        domain = parts[0]
        include_subdomains = parts[1]
        path = parts[2]
        secure = parts[3]
        expires = parts[4]
        name = parts[5]
        value = parts[6]

        if not domain:
            continue

        # Only AMP4 cookies
        if "amp4.cc" not in domain.lower():
            continue

        try:
            expires_int = int(expires)
        except Exception:
            expires_int = 0

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
            expires=expires_int if expires_int > 0 else None,
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )

        session.cookies.set_cookie(cookie)

        count += 1

    return session, count


# ---------------------------------------------------------
# AMP4 PAGE
# ---------------------------------------------------------

def get_amp4_page(session):

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://amp4.cc/",
    }

    response = session.get(
        "https://amp4.cc/",
        headers=headers,
        timeout=30,
        allow_redirects=True
    )

    return response


# ---------------------------------------------------------
# FIND POSSIBLE API ENDPOINTS
# ---------------------------------------------------------

def find_endpoints(html):

    endpoints = set()

    # Absolute URLs
    absolute = re.findall(
        r'https?://[^"\']+',
        html
    )

    for item in absolute:
        if "amp4.cc" in item:
            endpoints.add(item)

    # Relative API-looking paths
    relative = re.findall(
        r'["\'](\/(?:api|ajax|convert|download|youtube|process|convert-url)[^"\']*)["\']',
        html,
        flags=re.I
    )

    for item in relative:
        endpoints.add(urljoin("https://amp4.cc/", item))

    return sorted(endpoints)


# ---------------------------------------------------------
# FIND FORMS
# ---------------------------------------------------------

def inspect_forms(html):

    soup = BeautifulSoup(html, "html.parser")

    forms = []

    for form in soup.find_all("form"):

        action = form.get("action") or "/"
        method = (form.get("method") or "GET").upper()

        fields = []

        for inp in form.find_all(["input", "select", "textarea"]):

            name = inp.get("name")

            if name:
                fields.append({
                    "name": name,
                    "type": inp.get("type", ""),
                    "value_present": bool(inp.get("value"))
                })

        forms.append({
            "action": urljoin("https://amp4.cc/", action),
            "method": method,
            "fields": fields
        })

    return forms


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

cookies_file = st.file_uploader(
    "Upload AMP4 cookies.txt",
    type=["txt", "cookies"]
)

youtube_url = st.text_input(
    "YouTube URL",
    placeholder="https://www.youtube.com/watch?v=..."
)

if st.button("🚀 Test AMP4", type="primary"):

    if not cookies_file:
        st.error("Please upload your AMP4 cookies.txt")
        st.stop()

    if not youtube_url.strip():
        st.error("Please enter a YouTube URL")
        st.stop()

    try:

        # -------------------------------------------------
        # LOAD COOKIES
        # -------------------------------------------------

        session, cookie_count = load_netscape_cookies(
            cookies_file
        )

        st.info(
            f"Loaded {cookie_count} AMP4 cookies locally."
        )

        # -------------------------------------------------
        # OPEN AMP4
        # -------------------------------------------------

        with st.spinner("Connecting to AMP4..."):

            response = get_amp4_page(session)

        st.write("### 1. AMP4 Connection")

        if response.status_code == 200:
            st.success(
                f"AMP4 connection OK — HTTP {response.status_code}"
            )
        else:
            st.error(
                f"AMP4 returned HTTP {response.status_code}"
            )

        st.write(
            "Final URL:",
            response.url
        )

        # -------------------------------------------------
        # CHECK PAGE
        # -------------------------------------------------

        html = response.text

        st.write("### 2. AMP4 Page Analysis")

        if "youtube" in html.lower():
            st.success(
                "YouTube converter interface detected."
            )
        else:
            st.warning(
                "YouTube converter interface was not clearly detected."
            )

        # -------------------------------------------------
        # CHECK FOR CAPTCHA
        # -------------------------------------------------

        lower_html = html.lower()

        captcha_words = [
            "captcha",
            "recaptcha",
            "hcaptcha",
            "cloudflare"
        ]

        detected = [
            x for x in captcha_words
            if x in lower_html
        ]

        if detected:

            st.warning(
                "Anti-bot/CAPTCHA related content detected: "
                + ", ".join(detected)
            )

            st.info(
                "This test does not bypass CAPTCHA or anti-bot protection."
            )

        # -------------------------------------------------
        # FIND FORMS
        # -------------------------------------------------

        forms = inspect_forms(html)

        st.write("### 3. HTML Forms")

        if forms:

            for i, form in enumerate(forms, 1):

                with st.expander(
                    f"Form {i}"
                ):

                    st.write(
                        "Action:",
                        form["action"]
                    )

                    st.write(
                        "Method:",
                        form["method"]
                    )

                    st.json(
                        form["fields"]
                    )

        else:

            st.info(
                "No normal HTML form detected."
            )

        # -------------------------------------------------
        # FIND POSSIBLE API ENDPOINTS
        # -------------------------------------------------

        endpoints = find_endpoints(html)

        st.write("### 4. Possible AMP4 endpoints")

        if endpoints:

            for endpoint in endpoints:

                # Do not expose cookie values.
                st.code(endpoint)

        else:

            st.info(
                "No obvious API/conversion endpoint was found "
                "in the initial HTML."
            )

        # -------------------------------------------------
        # SEARCH JAVASCRIPT
        # -------------------------------------------------

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        scripts = []

        for script in soup.find_all("script", src=True):

            src = urljoin(
                response.url,
                script.get("src")
            )

            scripts.append(src)

        st.write("### 5. JavaScript files")

        if scripts:

            st.write(
                f"Detected {len(scripts)} JavaScript files."
            )

            for src in scripts[:20]:

                st.code(src)

        else:

            st.info(
                "No external JavaScript files detected."
            )

        # -------------------------------------------------
        # IMPORTANT RESULT
        # -------------------------------------------------

        st.write("### 6. Result")

        st.success(
            "AMP4 HTTP session test completed."
        )

        st.info(
            "If AMP4 requires a JavaScript-generated API request, "
            "this HTTP-only version will show the page/endpoints "
            "but will not bypass browser/anti-bot protections."
        )

    except requests.RequestException as e:

        st.error(
            f"Network error: {e}"
        )

    except Exception as e:

        st.error(
            f"Unexpected error: {e}"
        )
