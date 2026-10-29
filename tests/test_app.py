import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import app as webapp  # noqa: E402


@pytest.fixture
def client(tmp_path):
    webapp.app.config.update(TESTING=True, LIVE_CSV=tmp_path / "feedback_live.csv")
    return webapp.app.test_client()


def test_form_renders(client):
    r = client.get("/")
    assert r.status_code == 200 and b"Tell us how we did" in r.data


def test_submit_classifies_and_persists(client):
    r = client.post("/", data={
        "customer_name": "Test Person", "email": "test@example.com", "rating": "1",
        "feedback_text": "I was charged twice for my subscription, please refund me.",
    }, follow_redirects=True)
    assert r.status_code == 200
    body = r.data.decode()
    assert "Finance" in body and "Billing &amp; Payments" in body

    rows = list(csv.DictReader(open(webapp.app.config["LIVE_CSV"], encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["feedback_id"] == "W0001" and rows[0]["channel"] == "Web App"


def test_ids_increment(client):
    for _ in range(2):
        client.post("/", data={"customer_name": "A B", "email": "a@b.com", "rating": "3",
                               "feedback_text": "The app crashes on startup every time."})
    rows = list(csv.DictReader(open(webapp.app.config["LIVE_CSV"], encoding="utf-8")))
    assert [r["feedback_id"] for r in rows] == ["W0001", "W0002"]


@pytest.mark.parametrize("field,data", [
    ("email", {"customer_name": "A", "email": "nope", "feedback_text": "x" * 20}),
    ("feedback_text", {"customer_name": "A", "email": "a@b.com", "feedback_text": "short"}),
    ("customer_name", {"customer_name": "", "email": "a@b.com", "feedback_text": "x" * 20}),
])
def test_invalid_submission_is_rejected(client, field, data):
    r = client.post("/", data=data)
    assert r.status_code == 400
    assert not webapp.app.config["LIVE_CSV"].exists()


def test_queue_groups_by_department(client):
    client.post("/", data={"customer_name": "A B", "email": "a@b.com", "rating": "1",
                           "feedback_text": "This is fraud and I will contact my lawyer."})
    r = client.get("/queue")
    assert b"Legal &amp; Compliance" in r.data and b"Urgent" in r.data


def test_live_file_feeds_the_bot(client, tmp_path):
    """A web submission must be readable by the batch pipeline without any conversion."""
    from pipeline import read_feedback, validate
    client.post("/", data={"customer_name": "A B", "email": "a@b.com", "rating": "2",
                           "feedback_text": "My package never arrived and tracking is stuck."})
    rows = read_feedback([ROOT / "data" / "feedback_sample.csv", webapp.app.config["LIVE_CSV"]])
    valid, exceptions = validate(rows)
    assert any(r["feedback_id"] == "W0001" for r in valid)


def test_dashboard_without_a_bot_run_yet(client, tmp_path):
    webapp.app.config["SUMMARY_JSON"] = tmp_path / "no_such_summary.json"
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert b"No report yet" in r.data


def test_dashboard_renders_summary(client, tmp_path):
    summary = {
        "run_time": "2026-10-28 12:00:00",
        "records_read": 10, "routed": 7, "needs_review": 2, "exceptions": 1,
        "by_department": {"Finance": 4, "Logistics": 3},
        "by_category": {"Billing & Payments": 4, "Shipping & Delivery": 3},
        "by_priority": {"Urgent": 1, "High": 3, "Normal": 2, "Low": 1},
        "by_sentiment": {"Negative": 5, "Positive": 2, "Neutral": 0},
    }
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    webapp.app.config["SUMMARY_JSON"] = summary_path

    r = client.get("/dashboard")
    body = r.data.decode()
    assert r.status_code == 200
    assert "Finance" in body and "Billing &amp; Payments" in body
    assert "Urgent" in body
    # Neutral has a 0 count and must not render an empty segment
    assert body.count('class="sentiment-seg') == 2
