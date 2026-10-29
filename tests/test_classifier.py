import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from classifier import FeedbackClassifier  # noqa: E402
from pipeline import parse_rating, run, validate  # noqa: E402


@pytest.fixture(scope="module")
def clf():
    return FeedbackClassifier.from_file(ROOT / "config" / "rules.json")


@pytest.mark.parametrize("text,category,department", [
    ("I was charged twice for my subscription", "Billing & Payments", "Finance"),
    ("My package never arrived", "Shipping & Delivery", "Logistics"),
    ("The app crashes on startup", "Technical Issue", "IT Support"),
    ("The jacket fell apart after one wash", "Product Quality", "Quality Assurance"),
    ("The agent was rude and I was on hold forever", "Customer Service", "Customer Support"),
    ("Please add dark mode", "Feature Request", "Product Management"),
    ("Excellent product, highly recommend", "Praise", "Marketing"),
    ("I will contact my lawyer about this fraud", "Legal & Privacy", "Legal & Compliance"),
])
def test_category_and_department(clf, text, category, department):
    r = clf.classify(text)
    assert (r.category, r.department) == (category, department)
    assert not r.needs_review


def test_word_boundaries(clf):
    # "issue" must not trigger the "sue" legal keyword
    assert clf.classify("I have an issue with my order").category != "Legal & Privacy"


def test_unmatched_goes_to_default_for_review(clf):
    r = clf.classify("What time do you open on Sunday?")
    assert r.category == "General Inquiry" and r.department == "Customer Support" and r.needs_review


def test_urgent_priority(clf):
    assert clf.classify("This is fraud, I will sue", rating=1).priority == "Urgent"
    assert clf.classify("Cheap and broken", rating=1).priority == "High"
    assert clf.classify("Love it, excellent", rating=5).priority == "Low"


def test_sentiment_negation(clf):
    assert clf.sentiment("not good at all") == "Negative"
    assert clf.sentiment("really good") == "Positive"


def test_parse_rating():
    assert parse_rating("4") == 4
    assert parse_rating("") is None
    assert parse_rating("9") is None


def test_validate_flags_bad_rows():
    base = dict(date="", customer_name="", channel="", rating="")
    rows = [
        dict(base, feedback_id="1", email="a@b.com", feedback_text="ok"),
        dict(base, feedback_id="1", email="a@b.com", feedback_text="dup"),
        dict(base, feedback_id="2", email="a@b.com", feedback_text=""),
        dict(base, feedback_id="3", email="not-an-email", feedback_text="hi"),
    ]
    valid, exc = validate(rows)
    assert len(valid) == 1 and len(exc) == 3


def test_end_to_end(tmp_path):
    summary = run(ROOT / "data" / "feedback_sample.csv", ROOT / "config" / "rules.json",
                  tmp_path, now=datetime(2026, 10, 28, 9, 0))
    # Counts are derived from the sample file rather than hard-coded, so editing or extending
    # data/feedback_sample.csv during a demo does not break the suite.
    assert summary["records_read"] > 0
    assert summary["exceptions"] >= 1  # the file deliberately contains a duplicate id
    assert summary["routed"] + summary["needs_review"] == summary["records_read"] - summary["exceptions"]
    assert sum(summary["by_department"].values()) == summary["routed"] + summary["needs_review"]
    assert (tmp_path / "feedback_report.xlsx").exists()
    assert (tmp_path / "by_department" / "Finance.csv").exists()
    assert (tmp_path / "exceptions.csv").exists()
