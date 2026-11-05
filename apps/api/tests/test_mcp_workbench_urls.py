"""Keep MCP workbench links aligned with routes and query readers in the web app."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.contracts import IndexSnapshot, JourneyEvidenceRef, JourneyStep, ProjectJourney, SymbolRef
from app.mcp_server import _citation_url, _mcp_journey_response, _session_url


ROOT = Path(__file__).resolve().parents[3]
WEB_APP = ROOT / "apps" / "web" / "app"


def test_mcp_workbench_urls_match_web_query_readers_and_routes() -> None:
    citation = _citation_url(
        "https://codetrail.example",
        project_id="project-a",
        index_id="index-a",
        path="src/main.py",
        start_line=3,
        end_line=5,
        content_hash="hash-a",
        evidence_id="evidence-a",
    )
    session = _session_url("https://codetrail.example", "project-a", "session-a")
    urls = [urlsplit(citation), urlsplit(session)]

    workbench_source = (WEB_APP / "workbench.tsx").read_text(encoding="utf-8")
    navigation_source = (ROOT / "apps" / "web" / "lib" / "workbench-navigation.ts").read_text(encoding="utf-8")

    evidence_keys_match = re.search(r'const keys = \[([^\]]+)\];', workbench_source)
    assert evidence_keys_match is not None, "workbench evidence URL keys must remain explicitly declared"
    evidence_keys = set(re.findall(r'"([a-z_]+)"', evidence_keys_match.group(1)))
    route_keys = set(re.findall(r'params\.(?:get|has)\("([a-z_]+)"\)', navigation_source))
    web_query_keys = evidence_keys | route_keys

    emitted_query_keys = set().union(*(set(parse_qs(url.query)) for url in urls))
    assert emitted_query_keys <= web_query_keys
    assert emitted_query_keys == {
        "project_id",
        "index_id",
        "path",
        "start_line",
        "end_line",
        "content_hash",
        "evidence_id",
        "session_id",
    }

    route_paths = {
        "/" if page.parent == WEB_APP else "/" + page.parent.relative_to(WEB_APP).as_posix()
        for page in WEB_APP.rglob("page.tsx")
    }
    emitted_paths = {url.path or "/" for url in urls}
    assert emitted_paths <= route_paths


def test_summary_journey_keeps_mcp_evidence_reference_urls() -> None:
    index = IndexSnapshot(
        index_id="index-a",
        repository_id="repo-a",
        revision="revision-a",
        dirty=False,
        snapshot_hash="a" * 64,
        status="ready",
        rules_version="rules-a",
    )
    journey = ProjectJourney(
        project_id="project-a",
        index=index,
        rules_version="rules-a",
        journey_id="journey-a",
        action_label="Save preferences",
        steps=[
            JourneyStep(
                step_id="step-a",
                role="trigger",
                title="Submit form",
                relation_kind="ui_action",
                certainty="static",
                explanation="The form submits the action.",
                symbols=[SymbolRef(id="symbol-a", name="Preferences.save", kind="function", at="src/preferences.py:3-5")],
                evidence=[
                    JourneyEvidenceRef(
                        evidence_id="evidence-a",
                        content_hash="b" * 64,
                        path="src/preferences.py",
                        start_line=3,
                        end_line=5,
                    )
                ],
            )
        ],
    )

    response = _mcp_journey_response(journey, "https://codetrail.example")

    assert response.ok is True
    assert response.journey is not None
    assert response.references[0].evidence_id == "evidence-a"
    assert response.references[0].url == _citation_url(
        "https://codetrail.example",
        project_id="project-a",
        index_id="index-a",
        path="src/preferences.py",
        start_line=3,
        end_line=5,
        content_hash="b" * 64,
        evidence_id="evidence-a",
    )

