"""Counts guard: an unsupported role count ("several roles", "both jobs") triggers one regeneration."""

import pytest

from app import unsupported_count_claim

SOURCES = ["Java Full Stack Engineer at Copart, May 2022 to January 2024.", "Apache Kafka, ActiveMQ, JMS."]


@pytest.mark.parametrize("answer, claim", [
    ("Nitin has used Apache Kafka in several roles. At Copart…", "several roles"),
    ("He used it in both of his professional roles.", "both of his professional roles"),
    ("Kafka appears in multiple jobs [skills#3].", "multiple jobs"),
    ("He held a few Java positions.", "a few Java positions"),
])
def test_detects_unsupported_role_counts(answer, claim):
    assert unsupported_count_claim(answer, SOURCES) == claim


@pytest.mark.parametrize("answer", [
    "He used Kafka at Copart [experience-copart#2] and at Sonata Software [experience-sonata#3].",
    "Both Kafka and ActiveMQ appear in his skills [skills#3].",          # "both" not about roles
    "He used several messaging tools such as Kafka and JMS.",            # count of tools, not roles
])
def test_ignores_answers_without_role_counts(answer):
    assert unsupported_count_claim(answer, SOURCES) is None


def test_allowed_when_a_source_states_the_count():
    assert unsupported_count_claim("He held several roles.", SOURCES + ["He has held several roles in banking."]) is None
