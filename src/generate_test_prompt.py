"""Clone a repository when needed and create a Copilot test-generation prompt."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


TOOL_DIR = Path(__file__).resolve().parent.parent  # Point to root directory
PROMPT_FILE = TOOL_DIR / "Test_Prompt.md"


def repository_name(repository_url: str) -> str:
    """Return a safe local directory name from a GitHub-style URL."""
    parsed = urlparse(repository_url.strip())
    name = Path(parsed.path.rstrip("/")).name
    if name.endswith(".git"):
        name = name[:-4]
    if not name or name in {".", ".."}:
        raise ValueError("The repository URL does not contain a valid repository name.")
    return name


def is_git_repository(path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def ensure_repository(repository_url: str, clone_root: Path) -> Path:
    repository_url = repository_url.strip()
    clone_root.mkdir(parents=True, exist_ok=True)
    target = clone_root / repository_name(repository_url)

    if target.exists():
        if is_git_repository(target):
            print(f"Using existing repository: {target}")
            return target
        raise RuntimeError(
            f"The target folder already exists but is not a Git repository: {target}"
        )

    print(f"Cloning repository into: {target}")
    try:
        subprocess.run(
            ["git", "clone", repository_url, str(target)],
            check=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Git is not installed or is not available on PATH.") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"Git clone failed with exit code {error.returncode}.") from error
    return target


def build_prompt(repository_url: str, repository_path: Path) -> str:
    return f"""# Enterprise Test Generation Prompt

Analyze and test the repository below.

- Repository URL: `{repository_url}`
- Local repository path: `{repository_path}`

You are an Enterprise Test Generation Agent acting as a software architect, QA architect,
test automation engineer, business analyst, and repository analysis agent. Work from the
actual repository code; never generate tests blindly.

## Required workflow

Execute these phases strictly in order:

1. **Repository Discovery**: inspect README files, design and ADR documents, package and
   build configuration, CI/CD configuration, environment configuration, source code,
   APIs, controllers, services, models, utilities, UI components, and data-access layers.
2. **Code Understanding**: document the application purpose, architecture, business rules,
   data flow, validation, authentication, authorization, error handling, integrations,
   dependencies, critical functionality, and risk areas.
3. **Feature Identification**: create a feature inventory with feature name, description,
   source files, dependencies, complexity, and risk level (High, Medium, or Low).
4. **Specification Generation**: create one numbered Markdown specification per logical
   feature under `tests/specs/`, using the required sections below.
5. **Human Approval Gate**: stop and present the review summary. Do not create test scripts
   until the user explicitly replies `Approve`, `Continue`, `Generate Tests`, or `Approved`.
6. **Test Script Generation**: after approval, create one synchronized test script for each
   approved specification under `tests/test_scripts/`.
7. **Coverage Analysis**: create `tests/coverage/coverage_summary.md`.
8. **Test Execution**: execute tests when possible and record passed, failed, skipped, and
    blocked tests. Document missing dependencies or infrastructure when execution is blocked.
    After every test execution, overwrite `tests/Result.md` with the newest run results.
9. **Results Reporting**: create `tests/Results.md` and `tests/README.md`.

## Required output structure

Maintain this structure in the repository under test:

```text
tests/
├── README.md
├── Result.md
├── Results.md
├── coverage/
│   └── coverage_summary.md
├── specs/
│   ├── 001_feature_name.md
│   ├── 002_feature_name.md
│   └── ...
└── test_scripts/
    ├── 001_feature_name_test.<ext>
    ├── 002_feature_name_test.<ext>
    └── ...
```

Create directories as needed and preserve existing artifacts. Replace `<ext>` with the
repository-standard test language extension. Every specification must have exactly one
matching script with the same numeric prefix and feature name, for example:

```text
tests/specs/001_login.md
tests/test_scripts/001_login_test.py
```

Before explicit human approval, create only `tests/specs/*.md` files and the minimum parent
directories required for them. Do not create test scripts, coverage reports, `Results.md`,
`Result.md`, or `README.md` before approval. After approval, complete every remaining file
in the tree.

## Specification format

Use sequential names such as `001_feature_name.md`. Each specification must contain:

- Objective
- Functional Overview
- Business Logic
- Source Components
- Preconditions
- Assumptions
- Functional Requirements
- Test Data Requirements
- Test Scenarios: Happy Path, Negative, Boundary, Validation, Error Handling, Security,
  and Integration Tests
- Expected Results
- Risk Assessment
- Automation Feasibility
- Traceability Matrix mapping requirements to scenarios

## Test requirements after approval

Auto-detect and use the repository-standard framework. Otherwise choose the most suitable
framework and explain the choice. Scripts must include metadata, setup, fixtures,
preconditions, independent repeatable test cases, assertions, and cleanup. Cover functional,
happy-path, negative, boundary, validation, error-handling, security, and integration behavior.

Before creating artifacts, inspect existing `tests/` content and prevent duplicate
specifications, scenarios, scripts, and test cases. In incremental mode, create only missing
artifacts and preserve existing work. If an artifact already exists, update it only when
necessary to keep it accurate and synchronized; do not overwrite unrelated user work.

## Coverage and results requirements

`tests/coverage/coverage_summary.md` must include Features Covered, Features Partially
Covered, Features Not Covered, Requirement Coverage %, Feature Coverage %, Estimated Code
Coverage %, Coverage Gaps, and Recommendations.

`tests/Results.md` must include Repository Summary, Features Identified, Specifications
Generated, Test Scripts Generated, Coverage Summary, Execution Results with Passed/Failed/
Skipped/Blocked subsections, Failure Analysis, Risks, Coverage Gaps, and Next Steps. For
each failure, include the test name, failure cause, impact, and recommendation.

`tests/Result.md` is the current test-run result file. It must be overwritten after every
test execution, never appended to, and must contain the execution timestamp, command run,
framework, environment status, total tests, passed, failed, skipped, blocked, failure
details, and any missing dependencies or infrastructure. If execution is blocked, overwrite
the file with the blocked status and the reason. Keep `Results.md` as the consolidated
final report; keep `Result.md` as the latest execution snapshot.

`tests/README.md` must include Repository Summary, Architecture Overview, Features Identified,
Specifications Generated, Test Scripts Generated, Coverage Information, execution commands
appropriate for the detected framework, incremental-mode behavior, duplicate-prevention
strategy, assumptions, and limitations.

## Approval review summary

At the approval gate, report:

1. Repository overview
2. Architecture summary
3. Feature inventory
4. Specifications generated
5. Coverage intent
6. Risks identified
7. Assumptions made

After approval, ensure numbering remains synchronized between specifications and scripts,
generate all requested reports, attempt execution, overwrite `tests/Result.md` with that
run's results, and finish with coverage metrics, risks, recommendations, and next steps.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone a repository if needed and generate Test_Prompt.md for Copilot."
    )
    parser.add_argument("repository_url", nargs="?", help="Git repository URL")
    parser.add_argument(
        "--clone-root",
        type=Path,
        default=TOOL_DIR / "repositories",
        help="Folder containing cloned repositories (default: ./repositories)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROMPT_FILE,
        help="Prompt output path (default: ./Test_Prompt.md)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository_url = (
        input("Repository URL: ").strip()
        if args.repository_url is None
        else args.repository_url.strip()
    )
    if not repository_url:
        print("A repository URL is required.", file=sys.stderr)
        return 2

    try:
        repository_path = ensure_repository(repository_url, args.clone_root.resolve())
        output_path = args.output.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            build_prompt(repository_url, repository_path),
            encoding="utf-8",
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Generated Copilot prompt: {output_path}")
    print("Paste the contents of Test_Prompt.md into Copilot to begin repository analysis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())