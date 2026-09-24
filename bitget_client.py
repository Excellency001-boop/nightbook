import json
import os
import urllib.error
import urllib.request

BASE_URL = "https://api.bitget.com"


def _load_dotenv():
    if not os.path.exists(".env"):
        return
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()

API_KEY = os.getenv("BITGET_API_KEY", "")
API_SECRET = os.getenv("BITGET_API_SECRET", "")
API_PASSPHRASE = os.getenv("BITGET_API_PASSPHRASE", "")


class Response:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self) -> dict:
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text[:300]}")


def _sign(timestamp: str, method: str, request_path: str, body: str = "") -> str:
    import base64
    import hashlib
    import hmac

    prehash = f"{timestamp}{method.upper()}{request_path}{body}"
    digest = hmac.new(API_SECRET.encode(), prehash.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def get(path: str, params: dict | None = None, timeout: float = 10.0) -> Response:
    params = params or {}
    query = "&".join(f"{k}={v}" for k, v in params.items())
    request_path = path + (f"?{query}" if query else "")

    headers = {}
    if API_KEY and API_SECRET and API_PASSPHRASE:
        import time as _time

        ts = str(int(_time.time() * 1000))
        headers = {
            "ACCESS-KEY": API_KEY,
            "ACCESS-SIGN": _sign(ts, "GET", request_path),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": API_PASSPHRASE,
            "Content-Type": "application/json",
        }

    req = urllib.request.Request(BASE_URL + request_path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, resp.read().decode())
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read().decode())
