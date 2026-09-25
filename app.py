import streamlit as st
import requests
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from http.cookiejar import Cookie


# =========================================================
# PAGE
# =========================================================

st.set_page_config(
    page_title="AMP4 Downloader Test",
    page_icon="🚀",
    layout="centered"
)

st.title("🚀 AMP4 Downloader Test")

st.warning(
    "Use this only for videos you are authorized to download "
    "and where AMP4 permits the intended use. "
    "Do not paste cookie values into chat."
)

st.caption(
    "HTTP-only version — no Playwright, Selenium, Chromium or browser driver."
)


# =========================================================
# HTML PARSER
# =========================================================

class AMP4Parser(HTMLParser):

    def __init__(self):
        super().__init__()

        self.forms = []
        self.links = []
        self.scripts = []

        self.current_form = None

    def handle_starttag(self, tag, attrs):

        attrs = dict(attrs)

        tag = tag.lower()

        # -----------------------------
        # FORM
        # -----------------------------

        if tag == "form":

            self.current_form = {
                "action": attrs.get("action") or "/",
                "method": (
                    attrs.get("method") or "GET"
                ).upper(),
                "fields": []
            }

            self.forms.append(
                self.current_form
            )

        # -----------------------------
        # FORM FIELDS
        # -----------------------------

        elif tag in (
            "input",
            "select",
            "textarea"
        ):

            if self.current_form:

                name = attrs.get("name")

                if name:

                    self.current_form[
                        "fields"
                    ].append(
                        {
                            "name": name,
                            "type": attrs.get(
                                "type",
                                ""
                            ),
                            "value": attrs.get(
                                "value",
                                ""
                            )
                        }
                    )

        # -----------------------------
        # LINKS
        # -----------------------------

        elif tag == "a":

            href = attrs.get("href")

            if href:

                self.links.append(
                    href
                )

        # -----------------------------
        # JAVASCRIPT
        # -----------------------------

        elif tag == "script":

            src = attrs.get("src")

            if src:

                self.scripts.append(
                    src
                )


# =========================================================
# COOKIE LOADER
# =========================================================

def load_amp4_cookies(uploaded_file):

    raw = uploaded_file.getvalue().decode(
        "utf-8",
        errors="replace"
    )

    session = requests.Session()

    count = 0

    for line in raw.splitlines():

        line = line.strip()

        if not line:
            continue

        # Skip comments
        if (
            line.startswith("#")
            and not line.startswith("#HttpOnly_")
        ):
            continue

        line = line.replace(
            "#HttpOnly_",
            "",
            1
        )

        parts = line.split("\t")

        if len(parts) != 7:
            continue

        (
            domain,
            include_subdomains,
            path,
            secure,
            expires,
            name,
            value
        ) = parts

        # Only AMP4 cookies
        if "amp4.cc" not in domain.lower():
            continue

        try:
            expires_value = int(
                float(expires)
            )
        except Exception:
            expires_value = None

        cookie = Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=(
                domain.startswith(".")
            ),
            path=path or "/",
            path_specified=True,
            secure=(
                secure.upper() == "TRUE"
            ),
            expires=(
                expires_value
                if expires_value
                and expires_value > 0
                else None
            ),
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )

        session.cookies.set_cookie(
            cookie
        )

        count += 1

    return session, count


# =========================================================
# HEADERS
# =========================================================

def get_headers():

    return {

        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/153.0.0.0 "
            "Safari/537.36"
        ),

        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,"
            "image/webp,"
            "*/*;q=0.8"
        ),

        "Accept-Language":
            "en-US,en;q=0.9",

        "Cache-Control":
            "no-cache",

        "Pragma":
            "no-cache",

        "Referer":
            "https://amp4.cc/"
    }


# =========================================================
# GET AMP4 PAGE
# =========================================================

def get_amp4(session):

    response = session.get(
        "https://amp4.cc/",
        headers=get_headers(),
        timeout=30,
        allow_redirects=True
    )

    return response


# =========================================================
# FIND YOUTUBE FIELD
# =========================================================

def find_youtube_field(form):

    candidates = [

        "url",
        "video_url",
        "youtube_url",
        "youtube",
        "link",
        "video",
        "input"
    ]

    fields = form.get(
        "fields",
        []
    )

    # Exact/strong match
    for field in fields:

        name = field[
            "name"
        ].lower()

        if name in candidates:

            return field[
                "name"
            ]

    # Partial match
    for field in fields:

        name = field[
            "name"
        ].lower()

        if (
            "url" in name
            or "youtube" in name
            or "video" in name
            or "link" in name
        ):

            return field[
                "name"
            ]

    return None


# =========================================================
# FIND DOWNLOAD LINKS
# =========================================================

def find_download_links(
    html,
    base_url
):

    found = []

    # href URLs
    hrefs = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        html,
        flags=re.I
    )

    # src URLs
    srcs = re.findall(
        r'src\s*=\s*["\']([^"\']+)["\']',
        html,
        flags=re.I
    )

    # Direct MP4/WebM
    for url in hrefs + srcs:

        absolute = urljoin(
            base_url,
            url
        )

        lower = absolute.lower()

        if (
            ".mp4" in lower
            or ".webm" in lower
            or "download" in lower
            or "file=" in lower
            or "download_url" in lower
        ):

            if absolute not in found:

                found.append(
                    absolute
                )

    # JSON-style URLs
    json_urls = re.findall(
        r'["\'](https?://[^"\']+)["\']',
        html,
        flags=re.I
    )

    for url in json_urls:

        lower = url.lower()

        if (
            ".mp4" in lower
            or ".webm" in lower
            or "download" in lower
        ):

            if url not in found:

                found.append(
                    url
                )

    return found


# =========================================================
# DETECT ANTI BOT
# =========================================================

def detect_antibot(html):

    text = html.lower()

    checks = [

        "captcha",

        "recaptcha",

        "hcaptcha",

        "cloudflare",

        "challenge-platform",

        "captcha failed",

        "verify you are human"
    ]

    detected = []

    for item in checks:

        if item in text:

            detected.append(
                item
            )

    return detected


# =========================================================
# DETECT STATUS
# =========================================================

def detect_status(html):

    text = html.lower()

    status_words = {

        "failed":
            [
                "conversion failed",
                "failed",
                "error"
            ],

        "processing":
            [
                "downloading",
                "processing",
                "converting",
                "conversion"
            ],

        "finished":
            [
                "conversion finished",
                "completed",
                "download"
            ]
    }

    result = []

    for status, words in status_words.items():

        for word in words:

            if word in text:

                result.append(
                    status
                )

                break

    return list(
        dict.fromkeys(result)
    )


# =========================================================
# SUBMIT FORM
# =========================================================

def submit_amp4_form(
    session,
    page_url,
    form,
    youtube_url
):

    action = urljoin(
        page_url,
        form.get(
            "action",
            "/"
        )
    )

    method = (
        form.get(
            "method",
            "GET"
        )
        .upper()
    )

    field_name = find_youtube_field(
        form
    )

    if not field_name:

        return None, None, (
            "Could not identify the "
            "YouTube URL field."
        )

    # -----------------------------------------
    # Collect normal fields
    # -----------------------------------------

    data = {}

    for field in form.get(
        "fields",
        []
    ):

        name = field[
            "name"
        ]

        value = field.get(
            "value",
            ""
        )

        # Keep hidden/default values
        if value:

            data[name] = value

    # -----------------------------------------
    # Put YouTube URL
    # -----------------------------------------

    data[field_name] = youtube_url

    headers = get_headers()

    headers[
        "Origin"
    ] = "https://amp4.cc"

    headers[
        "Referer"
    ] = page_url

    # -----------------------------------------
    # Submit
    # -----------------------------------------

    if method == "POST":

        response = session.post(
            action,
            data=data,
            headers=headers,
            timeout=60,
            allow_redirects=True
        )

    else:

        response = session.get(
            action,
            params=data,
            headers=headers,
            timeout=60,
            allow_redirects=True
        )

    return (
        response,
        field_name,
        None
    )


# =========================================================
# MAIN BUTTON
# =========================================================

cookies_file = st.file_uploader(
    "Upload AMP4 cookies.txt",
    type=[
        "txt",
        "cookies"
    ]
)

youtube_url = st.text_input(
    "YouTube URL",
    placeholder=(
        "https://www.youtube.com/watch?v=..."
    )
)


if st.button(
    "🚀 Start Full AMP4 Test",
    type="primary"
):

    # -----------------------------------------
    # Validate
    # -----------------------------------------

    if not cookies_file:

        st.error(
            "Please upload AMP4 cookies.txt"
        )

        st.stop()

    if not youtube_url.strip():

        st.error(
            "Please enter YouTube URL"
        )

        st.stop()

    # -----------------------------------------
    # Load cookies
    # -----------------------------------------

    try:

        session, cookie_count = (
            load_amp4_cookies(
                cookies_file
            )
        )

    except Exception as e:

        st.error(
            f"Cookie loading error: {e}"
        )

        st.stop()

    st.success(
        f"AMP4 cookies loaded: {cookie_count}"
    )

    # -----------------------------------------
    # STEP 1
    # -----------------------------------------

    st.write(
        "## 1️⃣ Opening AMP4"
    )

    try:

        with st.spinner(
            "Connecting..."
        ):

            page = get_amp4(
                session
            )

    except Exception as e:

        st.error(
            f"AMP4 connection failed: {e}"
        )

        st.stop()

    if page.status_code == 200:

        st.success(
            "AMP4 HTTP connection: ✅"
        )

    else:

        st.warning(
            f"AMP4 HTTP status: "
            f"{page.status_code}"
        )

    st.write(
        "Page:",
        page.url
    )

    # -----------------------------------------
    # STEP 2
    # -----------------------------------------

    st.write(
        "## 2️⃣ Checking AMP4"
    )

    html = page.text

    parser = AMP4Parser()

    parser.feed(
        html
    )

    st.success(
        f"Forms found: "
        f"{len(parser.forms)}"
    )

    # -----------------------------------------
    # CAPTCHA
    # -----------------------------------------

    anti_bot = detect_antibot(
        html
    )

    if anti_bot:

        st.warning(
            "AMP4 anti-bot/CAPTCHA indicators found:"
        )

        st.write(
            ", ".join(
                anti_bot
            )
        )

        st.info(
            "This test will not bypass "
            "CAPTCHA or anti-bot protection."
        )

    # -----------------------------------------
    # STEP 3
    # -----------------------------------------

    st.write(
        "## 3️⃣ Detecting YouTube form"
    )

    selected_form = None

    selected_field = None

    for form in parser.forms:

        field = find_youtube_field(
            form
        )

        if field:

            selected_form = form

            selected_field = field

            break

    if not selected_form:

        st.error(
            "❌ YouTube URL form could not "
            "be identified."
        )

        st.info(
            "AMP4 may be using a JavaScript-only "
            "request instead of a normal HTML form."
        )

        st.stop()

    st.success(
        f"YouTube field detected: "
        f"{selected_field}"
    )

    st.write(
        "Form method:",
        selected_form.get(
            "method"
        )
    )

    st.write(
        "Form action:",
        urljoin(
            page.url,
            selected_form.get(
                "action",
                "/"
            )
        )
    )

    # -----------------------------------------
    # STEP 4
    # -----------------------------------------

    st.write(
        "## 4️⃣ Sending YouTube URL"
    )

    st.code(
        youtube_url
    )

    try:

        with st.spinner(
            "Sending normal AMP4 conversion request..."
        ):

            result = submit_amp4_form(
                session,
                page.url,
                selected_form,
                youtube_url.strip()
            )

    except Exception as e:

        st.error(
            f"Request failed: {e}"
        )

        st.stop()

    response = result[0]

    if response is None:

        st.error(
            result[2]
        )

        st.stop()

    # -----------------------------------------
    # STEP 5
    # -----------------------------------------

    st.write(
        "## 5️⃣ AMP4 Response"
    )

    st.write(
        "HTTP status:",
        response.status_code
    )

    st.write(
        "Final URL:",
        response.url
    )

    if response.status_code >= 200 and response.status_code < 400:

        st.success(
            "AMP4 request accepted at HTTP level."
        )

    else:

        st.error(
            f"AMP4 returned HTTP "
            f"{response.status_code}"
        )

    response_html = response.text

    # -----------------------------------------
    # CAPTCHA RESPONSE
    # -----------------------------------------

    response_antibot = detect_antibot(
        response_html
    )

    if response_antibot:

        st.warning(
            "AMP4 returned anti-bot/CAPTCHA content:"
        )

        st.write(
            ", ".join(
                response_antibot
            )
        )

        st.stop()

    # -----------------------------------------
    # STATUS
    # -----------------------------------------

    statuses = detect_status(
        response_html
    )

    if statuses:

        st.write(
            "Detected status:",
            ", ".join(
                statuses
            )
        )

    # -----------------------------------------
    # DOWNLOAD LINKS
    # -----------------------------------------

    st.write(
        "## 6️⃣ Searching for download result"
    )

    download_links = find_download_links(
        response_html,
        response.url
    )

    if download_links:

        st.success(
            f"Possible download links found: "
            f"{len(download_links)}"
        )

        for i, link in enumerate(
            download_links[:20],
            1
        ):

            st.write(
                f"### Download {i}"
            )

            st.code(
                link
            )

            st.markdown(
                f"[Open download link]({link})"
            )

    else:

        st.warning(
            "❌ No direct download link found "
            "in the HTTP response."
        )

    # -----------------------------------------
    # RESULT
    # -----------------------------------------

    st.write(
        "## 7️⃣ Final Test Result"
    )

    if download_links:

        st.success(
            "🎉 AMP4 conversion/download URL "
            "was detected!"
        )

        st.info(
            "The next step can be integrating "
            "this successful request into your "
            "main Shorts application."
        )

    elif (
        "conversion" in response_html.lower()
        or "downloading" in response_html.lower()
        or "processing" in response_html.lower()
    ):

        st.info(
            "AMP4 accepted/started a conversion "
            "flow, but the final download URL "
            "was not present in this response."
        )

        st.info(
            "This likely means the conversion "
            "is handled asynchronously by JavaScript."
        )

    else:

        st.warning(
            "AMP4 did not expose a direct "
            "download result through this normal "
            "HTTP request."
        )

        st.write(
            "This does NOT prove AMP4 cannot "
            "download the video. It means the "
            "website likely needs its JavaScript "
            "conversion flow or an official API."
        )
