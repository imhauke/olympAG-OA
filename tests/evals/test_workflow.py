from pathlib import Path

import yaml


def test_live_workflow_is_manual_and_keeps_inputs_out_of_shell_source() -> None:
    workflow = yaml.load(Path(".github/workflows/evals.yml").read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    job = workflow["jobs"]["evaluate"]
    assert job["env"]["GEMINI_API_KEY"] == "${{ secrets.GEMINI_API_KEY }}"
    assert job["env"]["ANTHROPIC_API_KEY"] == ""
    command = next(
        step["run"] for step in job["steps"] if step.get("name") == "Evaluate synthetic cases"
    )
    assert "${{ inputs." not in command
    assert '"${args[@]}"' in command
    upload = job["steps"][-1]
    assert upload["if"] == "always()"
