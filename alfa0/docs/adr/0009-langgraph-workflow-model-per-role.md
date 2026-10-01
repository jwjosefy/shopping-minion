# ADR-0009: Workflow on LangGraph as a deterministic graph; one configurable model per role

- **Status:** Accepted. "No fallback in v0" superseded for intake by [ADR-0011](0011-intake-fallback-model.md).
- **Date:** 2026-09-29

## Context

The system uses different kinds of model calls: reading a photo, choosing among candidates, and exploring a website. Each one fits a different model and cost. Johann wants to route between providers and models without coupling the code to any one of them.

The flow itself is fixed: intake → review → search → decide → act → report. The first design idea included an orchestrator agent on a large model. When asked what it would decide that a fixed flow doesn't, the answer was "clarifying ambiguous items", and that is v1 scope (see [ADR-0008](0008-low-confidence-decisions-added-and-flagged.md)).

## Options considered

1. **Plain Python pipeline plus provider SDKs.** Minimal, but checkpointing, per-item fan-out and future human-in-the-loop interrupts would all be built by hand.
2. **LangGraph with an LLM orchestrator** that chooses the next step. Flexible, but it adds cost, latency and nondeterminism for a flow that doesn't need decisions in v0. It also works against ADR-0005's spirit of keeping execution predictable.
3. **LangGraph as a deterministic graph, with models only inside specific nodes.**

## Decision

Option 3.

- The workflow is a LangGraph state graph with fixed edges. It gives us state, per-item processing, checkpointing (a run can resume after a failure), and interrupts for the v1 clarification step.
- Every model call belongs to a **role** (`intake`, `resolver`, `discovery`), configured in `config/models.yaml`. Chat models go through LangChain's provider-agnostic `init_chat_model`, and decision backends implement their own interface ([ADR-0010](0010-single-pass-typed-resolver.md)).
- v0 defaults: `intake` on Claude Sonnet 5.5 (Haiku 4.5 as an option), `resolver` backend chosen by evals, `discovery` on Claude Opus 5.5.
- **No fallback in v0.** The field is reserved in the config and left unset.
- GLM 5.3 is left out of v0. The places to add it are marked `TODO(glm)`.

## Consequences

- Swapping a model or provider is a config change plus an API key in `.env`.
- The graph is inspectable, and the same inputs follow the same path. That makes runs comparable across evals.
- An LLM orchestrator can still be added later, as a node, once there's a real decision for it to make.
- Cost: LangGraph and LangChain are new dependencies and a learning curve (Johann hasn't used LangGraph before). This project is also how that experience gets built.
