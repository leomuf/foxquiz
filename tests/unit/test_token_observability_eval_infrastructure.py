# SPDX-FileCopyrightText: 2026 Leonardo Muffato (AUTOSOFT Engineering)
#
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for token-observability evaluation infrastructure."""

import json
from pathlib import Path

import yaml
from vertexai import types as vertex_types

PROJECT_ROOT = Path(__file__).parents[2]
EVAL_DIR = PROJECT_ROOT / "tests" / "eval"
DATASET_DIR = EVAL_DIR / "datasets"


def test_token_observability_datasets_seed_workflow_metadata() -> None:
    """Verify every generated dataset case carries valid Workflow metadata."""
    dataset_names = (
        "token-observability-pilot.json",
        "token-observability-rollout.json",
        "token-observability-regression.json",
        "structured-request-contract-safe.json",
        "structured-request-contract-malicious.json",
    )

    for dataset_name in dataset_names:
        dataset = json.loads((DATASET_DIR / dataset_name).read_text(encoding="utf-8"))
        vertex_types.EvaluationDataset.model_validate(dataset)
        assert dataset["eval_cases"]
        for case in dataset["eval_cases"]:
            assert case["agent_data"] == {
                "agents": {
                    "root_agent": {
                        "agent_id": "root_agent",
                        "agent_type": "Workflow",
                        "description": (
                            "Interactive School Exam Preparation Companion (FoxQuiz)"
                        ),
                        "sub_agents": [],
                    }
                },
                "turns": [],
            }


def test_token_observability_judge_requires_structured_json() -> None:
    """Verify the fulfillment judge requires bounded schema-valid JSON output."""
    config = yaml.safe_load(
        (EVAL_DIR / "token_observability_eval_config.yaml").read_text(encoding="utf-8")
    )
    metric = next(
        metric
        for metric in config["custom_metrics"]
        if metric["name"] == "quiz_request_fulfillment"
    )
    vertex_types.LLMMetric.model_validate(metric)
    generation_config = metric["judge_model_generation_config"]

    assert generation_config["temperature"] == 0
    assert generation_config["max_output_tokens"] == 512
    assert generation_config["response_mime_type"] == "application/json"
    assert generation_config["response_json_schema"] == {
        "type": "object",
        "additionalProperties": False,
        "required": ["score", "explanation"],
        "properties": {
            "score": {"type": "integer", "minimum": 1, "maximum": 5},
            "explanation": {"type": "string", "minLength": 1},
        },
    }
    assert "{prompt}" in metric["prompt_template"]
    assert "{response}" in metric["prompt_template"]
    assert "{agent_data}" not in metric["prompt_template"]
