import copy
import json
import logging
import os
import random
import string
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
from locust import HttpUser, LoadTestShape, between, task  # noqa: F401

import request_log  # noqa: F401  (registers the listeners)

load_dotenv()

MAX_IDS = 50
INVALID_TOKEN = "this-is-not-a-valid-token"

expected_tasks = {
    "CalculateLandedCost",
    "LinkS10ID",
    "QueryDeclaration",
    "CreateManifest",
    "CalculateLandedCost [missing country]",
    "CalculateLandedCost [country as number]",
    "CalculateLandedCost [invalid token]",
    "QueryDeclaration [unknown id]",
    "LinkS10ID [already linked]",
    "QueryDeclaration [hsCode assigned]",
    "CreateManifest [EU IOSS]",
    "CalculateLandedCost [no hsCode]",
}

completed_tasks = set() 

# ---------- body checks ----------
# Each returns None when the body is as expected, or a short description of the problem.

def expect_error_code(code):
    def check(data):
        errors = data.get("errors") if isinstance(data, dict) else None
        if not errors:
            return "no errors array"
        actual = errors[0].get("code") if isinstance(errors[0], dict) else None
        if str(actual) != code:
            return f"error code {actual}, expected {code}"
        return None
    return check


def has_declaration_id(data):
    try:
        if data["data"]["declaration"]["id"]:
            return None
    except (KeyError, TypeError):
        pass
    return "no declaration id"


def all_pieces_have_hs_code(data):
    try:
        pieces = data["data"]["declaration"]["contentPieces"]
    except (KeyError, TypeError):
        return "no contentPieces in response"
    if not pieces:
        return "contentPieces is empty"
    missing = sum(1 for p in pieces if not p.get("hsCode"))
    return f"{missing} piece(s) without hsCode" if missing else None

def expect_link_success(data):
    try:
        result = data["data"]["result"]
    except (KeyError, TypeError):
        return "no data.result"
    if result != "SUCCESS":
        return f"result {result}, reason: {data['data'].get('reason')}"
    return None

def expect_link_failure(data):
    try:
        result = data["data"]["result"]
        reason = data["data"].get("reason")
    except (KeyError, TypeError):
        return "no data.result"
    if result != "FAILURE":
        return f"result {result}, expected FAILURE"
    if not isinstance(reason, str) or not reason:
        return "FAILURE without a reason"
    return None


def expect_manifest_result(data):
    try:
        result = data["data"]["result"]
    except (KeyError, TypeError):
        return "no data.result"
    return None if result in ("SUCCESS", "FAILURE") else f"unexpected result {result}"


# ---------- payload builders ----------

RUBBER_SHOES = {
    "quantity": 2,
    "description": "rubber shoes",
    "countryOfOrigin": "CH",
    "hsCode": "6402990400",
    "weight": 1.5,
    "amount": 87.25,
    "currencyCode": "CHF",
}

WOODEN_TOY_NO_HS = {
    "quantity": 1,
    "description": "wooden toy",
    "countryOfOrigin": "CH",
    "weight": 0.4,
    "amount": 25.0,
    "currencyCode": "CHF",
}

SENDER_CH = {
    "name": "Alice Bob",
    "email": "alice.bob@example.com",
    "telephone": "41791112222",
    "address": "Weltpoststrasse 4",
    "locality": "Bern",
    "postalCode": "3015",
    "country": "CH",
}

RECIPIENT_US = {
    "name": "John Doe",
    "email": "john.doe@example.com",
    "telephone": "17181112222",
    "address": "13th St",
    "locality": "New York",
    "postalCode": "10011",
    "country": "US",
}

RECIPIENT_FR = {
    "name": "John Doe",
    "email": "john.doe@mail.com",
    "telephone": "33123456789",
    "address": "24 Rue De L'Exposition",
    "locality": "Paris",
    "postalCode": "75007",
    "country": "FR",
}


def landed_cost_payload(piece=None, gross_weight=3.0):
    """Fresh CalculateLandedCost body. Defaults to the happy-path rubber shoes."""
    stamp = int(time.time() * 1000)
    if piece is None:
        piece = dict(RUBBER_SHOES, description=f"rubber shoes {random.randint(1, 100000)}")
    recipient = dict(RECIPIENT_US, email=f"loadtest+{stamp}@example.com")
    return {
        "recipient": recipient,
        "sender": copy.deepcopy(SENDER_CH),
        "declaration": {
            "contentPieces": [copy.deepcopy(piece)],
            "natureType": "31",
            "grossWeight": gross_weight,
        },
        "shipment": {"postage": 5.0, "currencyCode": "CHF"},
        "landedCostCalculation": {"method": "DDP", "currencyCode": "CHF"},
    }


def manifest_payload(tracking_number, destination="JFK", recipient=None, tax_id=""):
    arrival = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "arrivalDateTime": arrival,
        "destinationCode": destination,
        "originCode": "ZRH",
        "transportationMode": "AIR",
        "carrierCode": "LX",
        "transportNumber": "16",
        "postalOperatorCode": "J1CCHA",
        "mailItems": [{
            "trackingNumber": tracking_number,
            "natureType": "31",
            "recipient": copy.deepcopy(recipient or RECIPIENT_US),
            "sender": dict(SENDER_CH, taxId=tax_id),
            "contentPieces": [copy.deepcopy(RUBBER_SHOES)],
        }],
    }


class HappyCdsUser(HttpUser):
    host = os.environ["HOST"]
    wait_time = between(1, 3)

    def on_start(self):
        self.client.headers["securityToken"] = os.environ["CDS_API_TOKEN"]
        self.declarationIds = []
        self.unlinkedIds = []  # declarations not yet linked; LinkS10ID takes from here
        self.links = []  # (declarationId, trackingNumber) pairs that linked successfully

    # ---------- helpers ----------

    def post_json(self, path, payload, expect=(200,), check=None, name=None, headers=None):
        """POST and classify the outcome.

        expect:  status codes that count as the intended outcome.
        check:   optional function(json) -> None if the body is right, else a short problem string.
        name:    stats label; defaults to the path.
        headers: per-request header overrides (merged over the session headers).
        Returns the parsed JSON when status and check both pass, otherwise None.
        """
        name = name or path
        with self.client.post(path, json=payload, name=name, headers=headers,
                              catch_response=True, timeout=30, allow_redirects=False) as resp:
            status = resp.status_code
            body = (resp.text or "<empty>")[:500]
            detail = ""

            if status in expect:
                try:
                    data = resp.json()
                except ValueError:
                    category = "BAD_BODY"
                else:
                    problem = check(data) if check else None
                    if problem is None:
                        resp.success()  # needed so expected 4xx responses count as successes
                        return data
                    category, detail = "WRONG_BODY", problem
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

            message = f"{category} {status}" + (f": {detail}" if detail else "")
            resp.failure(message)
            logging.warning("%s | %s | %s | payload=%s | body=%s",
                            datetime.now().isoformat(timespec="seconds"),
                            message, name, json.dumps(payload)[:500], body)
            return None

    def remember_id(self, declaration_id):
        for pool in (self.declarationIds, self.unlinkedIds):
            pool.append(declaration_id)
            if len(pool) > MAX_IDS:
                pool.pop(0)

    def remember_link(self, declaration_id, tracking_number):
        self.links.append((declaration_id, tracking_number))
        if len(self.links) > MAX_IDS:
            self.links.pop(0)

    def GenerateTrackingNumber(self):
        serial = [random.randint(0, 9) for _ in range(8)]
        weights = [8, 6, 4, 2, 3, 5, 9, 7]
        check = 11 - sum(d * w for d, w in zip(serial, weights)) % 11
        check = {10: 0, 11: 5}.get(check, check)
        service = random.choice(string.ascii_uppercase) + random.choice(string.ascii_uppercase)
        return service + "".join(map(str, serial)) + str(check) + "CH"

    # ---------- happy-path tasks ----------

    @task(5)
    def CalculateLandedCost(self):
        data = self.post_json("/CalculateLandedCost", landed_cost_payload())
        completed_tasks.add("CalculateLandedCost")
        if data is None:
            return
        try:
            self.remember_id(data["data"]["declaration"]["id"])
        except (KeyError, TypeError):
            logging.warning("CalculateLandedCost: unexpected shape: %s", json.dumps(data)[:500])

    @task(5)
    def LinkS10ID(self):
        if not self.unlinkedIds:
            self.CalculateLandedCost()
            return
        declaration_id = self.unlinkedIds.pop()
        tracking_number = self.GenerateTrackingNumber()
        data = self.post_json("/LinkS10ID", {
            "declarationId": declaration_id,
            "trackingNumbers": tracking_number,
        }, check=expect_link_success)
        if data is not None:
            self.remember_link(declaration_id, tracking_number)
            completed_tasks.add("LinkS10ID")

    @task(5)
    def QueryDeclaration(self):
        if not self.declarationIds:
            self.CalculateLandedCost()
            return
        self.post_json("/QueryDeclaration", {"declarationId": random.choice(self.declarationIds)})
        completed_tasks.add("QueryDeclaration")

    @task(25)
    def CreateManifest(self):
        self.post_json("/CreateManifest", manifest_payload(self.GenerateTrackingNumber()))
        completed_tasks.add("CreateManifest")

    # ---------- edge cases and error paths ----------

    @task(1)
    def CalculateLandedCost_MissingCountry(self):
        payload = landed_cost_payload()
        del payload["recipient"]["country"]
        del payload["declaration"]["contentPieces"][0]["countryOfOrigin"]
        self.post_json("/CalculateLandedCost", payload, expect=(400,),
                       check=expect_error_code("11"),
                       name="/CalculateLandedCost [missing country]")
        completed_tasks.add("CalculateLandedCost [missing country]")

    @task(1)
    def CalculateLandedCost_InvalidCountry(self):
        payload = landed_cost_payload()
        payload["recipient"]["country"] = 12345
        self.post_json("/CalculateLandedCost", payload, expect=(400,),
                       check=expect_error_code("11"),
                       name="/CalculateLandedCost [country as number]")
        completed_tasks.add("CalculateLandedCost [country as number]")

    @task(1)
    def CalculateLandedCost_InvalidToken(self):
        self.post_json("/CalculateLandedCost", landed_cost_payload(), expect=(401,),
                       check=expect_error_code("14"),
                       headers={"securityToken": INVALID_TOKEN},
                       name="/CalculateLandedCost [invalid token]")
        completed_tasks.add("CalculateLandedCost [invalid token]")

    @task(1)
    def QueryDeclaration_UnknownId(self):
        self.post_json("/QueryDeclaration",
                       {"declarationId": f"nonexistent{random.randint(100000, 999999)}"},
                       expect=(400,), check=expect_error_code("11"),
                       name="/QueryDeclaration [unknown id]")
        completed_tasks.add("QueryDeclaration [unknown id]")

    @task(1)
    def LinkS10ID_AlreadyLinked(self):
        if not self.links:
            self.LinkS10ID()
            return
        declaration_id, tracking_number = random.choice(self.links)
        self.post_json("/LinkS10ID", {
            "declarationId": declaration_id,
            "trackingNumbers": tracking_number,
        }, check=expect_link_failure, name="/LinkS10ID [already linked]")
        completed_tasks.add("LinkS10ID [already linked]")

    @task(1)
    def HsCodeSelfAssigned(self):
        """Create a declaration without hsCode, then confirm the server assigned one."""
        data = self.post_json("/CalculateLandedCost",
                              landed_cost_payload(piece=WOODEN_TOY_NO_HS, gross_weight=0.4),
                              check=has_declaration_id,
                              name="/CalculateLandedCost [no hsCode]")
        completed_tasks.add("CalculateLandedCost [no hsCode]")
        if data is None:
            return
        self.post_json("/QueryDeclaration",
                       {"declarationId": data["data"]["declaration"]["id"]},
                       check=all_pieces_have_hs_code,
                       name="/QueryDeclaration [hsCode assigned]")
        completed_tasks.add("QueryDeclaration [hsCode assigned]")

    @task(1)
    def CreateManifest_EuIoss(self):
        self.post_json("/CreateManifest",
                       manifest_payload(self.GenerateTrackingNumber(), destination="CDG",
                                        recipient=RECIPIENT_FR, tax_id="IM1234567890"),
                       check=expect_manifest_result,
                       name="/CreateManifest [EU IOSS]")
        completed_tasks.add("CreateManifest [EU IOSS]")