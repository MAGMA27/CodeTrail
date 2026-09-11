"""Export the deterministic JSON Schema artifact for CodeTrail contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .contracts import (
    CancelRunRequest,
    CancelRunResponse,
    CodeEdge,
    CreateRunRequest,
    CreateRunResponse,
    Event,
    EventSequence,
    Evidence,
    EvidenceReference,
    Feature,
    FeatureAssociation,
    FeatureGeneration,
    FeatureGenerationBudget,
    FeatureGenerationError,
    FeatureGenerationRequest,
    FeatureGenerationResponse,
    FeatureGenerationListResponse,
    FeatureGenerationSummary,
    FeatureGenerationUsage,
    FeatureGraphEdge,
    FeatureGraphNode,
    FeatureGraphResponse,
    FeatureGraphSnapshot,
    FeatureModelOutput,
    EvidenceResponse,
    FixtureResponseSet,
    GraphResponse,
    GraphSnapshot,
    IndexSnapshot,
    Issue,
    McpError,
    McpFeatureGenerationListResponse,
    McpFeatureGraphResponse,
    McpFeatureGenerationResponse,
    McpImportResponse,
    McpImportStatusResponse,
    McpIndexResponse,
    McpNeighborsResponse,
    McpOverviewResponse,
    McpProjectIndexesResponse,
    McpProjectListResponse,
    McpProjectResponse,
    McpSearchResponse,
    McpSourceResponse,
    ModelOutput,
    ImportAttempt,
    ImportAttemptResponse,
    GitHubImportRequest,
    GitHubImportResponse,
    LocalImportRequest,
    LocalImportResponse,
    OverviewCoverage,
    OverviewEntryPoint,
    OverviewModule,
    OverviewPath,
    ProjectOverview,
    ReadSourceParams,
    Report,
    ReportResponse,
    Run,
    RunStatusResponse,
    SseEventFrame,
    Symbol,
    ToolRequest,
    ToolResult,
    UnresolvedReference,
    SCHEMA_VERSION,
)


SCHEMA_OUTPUT = Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "codetrail.json"


def build_schema_document() -> dict[str, Any]:
    models: dict[str, Any] = {
        "CancelRunRequest": CancelRunRequest.model_json_schema(),
        "CancelRunResponse": CancelRunResponse.model_json_schema(),
        "CodeEdge": CodeEdge.model_json_schema(),
        "CreateRunRequest": CreateRunRequest.model_json_schema(),
        "CreateRunResponse": CreateRunResponse.model_json_schema(),
        "Event": TypeAdapter(Event).json_schema(),
        "EventSequence": EventSequence.model_json_schema(),
        "Evidence": Evidence.model_json_schema(),
        "EvidenceReference": EvidenceReference.model_json_schema(),
        "Feature": Feature.model_json_schema(),
        "FeatureAssociation": FeatureAssociation.model_json_schema(),
        "FeatureGeneration": FeatureGeneration.model_json_schema(),
        "FeatureGenerationBudget": FeatureGenerationBudget.model_json_schema(),
        "FeatureGenerationError": FeatureGenerationError.model_json_schema(),
        "FeatureGenerationRequest": FeatureGenerationRequest.model_json_schema(),
        "FeatureGenerationResponse": FeatureGenerationResponse.model_json_schema(),
        "FeatureGenerationListResponse": FeatureGenerationListResponse.model_json_schema(),
        "FeatureGenerationSummary": FeatureGenerationSummary.model_json_schema(),
        "FeatureGenerationUsage": FeatureGenerationUsage.model_json_schema(),
        "FeatureGraphEdge": FeatureGraphEdge.model_json_schema(),
        "FeatureGraphNode": FeatureGraphNode.model_json_schema(),
        "FeatureGraphResponse": FeatureGraphResponse.model_json_schema(),
        "FeatureGraphSnapshot": FeatureGraphSnapshot.model_json_schema(),
        "FeatureModelOutput": FeatureModelOutput.model_json_schema(),
        "EvidenceResponse": EvidenceResponse.model_json_schema(),
        "FixtureResponseSet": FixtureResponseSet.model_json_schema(),
        "GraphResponse": GraphResponse.model_json_schema(),
        "GraphSnapshot": GraphSnapshot.model_json_schema(),
        "ImportAttempt": ImportAttempt.model_json_schema(),
        "ImportAttemptResponse": ImportAttemptResponse.model_json_schema(),
        "GitHubImportRequest": GitHubImportRequest.model_json_schema(),
        "GitHubImportResponse": GitHubImportResponse.model_json_schema(),
        "IndexSnapshot": IndexSnapshot.model_json_schema(),
        "Issue": Issue.model_json_schema(),
        "McpError": McpError.model_json_schema(),
        "McpFeatureGenerationListResponse": McpFeatureGenerationListResponse.model_json_schema(),
        "McpFeatureGenerationResponse": McpFeatureGenerationResponse.model_json_schema(),
        "McpFeatureGraphResponse": McpFeatureGraphResponse.model_json_schema(),
        "McpImportResponse": McpImportResponse.model_json_schema(),
        "McpImportStatusResponse": McpImportStatusResponse.model_json_schema(),
        "McpIndexResponse": McpIndexResponse.model_json_schema(),
        "McpNeighborsResponse": McpNeighborsResponse.model_json_schema(),
        "McpOverviewResponse": McpOverviewResponse.model_json_schema(),
        "McpProjectIndexesResponse": McpProjectIndexesResponse.model_json_schema(),
        "McpProjectListResponse": McpProjectListResponse.model_json_schema(),
        "McpProjectResponse": McpProjectResponse.model_json_schema(),
        "McpSearchResponse": McpSearchResponse.model_json_schema(),
        "McpSourceResponse": McpSourceResponse.model_json_schema(),
        "ModelOutput": ModelOutput.model_json_schema(),
        "LocalImportRequest": LocalImportRequest.model_json_schema(),
        "LocalImportResponse": LocalImportResponse.model_json_schema(),
        "OverviewCoverage": OverviewCoverage.model_json_schema(),
        "OverviewEntryPoint": OverviewEntryPoint.model_json_schema(),
        "OverviewModule": OverviewModule.model_json_schema(),
        "OverviewPath": OverviewPath.model_json_schema(),
        "ProjectOverview": ProjectOverview.model_json_schema(),
        "ReadSourceParams": ReadSourceParams.model_json_schema(),
        "Report": Report.model_json_schema(),
        "ReportResponse": ReportResponse.model_json_schema(),
        "Run": Run.model_json_schema(),
        "RunStatusResponse": RunStatusResponse.model_json_schema(),
        "SseEventFrame": SseEventFrame.model_json_schema(),
        "Symbol": Symbol.model_json_schema(),
        "ToolRequest": ToolRequest.model_json_schema(),
        "ToolResult": TypeAdapter(ToolResult).json_schema(),
        "UnresolvedReference": UnresolvedReference.model_json_schema(),
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "schema_version": SCHEMA_VERSION,
        "generated_by": "uv run python -m app.export_schema",
        "models": models,
    }


def main() -> None:
    document = build_schema_document()
    SCHEMA_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(SCHEMA_OUTPUT)


if __name__ == "__main__":
    main()
