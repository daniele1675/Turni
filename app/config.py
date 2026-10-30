import os
import secrets


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Config:
    portal_url = os.environ.get("PORTAL_URL", "https://na.amazonmoment.com/goa/wfm/associate/schedule")
    username = os.environ.get("PORTAL_USERNAME", "")
    password = os.environ.get("PORTAL_PASSWORD", "")

    # Selettori opzionali, nel caso la pagina di login non venga riconosciuta da sola.
    user_selector = os.environ.get("LOGIN_USER_SELECTOR", "")
    pass_selector = os.environ.get("LOGIN_PASS_SELECTOR", "")
    submit_selector = os.environ.get("LOGIN_SUBMIT_SELECTOR", "")

    weeks_ahead = _int("WEEKS_AHEAD", 3)
    weeks_back = _int("WEEKS_BACK", 1)
    refresh_hours = max(1, _int("REFRESH_HOURS", 4))
    quiet_start = _int("QUIET_FROM_HOUR", 23)   # niente letture automatiche di notte
    quiet_end = _int("QUIET_TO_HOUR", 6)
    tz = os.environ.get("TZ", "Europe/Rome")

    data_dir = os.environ.get("DATA_DIR", "/data")
    port = _int("PORT", 8090)
    access_token = os.environ.get("ACCESS_TOKEN", "")
    notify_webhook = os.environ.get("NOTIFY_WEBHOOK_URL", "")
    headless = os.environ.get("HEADLESS", "1") != "0"

    @classmethod
    def ensure_token(cls):
        """Senza ACCESS_TOKEN ne genera uno e lo salva in /data, così resta uguale ai riavvii."""
        if cls.access_token:
            return cls.access_token
        path = os.path.join(cls.data_dir, "access_token.txt")
        if os.path.exists(path):
            cls.access_token = open(path).read().strip()
        else:
            os.makedirs(cls.data_dir, exist_ok=True)
            cls.access_token = secrets.token_urlsafe(24)
            with open(path, "w") as f:
                f.write(cls.access_token)
        return cls.access_token
