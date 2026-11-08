"""Retrieval regressions (BM25, no model calls). Adding knowledge files must not crowd out his roles."""

import knowledge


def top(query: str, k: int = 6) -> list[str]:
    return [c.id for c, _ in knowledge.retrieve(query, k)]


def test_kafka_has_copart_in_top_3():
    assert "experience-copart#2" in top("kafka", 3)


def test_java_and_aws_retrieves_all_three_roles():
    ids = top("Which of his jobs used Java and AWS?")
    assert {"experience-copart#4", "experience-jpmc-2024#4", "experience-jpmc-offers#3"} <= set(ids)


def test_unknown_technology_retrieves_nothing():
    assert top("Has Nitin used Rust?") == []


def test_monitoring_finds_datadog_section():
    assert "experience-jpmc-offers#6" in top("What monitoring tools has he used?")


def test_masters_ranks_first():
    assert top("masters degree", 1) == ["education#1"]


def test_no_phone_number_in_knowledge():
    assert not any(knowledge.PHONE_LIKE.search(c.text) for c in knowledge.CHUNKS)
