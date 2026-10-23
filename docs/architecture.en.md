# Architecture: make verification part of execution

[Back to the English homepage](../README.md) · [中文版](architecture.md)

The architecture separates **what a model proposes**, **what a symbolic mechanism can execute**, and **what the available evidence allows the application to conclude**. The goal is not to add an approval label to an answer after the fact, but to let implemented checks affect state transitions and delivered output.

This is a mechanism-level reference implementation extracted from a bridge-inspection application. The release combines extracted code with explicit public adapters and synthetic examples. It is not a complete production deployment, a formal proof of arbitrary model output, or an engineering safety certification.

![Execution contract and control boundaries.](assets/execution-contract.svg)

## 1. Responsibilities and authority

| Layer | Responsibility | Limit |
|---|---|---|
| Model / semantic layer | Interpret descriptions, suggest candidate fields and actions, organize language | Missing values must not silently become facts; model output does not grant write or sign-off authority |
| Symbolic layer | Represent objects, units, constraints, formulas, state, provenance, and available evidence capabilities explicitly | A formula is meaningful only under its applicability conditions; structured fields are not automatically true |
| Verification layer | Check the implemented object, count, numeric, citation, scope, and state conditions | Another model agreeing is not evidence; unsupported semantics may still be wrong |
| Residual control | Track unmet requirements and decide what information or action is still needed | Do not erase unknowns to claim completion; this is not a mathematical convergence proof |
| Memory governance | Extract accepted procedures, review them, match scope and prerequisites, influence candidate order | Historical engineering facts must not become facts about a new asset; rolled-back steps are not reusable accepted routes |
| Audit / execution support | Record attempts, decisions, checkpoints, and task retries | Logs are not certification; retries do not automatically give exactly-once external side effects |

The diagrams show responsibilities, not a universal pipeline that every request executes. Whether a request uses a particular module depends on the domain adapter and the entry point.

## 2. Put input into a checkable structure

Natural-language tasks and inspection reports are independent input paths:

- The semantic path preserves the object, time range, goal, and prohibitions.
- The report path parses supported DOCX tables and retains the document hash, table, row, column, and original text.

A domain adapter can connect these paths to a common observation contract. They are not one universal document-understanding model, and neither path establishes that the input describes the actual site correctly.

### A synthetic intake example

A worker describes reinforcement exposure on the underside of T-girder `1-1`, “a palm long and two fingers wide.” The system keeps the informal description but does not invent metric dimensions. The residual records missing fields and guides the next question. A later statement, “measured length 1.5 metres, width 10 centimetres,” can be normalized to `1.5 m × 0.1 m`. Unresolved identity and photographic-evidence checks remain unresolved.

![Residual-driven intake from an informal description to normalized measurements with verification gaps retained.](assets/residual-intake.svg)

The runnable example uses a rule-based Chinese parser. English figure labels are explanatory translations, not a claim of a fully multilingual intake implementation. An LLM hook is available through `compile_intake(..., llm_call=...)`; callers explicitly supply and govern that integration.

The important transition is **informal input → candidate record with provenance and unresolved conditions**, not “language → verified engineering truth.”

## 3. The kernel's actual execution order

`LoopManager` orders proposers by priority and calls them to collect candidates. It then passes candidates to `VerifierGate` one by one. The first accepted candidate is given to `MainStateUpdater`. Rejected or deferred candidates do not receive that acceptance; an interruption exits the current candidate-checking pass.

`RuntimeController` may continue with another iteration and record escalation advice. It does **not** automatically assign a person or switch models. It also checks covered state invariants and, when those fail, restores the covered previous state and records the event.

This has several practical consequences:

1. **Proposers should propose.** Do not perform irreversible external writes inside `propose()`.
2. **Priority is not lazy execution.** A cheap candidate being accepted does not mean an expensive proposer was never called. This ordering is not evidence of a token-saving percentage.
3. **`ACCEPT` has a scope.** It is permission under the implemented checks, not a proof of an arbitrary proposition.
4. **Attempt history is not committed state.** Read transitions together with rollback decisions before treating an attempted step as durable progress.
5. **A bounded answer can still be useful.** Missing evidence may lead to a conditional answer, not necessarily complete silence until every goal is established.

## 4. Residual reasoning: missing evidence changes the result

The residual holds goals, unresolved tasks, unknown evidence, and constraints. After accepted actions update state, the remaining requirements can be rebuilt. Final response construction uses the current state and its limits.

The synthetic bridge examples remove evidence from the input, rather than merely adding cautionary prose to a fixed answer:

- Missing or invalid scoring inputs prevent the demonstrated cross-score risk classification.
- A genuine numeric zero is retained as zero; it is not treated as missing.
- “No load test was performed” and “no maintenance record is available” do not become evidence that such records exist.
- Supplied design life and elapsed service years do not justify a remaining-life estimate without a calibrated life model.

`solved` means the current response task has reached a bounded result. It does not mean the bridge has been repaired or that all evidence is complete. Inspect `completion_mode=conditional` and `unknowns` as well.

The example's scoring values are synthetic inputs, not independently recomputed field assessments. Deployment requires versioned, applicable, authorized engineering rules and review of upstream records.

## 5. Forward retrieval and reverse verification

**Forward retrieval** searches an explicitly supplied graph and clause index for candidate support based on the component and defect.

**Reverse verification** takes checkable claims from a proposed answer and compares them with current tool results and the clause index. It checks what the answer says, not only whether something relevant was retrieved earlier.

For example:

- A bearing's score cannot be reused as a girder's score just because both numbers appear in the result set.
- A nonexistent clause cannot remain as supporting evidence.
- A clause that exists but is unrelated to the component or defect produces a review warning, not a claim of certified applicability.

The public `safe_delivery` path checks the verification result's structure, counts, and binding to the original answer. An invalid result or empty valid body is withheld. It does not silently return the original unverified answer on a verification failure.

**Hard blocks and soft warnings are distinct.** Warning-marked text may be shown for professional review and must not be mistaken for a formally issued assessment. The public adapter rejects an empty clause index rather than silently downgrading the check while describing it as complete verification.

The graph stores explicitly supplied relationships; its weights are not probabilities of correctness. The synthetic clause identifier `JTG 0000-2099` is deliberately fictional and is not an engineering standard.

This is a concrete error-control chain, not complete semantic entailment checking. A causal claim outside the implemented coverage may survive. A successful check also does not independently authenticate the underlying field data.

## 6. Governed memory: reuse a route, not an old answer

The process is:

**Execution record → uniquely matched accepted, non-rolled-back actions → candidate memory → review → scope and prerequisite matching → candidate-order guidance → fresh verification.**

In the synthetic example, approving a priority-analysis route changes the next task's accepted action order from `risk_assess → priority` to `priority → risk_assess`. The new bridge's values still come from its own inputs. A retrieved pattern lacking current prerequisite evidence cannot be applied just because the topic matches.

The public extractor excludes an entire step if a rollback event is present. An action must have a candidate identifier and exactly one matching `ACCEPT` decision. Missing alignment, conflicting decisions, rejected candidates, or aggregate acceptance counts do not establish a reusable accepted step.

The current memory matcher uses deterministic contextual coordinates and scope checks. It is application-level procedure retrieval and route selection—not KV-cache reuse, automatic training, or a theorem prover. Review approval is necessary but not sufficient, and a matching score is not a correctness probability.

Callers remain responsible for trustworthy execution logs and real review identities. This release does not cryptographically prove that a supplied audit record is authentic.

## 7. Computation, checkpoints, and side effects

The public calculator uses a bounded AST interpreter rather than Python `eval` for user expressions. Registered Python callables and lookup providers are still trusted application code; this is not an operating-system sandbox.

The public scoring adapter keeps reported values separate from recomputed values. Its synthetic component examples demonstrate that changing actual inputs changes the final calculated result. It does not tune results toward a desired narrative and does not issue a formal whole-bridge grade.

Checkpoint persistence, rollback, and retry solve different problems:

- A checkpoint preserves the supported task/state record.
- Covered internal rollback restores covered memory state after an invariant failure.
- The task runner can retry a whole task.

These mechanisms do not establish exact mid-step resumption or undo a sent work order, an external write, or a model charge. A production integration must separately design authorization, idempotency, and compensation for external effects.

Native trace examples record actual tool-call boundaries in OTLP JSON. They do not fabricate model spans when no model call occurs. Trace output may contain inputs and outputs; redaction, retention, access control, and any required external format validation remain deployment responsibilities.

## 8. Add a domain deliberately

1. Define `DomainProfile` / `ObservationSchema` and distinguish source observations from inferred candidates.
2. Supply domain vocabulary, object identifiers, versioned rules, units, and evidence contracts.
3. Implement proposers and checkers with real decision coverage, not approval-shaped placeholders.
4. Put external actions behind an authorized commit boundary.
5. Test the final state and delivered response after withdrawing evidence, changing values, swapping object identities, introducing incompatible memory, and injecting failures.

Changing “bridge” to “road,” “law,” or “mathematics” in a prompt is not a validated domain migration. Rule validity, model-output coverage, evidence authenticity, and institutional responsibility must be checked again.

## Read next

- [Runnable examples](../examples/) and [tests](../tests/)
- [Security and engineering safety](../SECURITY.md)
- [Contribution requirements](../CONTRIBUTING.md)
- [Source map](source-map.md), [public adaptations](public-adaptations.md), and [release boundaries](release-boundaries.md) (Chinese)
- [Reverse RAG](reverse-rag.md), [residual reasoning and memory](residual-and-memory.md), and [checkpoint recovery](checkpoints-and-recovery.md) (Chinese)
