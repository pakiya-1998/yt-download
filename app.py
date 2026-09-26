import streamlit as st
import requests
import re
from urllib.parse import urljoin

st.set_page_config(
    page_title="AMP4 Deep JS Diagnostic",
    page_icon="🕵️",
    layout="wide"
)

st.title("🕵️ AMP4 Deep JavaScript Diagnostic")

st.warning(
    "Diagnostic only. This tool inspects publicly loaded AMP4 HTML/JavaScript. "
    "It does NOT bypass CAPTCHA, anti-bot protection, login controls, "
    "or access restrictions."
)

st.caption(
    "Goal: find the public JavaScript request used by AMP4 for conversion."
)

# ============================================================
# SETTINGS
# ============================================================

page_url = st.text_input(
    "AMP4 Page URL",
    value="https://amp4.cc/"
)

max_scripts = st.slider(
    "Maximum JavaScript files to inspect",
    min_value=1,
    max_value=40,
    value=20
)

context_lines = st.slider(
    "Context around detected request",
    min_value=5,
    max_value=40,
    value=15
)

# ============================================================
# HELPERS
# ============================================================

def get_context(text, position, radius=500):
    """
    Return text around a detected match.
    """
    start = max(0, position - radius)
    end = min(len(text), position + radius)

    return text[start:end]


def clean_context(text):
    """
    Make JavaScript easier to read in Streamlit.
    """
    text = text.replace("\\n", "\n")
    text = text.replace("\\r", "")
    text = re.sub(r"\s+", " ", text)

    return text[:4000]


def search_request_context(source_name, source_text):
    """
    Search for likely network/API/conversion calls and
    return the surrounding JavaScript context.
    """

    patterns = [

        # fetch(...)
        (
            "FETCH",
            r"""fetch\s*\("""
        ),

        # axios.get/post/etc
        (
            "AXIOS",
            r"""axios\s*\.\s*(?:get|post|put|patch|delete|request)\s*\("""
        ),

        # XMLHttpRequest
        (
            "XHR",
            r"""(?:XMLHttpRequest|\.open\s*\()"""
        ),

        # $.ajax / $.get / $.post
        (
            "JQUERY AJAX",
            r"""\$\s*\.\s*(?:ajax|get|post)\s*\("""
        ),

        # URL-looking strings
        (
            "API URL",
            r"""["'`](?:https?:)?//[^"'`\s]+["'`]"""
        ),

        # Relative API paths
        (
            "API PATH",
            r"""["'`](?:/|\./|\.\./)(?:api|ajax|convert|download|process|video|youtube|task|job|status|result)[^"'`]*["'`]"""
        )
    ]

    results = []

    for label, pattern in patterns:

        try:

            matches = list(
                re.finditer(
                    pattern,
                    source_text,
                    re.IGNORECASE
                )
            )

        except Exception:

            matches = []

        for match in matches[:50]:

            position = match.start()

            context = get_context(
                source_text,
                position,
                radius=1500
            )

            results.append(
                {
                    "type": label,
                    "position": position,
                    "match": match.group(0)[:500],
                    "context": clean_context(context)
                }
            )

    return results


# ============================================================
# MAIN
# ============================================================

if st.button(
    "🕵️ Deep Inspect AMP4",
    type="primary"
):

    session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://amp4.cc/"
    }

    try:

        # ====================================================
        # STEP 1
        # ====================================================

        st.write("## 1️⃣ AMP4 Connection")

        with st.spinner(
            "AMP4 page load ho rahi hai..."
        ):

            response = session.get(
                page_url,
                headers=headers,
                timeout=30,
                allow_redirects=True
            )

        if response.status_code == 200:

            st.success(
                f"HTTP {response.status_code} ✅"
            )

        else:

            st.warning(
                f"AMP4 returned HTTP {response.status_code}"
            )

        st.write(
            "**Final URL:**",
            response.url
        )

        html = response.text
        html_lower = html.lower()

        # ====================================================
        # STEP 2
        # ====================================================

        st.write("## 2️⃣ Security Check")

        security_words = [
            "captcha",
            "recaptcha",
            "hcaptcha",
            "cloudflare",
            "challenge-platform",
            "turnstile"
        ]

        security_found = [
            x
            for x in security_words
            if x in html_lower
        ]

        if security_found:

            st.warning(
                "Security indicators found: "
                + ", ".join(security_found)
            )

        else:

            st.success(
                "No obvious security keyword found."
            )

        # ====================================================
        # STEP 3
        # ====================================================

        st.write("## 3️⃣ JavaScript Files")

        script_pattern = (
            r"""<script\b[^>]*\bsrc=["']([^"']+)["']"""
        )

        script_sources = re.findall(
            script_pattern,
            html,
            re.IGNORECASE
        )

        script_urls = []

        for src in script_sources:

            full_url = urljoin(
                response.url,
                src
            )

            if full_url not in script_urls:

                script_urls.append(
                    full_url
                )

        st.success(
            f"{len(script_urls)} JavaScript files found."
        )

        for index, url in enumerate(
            script_urls[:max_scripts],
            start=1
        ):

            st.write(
                f"**JS {index}:**"
            )

            st.code(url)

        # ====================================================
        # STEP 4
        # DOWNLOAD JS
        # ====================================================

        st.write(
            "## 4️⃣ Downloading JavaScript for inspection"
        )

        sources = [
            (
                "AMP4 HTML",
                html
            )
        ]

        progress = st.progress(0)

        scripts_to_check = script_urls[
            :max_scripts
        ]

        for index, script_url in enumerate(
            scripts_to_check
        ):

            try:

                js_response = session.get(
                    script_url,
                    headers=headers,
                    timeout=25
                )

                if js_response.ok:

                    sources.append(
                        (
                            script_url,
                            js_response.text
                        )
                    )

            except Exception:

                pass

            progress.progress(
                int(
                    (index + 1)
                    * 100
                    / max(
                        1,
                        len(scripts_to_check)
                    )
                )
            )

        st.success(
            f"{len(sources)} source files loaded."
        )

        # ====================================================
        # STEP 5
        # REQUEST CONTEXT
        # ====================================================

        st.write(
            "## 5️⃣ Actual JavaScript Request Detection"
        )

        all_results = []

        for source_name, source_text in sources:

            results = search_request_context(
                source_name,
                source_text
            )

            for result in results:

                result["source"] = source_name

                all_results.append(
                    result
                )

        # Remove exact duplicates

        unique_results = []

        seen = set()

        for item in all_results:

            key = (
                item["source"],
                item["type"],
                item["match"]
            )

            if key not in seen:

                seen.add(key)

                unique_results.append(
                    item
                )

        if not unique_results:

            st.error(
                "❌ No obvious JavaScript request found."
            )

        else:

            st.success(
                f"Found {len(unique_results)} request/API references."
            )

            # =================================================
            # GROUP BY TYPE
            # =================================================

            request_types = {}

            for item in unique_results:

                request_types.setdefault(
                    item["type"],
                    []
                ).append(item)

            # =================================================
            # DISPLAY
            # =================================================

            for request_type, items in request_types.items():

                st.write(
                    f"### 🔹 {request_type} "
                    f"({len(items)})"
                )

                for number, item in enumerate(
                    items[:20],
                    start=1
                ):

                    title = (
                        f"{number}. "
                        f"{item['source'][:80]}"
                    )

                    with st.expander(
                        title,
                        expanded=False
                    ):

                        st.write(
                            "**Detected:**"
                        )

                        st.code(
                            item["match"]
                        )

                        st.write(
                            "**JavaScript context:**"
                        )

                        st.code(
                            item["context"],
                            language="javascript"
                        )

        # ====================================================
        # STEP 6
        # TARGETED AMP4 TERMS
        # ====================================================

        st.write(
            "## 6️⃣ AMP4 Conversion Keywords"
        )

        important_words = [
            "youtube",
            "video",
            "download",
            "convert",
            "conversion",
            "format",
            "quality",
            "mp4",
            "webm",
            "trim",
            "captcha",
            "token",
            "queue",
            "status",
            "progress",
            "result",
            "job",
            "task",
            "file",
            "url"
        ]

        keyword_results = []

        for source_name, source_text in sources:

            text_lower = source_text.lower()

            found = []

            for word in important_words:

                if word in text_lower:

                    found.append(word)

            if found:

                keyword_results.append(
                    {
                        "Source": source_name[:100],
                        "Keywords": ", ".join(found)
                    }
                )

        if keyword_results:

            st.dataframe(
                keyword_results,
                use_container_width=True,
                hide_index=True
            )

        # ====================================================
        # STEP 7
        # SEARCH FOR YOUTUBE URL FIELD
        # ====================================================

        st.write(
            "## 7️⃣ YouTube URL / Form References"
        )

        youtube_patterns = [
            r"""youtube""",
            r"""youtube_url""",
            r"""video_url""",
            r"""videoUrl""",
            r"""url\s*:""",
            r"""url\s*=""",
            r"""youtube\.com""",
            r"""youtu\.be"""
        ]

        youtube_hits = []

        for source_name, source_text in sources:

            for pattern in youtube_patterns:

                try:

                    matches = list(
                        re.finditer(
                            pattern,
                            source_text,
                            re.IGNORECASE
                        )
                    )

                except Exception:

                    matches = []

                for match in matches[:15]:

                    context = get_context(
                        source_text,
                        match.start(),
                        radius=1000
                    )

                    youtube_hits.append(
                        {
                            "source": source_name,
                            "pattern": pattern,
                            "context": clean_context(
                                context
                            )
                        }
                    )

        if youtube_hits:

            st.success(
                f"{len(youtube_hits)} YouTube-related references found."
            )

            for index, hit in enumerate(
                youtube_hits[:30],
                start=1
            ):

                with st.expander(
                    f"YouTube reference {index}"
                ):

                    st.write(
                        "**Source:**",
                        hit["source"]
                    )

                    st.write(
                        "**Matched:**",
                        hit["pattern"]
                    )

                    st.code(
                        hit["context"],
                        language="javascript"
                    )

        else:

            st.warning(
                "No YouTube URL reference found."
            )

        # ====================================================
        # STEP 8
        # FINAL
        # ====================================================

        st.write(
            "## 8️⃣ Diagnostic Result"
        )

        st.success(
            "✅ Deep AMP4 JavaScript inspection complete."
        )

        st.info(
            "Ab humein normal asset URLs ke bajay "
            "JavaScript request ke aas-paas ka actual code "
            "dikhna chahiye."
        )

        st.warning(
            "⚠️ Agar request CAPTCHA, authentication, "
            "private token, ya access-control ke peeche hai, "
            "ye tool us protection ko bypass nahi karega."
        )

    except requests.RequestException as error:

        st.error(
            f"❌ Network error: {error}"
        )

    except Exception as error:

        st.error(
            f"❌ Unexpected error: "
            f"{type(error).__name__}: {error}"
        )
