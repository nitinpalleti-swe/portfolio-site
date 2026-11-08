"""Unit tests for the deterministic job-fit checks (no API key or network needed).

Run:  .venv/bin/python -m pytest -q tests/
"""

import pytest

from app import FitReport, FitRequirement, check_fit_report


def check(requirement: str, source_id: str, status: str = "match", jd: str = "job text"):
    report = FitReport(role_title="T", overall_fit="good", summary="s",
                       requirements=[FitRequirement(requirement=requirement, status=status, evidence="e", source_id=source_id)])
    fixed, notes = check_fit_report(report, jd)
    return fixed, notes


@pytest.mark.parametrize("requirement, source_id, expected", [
    # Python/ML cap respects alternatives: "X or Python" keeps match when the cited source supports X.
    ("Strong Java or Python and REST microservices", "skills#1", "match"),
    ("Java or Python", "skills#1", "match"),
    ("Java or Python", "skills#7", "match"),        # skills#7 states "his primary language is Java"
    ("Strong Java or Python and REST microservices", "experience-jpmc-2024#3", "match"),  # "Java" is in the section title
    ("Python required", "skills#7", "partial"),     # Python only → capped
    ("Python/Go backend services", "skills#1", "partial"),  # the alternative (Go) isn't supported → capped
    ("Machine learning or deep learning frameworks", "skills#6", "partial"),  # every option is ML → capped
])
def test_python_ml_cap(requirement, source_id, expected):
    fixed, _ = check(requirement, source_id)
    assert fixed.requirements[0].status == expected


def test_unknown_source_becomes_gap():
    fixed, notes = check("Kubernetes", "made-up#3")
    assert (fixed.requirements[0].status, fixed.requirements[0].source_id) == ("gap", "")
    assert notes


def test_sourceless_partial_becomes_gap():
    fixed, _ = check("Kafka", "", status="partial")
    assert fixed.requirements[0].status == "gap"


@pytest.mark.parametrize("requirement, expected", [
    ("10+ years of backend experience", "partial"),  # profile#1 states 8+ years
    ("5+ years of backend experience", "match"),
])
def test_years_need_a_source(requirement, expected):
    fixed, _ = check(requirement, "profile#1")
    assert fixed.requirements[0].status == expected


def test_no_sponsorship_adds_work_auth_gap():
    fixed, notes = check("Java", "skills#1", jd="We are unable to sponsor visas for this position.")
    auth = [r for r in fixed.requirements if r.source_id.startswith("looking-for#")]
    assert len(auth) == 1 and auth[0].status == "gap"


def test_sponsorship_available_adds_nothing():
    fixed, _ = check("Java", "skills#1", jd="Visa sponsorship available.")
    assert len(fixed.requirements) == 1


def test_overall_fit_capped_by_rows():
    report = FitReport(role_title="T", overall_fit="strong", summary=" ".join(["w"] * 70), requirements=[
        FitRequirement(requirement="Swift", status="gap", evidence="e", source_id=""),
        FitRequirement(requirement="Java", status="match", evidence="e", source_id="skills#1"),
    ])
    fixed, _ = check_fit_report(report, "job text")
    assert fixed.overall_fit == "partial"
    assert len(fixed.summary.split()) == 60
