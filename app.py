# ============================================================
# YOUTUBE OAUTH STATUS / DOWNLOAD AUTH CHECK
# ============================================================

def get_connected_youtube_account(account_key=None):
    """
    Return the currently connected YouTube OAuth account information.

    OAuth is used for YouTube Data API operations such as:
    - channel verification
    - video upload
    - channel metadata

    IMPORTANT:
    Do NOT pass the OAuth access token directly to yt-dlp as if it
    were a browser cookie/session. Current yt-dlp YouTube restrictions
    do not support OAuth login for video downloading.
    """

    account_key = account_key or _current_account_key()

    accounts = st.session_state.get("youtube_accounts", {})
    account = accounts.get(account_key)

    if not account:
        return None

    credentials = credentials_from_token(account)

    if not credentials:
        return None

    # Refresh expired OAuth access token when possible.
    try:
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

            _save_youtube_account(
                account_key,
                credentials
            )

    except Exception:
        pass

    return {
        "account_key": account_key,
        "credentials": credentials,
        "channel": account.get("channel"),
    }


def verify_youtube_oauth_account(account_key=None):
    """
    Verify the connected Google/YouTube account using the official
    YouTube Data API.
    """

    account_key = account_key or _current_account_key()

    connected = get_connected_youtube_account(account_key)

    if not connected:
        return {
            "ok": False,
            "message": "YouTube account connected nahi hai."
        }

    credentials = connected["credentials"]

    try:
        youtube = build(
            "youtube",
            "v3",
            credentials=credentials,
            cache_discovery=False
        )

        response = youtube.channels().list(
            part="snippet,contentDetails,statistics",
            mine=True
        ).execute()

        items = response.get("items", [])

        if not items:
            return {
                "ok": False,
                "message": "OAuth account se YouTube channel nahi mila."
            }

        channel = items[0]

        accounts = st.session_state.setdefault(
            "youtube_accounts",
            {}
        )

        accounts.setdefault(account_key, {})
        accounts[account_key]["channel"] = channel

        return {
            "ok": True,
            "channel": channel,
            "title": channel.get("snippet", {}).get(
                "title",
                "YouTube Channel"
            ),
            "channel_id": channel.get("id", ""),
        }

    except Exception as e:
        return {
            "ok": False,
            "message": str(e)
        }


def render_youtube_auth_status():
    """
    Compact UI showing the difference between:
    1. Google/YouTube OAuth connection
    2. YouTube downloader authentication
    """

    account_key = _current_account_key()

    result = verify_youtube_oauth_account(account_key)

    st.markdown("### 🔐 YouTube Account")

    if result["ok"]:

        channel_title = result["title"]
        channel_id = result["channel_id"]

        st.success(
            f"✅ YouTube connected: {channel_title}"
        )

        st.caption(
            f"Channel ID: {channel_id}"
        )

        st.info(
            "ℹ️ Google OAuth channel connection upload/API access ke "
            "liye active hai. YouTube video download ke liye OAuth token "
            "ko yt-dlp cookie ke replacement ke roop mein use nahi kiya "
            "ja sakta."
        )

    else:

        st.warning(
            "YouTube channel connected nahi hai."
        )

        st.caption(
            result.get("message", "")
        )

        st.info(
            "Pehle Connect YouTube se Google account authorize karo."
        )
