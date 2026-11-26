import json
import logging
import os
import random
import string
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
from locust import HttpUser, task, between
from pytest import Class

load_dotenv()

class ErrorCdsUser(HttpUser):
    host = os.environ["HOST"]

    def on_start(self):
        self.client.headers["securityToken"] = os.environ["CDS_API_TOKEN"]

    # ---------- helpers ----------

    def post_json(self, path, payload, expect=(200,)):
            """POST, classify the outcome, return parsed JSON on success or None."""
            with self.client.post(path, json=payload, name=path, catch_response=True,
                                  timeout=60, allow_redirects=False) as resp:
                status = resp.status_code
                body = (resp.text or "<empty>")[:500]
    
                if status in expect:
                    try:
                        return resp.json()
                    except ValueError:
                        category = "BAD_BODY"
                elif status == 0:
                    category = "NO_RESPONSE"
                    body = repr(getattr(resp, "error", None))
                elif 300 <= status < 400:
                    category = "REDIRECT_3XX"
                    body = f"Location: {resp.headers.get('Location')}"
                elif 400 <= status < 500:
                    category = "CLIENT_4XX"
                elif status >= 500:
                    category = "SERVER_5XX"
                else:
                    category = "UNEXPECTED"
    
                resp.failure(f"{category} {status}")
                logging.warning("%s | %s | %s | %s | payload=%s | body=%s",
                                datetime.now().isoformat(timespec="seconds"),
                                category, status, path,
                                json.dumps(payload)[:500], body)
                return None
