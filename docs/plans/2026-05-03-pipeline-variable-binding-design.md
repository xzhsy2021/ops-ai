# OPS Pipeline Variable Binding Design

> Status: Approved
> Date: 2026-05-03

## Goal

Redesign the relationship between pipeline step configuration and application
configuration so that step field values are no longer isolated literal strings.

The target outcome is:

- pipeline steps bind to standard variables
- application configuration becomes a first-class variable source
- runtime resolution is deterministic and explainable
- deploy-time preview can show both the final value and where it came from

## Problem Statement

Current code keeps application config and pipeline step config only loosely
connected.

Examples:

- `Application` stores `deploy_path`, `build_command`, `restart_command`,
  `health_url`, `default_variables`, `pipeline_id`, and `server_group_id`
- `PipelineStep.config` still stores step-specific strings such as:
  - `upload.local_path`
  - `upload.remote_path`
  - `deploy.deploy_path`
  - `deploy.package_path`
  - `restart.cmd`
  - `health_check.url`
- runtime execution currently consumes `req.variables` plus ad-hoc defaults

This creates four operational problems:

1. duplicated maintenance between application config and pipeline config
2. weak pipeline reusability across applications
3. no unified variable precedence model
4. difficult troubleshooting because users cannot see where a value came from

## Selected Product Decisions

The approved design choices are:

- field binding mode, not pure template syntax
- variable precedence:
  - runtime input
  - environment
  - application
  - pipeline step default

## Design Principles

- keep the current single-runtime architecture
- do not introduce a general-purpose expression engine in this phase
- prefer explicit binding metadata over implicit string conventions
- preserve backward compatibility with existing literal-only step config
- make resolution explainable to both backend logs and frontend users

## Variable Model

### Standard Variable Registry

All pipeline execution should resolve through a registry of canonical variable
names instead of direct coupling to application table column names.

Initial standard variables:

- `deploy_path`
- `build_command`
- `restart_command`
- `health_url`
- `artifact_type`
- `artifact_name`
- `artifact_local_path`
- `artifact_remote_path`
- `version`
- `repo`
- `branch`
- `server_group_id`
- `server_names`

Registry entries should define:

- variable name
- user-facing label
- data type
- whether required by any built-in step types
- application field source, if one exists
- environment source key, if one exists

### Application-to-Variable Mapping

The application model becomes one source of standard variables, for example:

- `Application.deploy_path -> deploy_path`
- `Application.build_command -> build_command`
- `Application.restart_command -> restart_command`
- `Application.health_url -> health_url`
- `Application.artifact_type -> artifact_type`
- `Application.repo -> repo`
- `Application.server_group_id -> server_group_id`
- `Application.default_variables.<key> -> <key>`

### Environment-to-Variable Mapping

Environment-level configuration should override application values when both
define the same variable name.

This can come from:

- existing environment records in the database
- future app-environment association data if introduced

The design does not require a new expression language. It only requires that the
resolved environment variable bag is available before execution.

## Step Field Binding Model

Each configurable pipeline step field should support two modes:

### Literal Mode

```json
{
  "mode": "literal",
  "value": "/tmp/app.tar.gz"
}
```

### Binding Mode

```json
{
  "mode": "binding",
  "var": "artifact_remote_path"
}
```

This keeps the UX explicit and avoids string-templating ambiguity.

## Built-In Step Field Mapping

Recommended first-wave binding targets:

### Checkout

- `repo_url -> repo`
- `branch -> branch`

### Build

- `cmd -> build_command`

### Upload

- `local_path -> artifact_local_path`
- `remote_path -> artifact_remote_path`

### Deploy

- `deploy_path -> deploy_path`
- `package_path -> artifact_remote_path`

### Restart

- `cmd -> restart_command`

### Health Check

- `url -> health_url`

### Command

- keep literal-first in phase 1
- allow optional binding only where the command body is simple and controlled

### Wait / Switch

- no binding needed initially beyond existing literals or empty config

## Resolution Precedence

For any variable name, the resolved value must follow this fixed order:

1. runtime input
2. environment
3. application
4. pipeline default

This means:

- the deploy UI can temporarily override a value
- environment configuration overrides app defaults
- application configuration overrides the step’s stored fallback
- the pipeline still remains runnable for simple cases with no app binding

## Resolution Algorithm

At deploy preparation time:

1. load application
2. load bound pipeline
3. load target environment context
4. collect runtime overrides from the deploy form
5. assemble a variable context using the precedence chain
6. resolve each step field:
   - if `mode=literal`, use the literal
   - if `mode=binding`, fetch the variable value
   - if bound variable is missing, fall back to the field default only if the
     field explicitly allows fallback
7. validate required resolved values before execution

Output should include both:

- `resolved_steps`
- `resolution_trace`

## Resolution Trace Model

Every resolved field should record:

- step id / step name
- field name
- final value
- source type:
  - runtime
  - environment
  - application
  - pipeline_default
  - literal
- source key

Example:

```json
{
  "step": "deploy",
  "field": "deploy_path",
  "value": "/data/web/app",
  "source_type": "application",
  "source_key": "app.deploy_path"
}
```

This trace should be usable for:

- pre-deploy preview
- audit records
- failure diagnostics

## Data Structure Changes

### PipelineStep.config

Current `config` is a free-form JSON object. Keep the column, but normalize the
shape for built-in step fields.

Recommended structure:

```json
{
  "fields": {
    "deploy_path": {
      "mode": "binding",
      "var": "deploy_path"
    },
    "package_path": {
      "mode": "binding",
      "var": "artifact_remote_path"
    }
  },
  "defaults": {
    "deploy_path": "/data/web/app",
    "package_path": "/tmp/app.tar.gz"
  }
}
```

Compatibility rule:

- if a step still uses the old flat config shape, treat it as literal-only and
  migrate lazily in the UI or backend compatibility layer

### Application

The current `Application` model is sufficient for phase 1.

No immediate schema change is required if:

- existing app fields remain the source of canonical variables
- `default_variables` remains an extensible JSON bag

Optional future schema changes can be deferred.

## Frontend Interaction Design

### Pipeline Editor

For each built-in step field:

- show a mode switch:
  - fixed value
  - bind to variable
- if bound:
  - show a dropdown of compatible standard variables
- if fixed:
  - show the normal input
- show the field default separately from the binding choice if fallback is
  allowed

### Application Detail Page

Add an explicit “pipeline variable source” section that shows:

- canonical variables derived from app fields
- custom entries from `default_variables`
- which variables are used by the bound pipeline

### Deploy Preview

Before deployment starts, show:

- final resolved values for all required bound fields
- source badges:
  - runtime
  - environment
  - application
  - pipeline default
- blocking warnings for unresolved required values

## Backend API Design

Recommended additions:

### Pipeline Binding Metadata

- enrich pipeline detail responses so each step field can declare:
  - supported variable names
  - current mode
  - current literal
  - current binding

### Application Variable Preview

- add an endpoint to return canonical variables derived from one application

### Resolution Preview

- add a dry-run endpoint for deploy preparation:

```text
POST /api/v2/deploy/resolve
```

Input:

- app id
- pipeline id
- environment
- runtime overrides

Output:

- resolved variable bag
- resolved step fields
- resolution trace
- validation errors

## Validation Rules

The resolver should fail before execution when required values are missing.

Examples:

- deploy step requires resolved `deploy_path`
- upload step requires resolved `artifact_local_path` and `artifact_remote_path`
- restart step requires resolved `restart_command` if the step is enabled
- health check step requires resolved `health_url`

Validation should be explicit and step-type aware.

## Migration Strategy

Phase 1 must support existing pipelines without breaking them.

Migration approach:

1. backend compatibility layer accepts old flat `config`
2. pipeline editor saves new structured `config`
3. existing pipelines can be upgraded when edited
4. optional one-time migration script can normalize common built-in fields later

## Risks

- if variable names are not standardized early, bindings will drift
- if fallback rules are too permissive, users will not know what is actually
  executing
- if the UI hides resolution source, operators will still distrust the pipeline
  model
- if command steps get full templating too early, complexity will grow faster
  than operational value

## Recommended Delivery Scope

Phase 1 should only cover:

- standard variable registry
- field binding mode
- precedence resolver
- preview/trace output
- built-in step support for checkout/build/upload/deploy/restart/health_check

Do not add:

- arbitrary expressions
- nested computed formulas
- full command templating
- user-defined resolver plugins

## Success Criteria

This design is realized when:

- application config can directly drive built-in pipeline step values
- the variable precedence chain is fixed and visible
- pipeline templates can be reused across applications with minimal hand-editing
- deploy preview can explain every resolved value and its source
- old pipelines continue to run through compatibility logic
