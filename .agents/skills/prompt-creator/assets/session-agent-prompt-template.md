# Session Prompt Template

This is a reusable template, not a completed prompt. Preserve every section the caller or template contract marks as required. Remove only unused optional sections and replace every `{{PLACEHOLDER}}` before delivery.

Use repository-relative paths or a named working-directory placeholder for a portable or shared prompt. Use a verified, resolved absolute path only when this is an explicitly private, same-machine handoff.

## Objective

{{ONE_USER_VISIBLE_OUTCOME}}

## Mode

{{ANSWER | REVIEW | DIAGNOSE | RESEARCH | PLAN | IMPLEMENT | HANDOFF}}

This mode authorizes: {{ALLOWED_ACTIONS}}. It does not authorize: {{EXCLUDED_ACTIONS}}.

## Live Instructions And Owners

- Working directory or repository root: `{{WORKING_DIRECTORY_OR_REPOSITORY_ROOT}}`
- Read and follow: {{ROOT_AND_SCOPED_INSTRUCTION_PATHS}}
- Canonical owner: {{OWNER_PATH_OR_DISCOVERY_ACTION}}
- Live consumer: {{CONSUMER_PATH_OR_NOT_APPLICABLE}}

Inherit the active platform, developer, user, and repository instruction hierarchy. Do not use this prompt to replace or reorder it.

## Verified Baseline

- User requirements: {{ACCEPTED_REQUIREMENTS}}
- Completed work and rationale: {{COMPLETED_WORK_AND_WHY_OR_NONE}}
- Current owners and behavior: {{VERIFIED_FACTS}}
- Checks run and observed results: {{FRESH_COMMANDS_AND_RESULTS_OR_NONE}}
- In-scope changes to preserve: {{FILES_OR_NONE}}

## Assumptions And Open Evidence

- Assumptions: {{ASSUMPTIONS_OR_NONE}}
- Facts to verify: {{FACTS_TO_VERIFY_OR_NONE}}
- External gates: {{CREDENTIAL_APPROVAL_PRIVATE_DOC_OR_NONE}}

## Scope

In scope:

- {{NAMED_FILE_OWNER_PLAN_ITEM_OR_BEHAVIOR}}

Excluded:

- {{UNRELATED_DIRTY_CONCURRENT_OR_ADJACENT_WORK}}

## Next Executable Action

{{FIRST_USEFUL_READ_COMMAND_EDIT_OR_PROBE}}

## Requirements

- {{TASK_SPECIFIC_REQUIREMENT}}
- {{TASK_SPECIFIC_REQUIREMENT}}

## Workflow

1. {{ORDERED_STEP_ONLY_WHEN_ORDER_AFFECTS_CORRECTNESS}}
2. {{ORDERED_STEP}}
3. {{ORDERED_STEP}}

## Validation

- {{VERIFIED_OWNER_COMMAND_OR_CHECK_CATEGORY_AND_DISCOVERY_STEP}}
- {{FAILURE_OR_BOUNDARY_CASE_WHEN_RELEVANT}}

Do not claim success for a check that was not run. Keep external, destructive, costly, or shared-state validation behind the applicable approval boundary.

## Failure And Stop Behavior

- If {{MISSING_EVIDENCE_OR_FAILURE}}, {{RETRY_ASK_NARROW_OR_REPORT_BEHAVIOR}}.
- Stop before {{UNAUTHORIZED_OR_AMBIGUOUS_ACTION}}.
- Stop successfully when {{OBSERVABLE_COMPLETION_BAR}}.

## Output

Return:

1. {{PRIMARY_RESULT}}
2. {{FILES_OR_ARTIFACTS_CHANGED_WHEN_APPLICABLE}}
3. {{VALIDATION_AND_OBSERVED_RESULTS}}
4. {{MATERIAL_ASSUMPTIONS_BLOCKERS_OR_REMAINING_RISKS}}
