from docich.research_paper_discovery import ENABLE_ENV, discover_candidates


ENV = {
    ENABLE_ENV: "1",
    "DOCICH_REPLY_WEB_SEARCH_ENABLED": "1",
}


def test_disabled_discovery_has_zero_search_calls():
    called = []
    result = discover_candidates(
        ["recent robotics research"], env={},
        searcher=lambda *args: called.append(args) or [],
    )
    assert result.status == "disabled"
    assert called == []


def test_search_snippets_cannot_enter_candidate_contract():
    # The search seam returns URLs only. Candidate DTOs have no title, summary,
    # description, excerpt or free-form evidence field to receive a snippet.
    result = discover_candidates(
        ["recent robotics research"], env=ENV,
        searcher=lambda query, timeout: [
            "https://arxiv.org/abs/2610.12345",
            "https://news.example.org/story",
        ],
    )
    assert result.ok
    assert len(result.candidates) == 1
    wire = result.candidates[0].wire()
    assert wire == {
        "url": "https://arxiv.org/abs/2610.12345",
        "source_kind": "preprint",
        "query_indexes": [0],
        "rank": 1,
    }
    assert not ({"title", "summary", "description", "excerpt"} & set(wire))


def test_pdf_and_non_primary_hosts_are_rejected():
    result = discover_candidates(
        ["recent materials research"], env=ENV,
        searcher=lambda query, timeout: [
            "https://arxiv.org/pdf/2610.12345.pdf",
            "https://example.org/paper",
            "https://arxiv.org/abs/2610.12345",
        ],
    )
    assert [candidate.url for candidate in result.candidates] == [
        "https://arxiv.org/abs/2610.12345"
    ]


def test_exact_or_www_primary_host_is_allowed_but_arbitrary_subdomain_is_not():
    result = discover_candidates(
        ["recent biology research"], env=ENV,
        searcher=lambda query, timeout: [
            "https://www.nature.com/articles/synthetic-www",
            "https://attacker.nature.com/articles/synthetic-subdomain",
        ],
    )
    assert result.ok
    assert [candidate.url for candidate in result.candidates] == [
        "https://www.nature.com/articles/synthetic-www"
    ]


def test_openreview_only_accepts_public_forum_landing_page():
    result = discover_candidates(
        ["recent machine learning research"], env=ENV,
        searcher=lambda query, timeout: [
            "https://openreview.net/profile?id=Example_Author1",
            "https://openreview.net/forum?id=synthetic-paper-id",
        ],
    )
    assert result.ok
    assert [candidate.url for candidate in result.candidates] == [
        "https://openreview.net/forum?id=synthetic-paper-id"
    ]
    assert result.candidates[0].source_kind == "review-platform"


def test_query_coverage_ranks_repeated_primary_candidate_first():
    calls = []

    def searcher(query, timeout):
        calls.append((query, timeout))
        if len(calls) == 1:
            return [
                "https://nature.com/articles/synthetic-a",
                "https://arxiv.org/abs/2610.11111",
            ]
        return [
            "https://arxiv.org/abs/2610.11111",
            "https://science.org/doi/10.0000/synthetic-b",
        ]

    result = discover_candidates(
        ["complex systems research", "emergent systems research"],
        env=ENV,
        searcher=searcher,
    )
    assert result.ok
    assert result.candidates[0].url == "https://arxiv.org/abs/2610.11111"
    assert result.candidates[0].query_indexes == (0, 1)
    assert [candidate.rank for candidate in result.candidates] == [1, 2, 3]
    assert len(calls) == 2
    assert all(0 < timeout <= 8 for _query, timeout in calls)


def test_invalid_or_private_query_holds_before_search():
    called = []
    result = discover_candidates(
        ["token=private-secret"], env=ENV,
        searcher=lambda *args: called.append(args) or [],
    )
    assert result.status == "invalid_input"
    assert called == []


def test_discovery_is_bounded_to_three_queries_and_twelve_candidates():
    too_many = discover_candidates(
        ["a", "b", "c", "d"], env=ENV,
        searcher=lambda *args: (_ for _ in ()).throw(AssertionError("search")),
    )
    assert too_many.status == "invalid_input"

    urls = [f"https://arxiv.org/abs/2610.{index:05d}" for index in range(20)]
    result = discover_candidates(
        ["bounded discovery"], env=ENV,
        searcher=lambda query, timeout: urls,
    )
    # search_public itself is consumed only up to its first eight returned URLs.
    assert len(result.candidates) == 8


def test_failed_search_does_not_fallback_to_another_provider():
    calls = []

    def searcher(query, timeout):
        calls.append(query)
        raise RuntimeError("synthetic transport failure")

    result = discover_candidates(["recent HCI research"], env=ENV, searcher=searcher)
    assert result.status == "unavailable"
    assert calls == ["recent HCI research"]
