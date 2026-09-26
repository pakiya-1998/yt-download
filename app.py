import streamlit as st
import requests
import re
from urllib.parse import urljoin

st.set_page_config(
    page_title="AMP4 JS Diagnostic",
    page_icon="🔎",
    layout="wide"
)

st.title("🔎 AMP4 JavaScript Endpoint Diagnostic")

st.warning(
    "Diagnostic only. This tool inspects public HTML/JavaScript. "
    "It does NOT bypass CAPTCHA, anti-bot protection, login controls, "
    "or access restrictions."
)

st.caption(
    "No Playwright • No Selenium • No Chromium • Normal HTTP requests only"
)

page_url = st.text_input(
    "AMP4 page",
    "https://amp4.cc/"
)

max_scripts = st.slider(
    "Maximum JavaScript files to inspect",
    1,
    30,
    15
)


if st.button("🔎 Inspect AMP4", type="primary"):

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
    }

    try:

        # =====================================================
        # 1. OPEN AMP4
        # =====================================================

        with st.spinner("AMP4 page read ho rahi hai..."):

            response = session.get(
                page_url,
                headers=headers,
                timeout=30
            )

        st.write("### 1. AMP4 Connection")

        st.success(
            f"HTTP {response.status_code} — {response.url}"
        )

        html = response.text
        html_lower = html.lower()

        # =====================================================
        # SECURITY / CAPTCHA CHECK
        # =====================================================

        security_words = [
            "captcha",
            "recaptcha",
            "hcaptcha",
            "cloudflare",
            "challenge-platform",
            "turnstile"
        ]

        security_found = [
            word
            for word in security_words
            if word in html_lower
        ]

        if security_found:

            st.warning(
                "Security/CAPTCHA indicators found: "
                + ", ".join(security_found)
            )

        else:

            st.success(
                "No obvious CAPTCHA/security keyword found "
                "in initial HTML."
            )

        # =====================================================
        # 2. FIND JAVASCRIPT FILES
        # =====================================================

        script_urls = []

        script_pattern = (
            r"""<script\b[^>]*\bsrc=["']([^"']+)["']"""
        )

        matches = re.findall(
            script_pattern,
            html,
            re.IGNORECASE
        )

        for src in matches:

            full_url = urljoin(
                response.url,
                src
            )

            if full_url not in script_urls:

                script_urls.append(full_url)

        st.write("### 2. JavaScript Files")

        st.write(
            f"Found **{len(script_urls)}** external JavaScript files."
        )

        for script_url in script_urls[:max_scripts]:

            st.code(script_url)

        # =====================================================
        # DOWNLOAD JS FILES
        # =====================================================

        sources = [
            ("AMP4 HTML", html)
        ]

        progress = st.progress(0)

        scripts_to_check = script_urls[:max_scripts]

        for index, script_url in enumerate(
            scripts_to_check
        ):

            try:

                js_response = session.get(
                    script_url,
                    headers=headers,
                    timeout=20
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

        # =====================================================
        # 3. POSSIBLE API ENDPOINTS
        # =====================================================

        endpoint_patterns = [

            r"""["'`]((?:https?:)?//[^"'`\s]+/[^"'`\s]*)["'`]""",

            r"""["'`]((?:/|\./|\.\./)(?:api|ajax|convert|download|process|youtube|video|task|job|status|result|file)[^"'`\\\s]*)["'`]"""
        ]

        request_patterns = [

            r"""fetch\s*\(\s*["'`]([^"'`]+)""",

            r"""axios\.(?:get|post|put|patch|delete)\s*\(\s*["'`]([^"'`]+)""",

            r"""\.open\s*\(\s*["'](?:GET|POST|PUT|PATCH|DELETE)["']\s*,\s*["'`]([^"'`]+)"""
        ]

        possible_endpoints = set()

        javascript_requests = []

        for source_name, source_text in sources:

            # ---------------------------------------------
            # Endpoint search
            # ---------------------------------------------

            for pattern in endpoint_patterns:

                try:

                    found_items = re.findall(
                        pattern,
                        source_text,
                        re.IGNORECASE
                    )

                except Exception:

                    found_items = []

                for item in found_items:

                    if (
                        "amp4.cc" in item.lower()
                        or item.startswith("/")
                        or item.startswith("./")
                        or item.startswith("../")
                    ):

                        possible_endpoints.add(
                            (
                                source_name,
                                item[:500]
                            )
                        )

            # ---------------------------------------------
            # fetch / axios / XHR search
            # ---------------------------------------------

            for pattern in request_patterns:

                try:

                    found_requests = re.findall(
                        pattern,
                        source_text,
                        re.IGNORECASE
                    )

                except Exception:

                    found_requests = []

                for item in found_requests:

                    javascript_requests.append(
                        (
                            source_name,
                            item[:500]
                        )
                    )

        # =====================================================
        # SHOW ENDPOINTS
        # =====================================================

        st.write(
            "### 3. Possible API / Conversion References"
        )

        if possible_endpoints:

            rows = []

            for source_name, endpoint in sorted(
                possible_endpoints
            ):

                rows.append(
                    {
                        "Source": source_name[:100],

                        "Reference": endpoint,

                        "Resolved URL": urljoin(
                            page_url,
                            endpoint
                        )
                    }
                )

            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True
            )

        else:

            st.info(
                "No obvious endpoint reference found "
                "in the inspected HTML/JS."
            )

        # =====================================================
        # 4. JAVASCRIPT REQUESTS
        # =====================================================

        st.write(
            "### 4. JavaScript Request Calls"
        )

        unique_requests = list(
            dict.fromkeys(
                javascript_requests
            )
        )

        if unique_requests:

            rows = []

            for source_name, request_target in unique_requests[:200]:

                rows.append(
                    {
                        "Source": source_name[:100],

                        "Request Target": request_target
                    }
                )

            st.dataframe(
                rows,
                use_container_width=True,
                hide_index=True
            )

        else:

            st.info(
                "No obvious fetch/axios/XHR target found."
            )

        # =====================================================
        # 5. CONVERSION KEYWORDS
        # =====================================================

        st.write(
            "### 5. Conversion-Related Keywords"
        )

        keywords = [
            "youtube",
            "convert",
            "download",
            "format",
            "quality",
            "video",
            "mp4",
            "webm",
            "trim",
            "captcha",
            "token",
            "api",
            "ajax",
            "status",
            "result",
            "job",
            "task"
        ]

        keyword_rows = []

        for source_name, source_text in sources:

            source_lower = source_text.lower()

            found_keywords = [
                keyword
                for keyword in keywords
                if keyword in source_lower
            ]

            if found_keywords:

                keyword_rows.append(
                    {
                        "Source": source_name[:100],

                        "Keywords": ", ".join(
                            found_keywords
                        )
                    }
                )

        if keyword_rows:

            st.dataframe(
                keyword_rows,
                use_container_width=True,
                hide_index=True
            )

        # =====================================================
        # FINAL RESULT
        # =====================================================

        st.write("### 6. Result")

        st.success(
            "✅ AMP4 JavaScript diagnostic complete."
        )

        st.info(
            "Agar conversion/API endpoint milta hai, "
            "hum us public request ko study karke "
            "normal API integration bana sakte hain."
        )

        st.warning(
            "⚠️ Ye tool CAPTCHA, anti-bot protection, "
            "login controls ya access restrictions bypass nahi karta."
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
