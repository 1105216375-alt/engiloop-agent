# Three reproducible EngiLoop demonstrations

These examples connect supplied inputs to actual code paths and inspect the resulting output. They are synthetic, offline mechanism demonstrations. They do not call an LLM, load private archives, use real standards, or provide engineering sign-off.

## Run

After installing `.[dev]` from the repository root:

```bash
python -m examples.showcase
python -m examples.showcase report --json
python -m examples.showcase residual --json
python -m examples.showcase rules --json
python -m pytest tests/test_showcase.py
```

The human-readable summary is generated from the returned results. `--json` includes inputs, source coordinates, actual checker issues, reasoning replies, residuals, and rule bindings. No model outputs or historical logs are fabricated.

## Report conflict

The example creates a DOCX in memory with a supported defect table and two erroneous draft paragraphs. The existing table adapter parses the defect record and records document hash and table/row/column provenance. A small explicit paragraph reader passes the draft text to the existing claim verifier.

Separate synthetic evidence gives the main girder risk score as 80 and the bearing risk score as 30. The draft's 92 is rejected. Its nonexistent citation is rejected. A separate wrong-object probe tries to use 30 for the girder and is also rejected. A template constructs the replacement from the current evidence, and that replacement is checked again.

This does not establish that arbitrary reports can be understood, that supplied evidence is authentic, or that every causal statement is correct. The clause index contains a deliberately fictitious standard. There is no model self-correction claim: the replacement is scripted, while the checks execute the actual verifier.

## Residual completion

One synthetic bridge session starts without either of its scoring inputs. The example calls the extracted `BridgeReasoningMode`, supplies Dr, calls it again, supplies BCI, and calls it again. Remaining scoring requirements shrink from two to one to zero. The actual reply changes from a missing-evidence boundary to a bounded rule-based screening.

Dr and BCI are fixture inputs, not calculated from the accompanying intake example. They do not establish the applicability of a scoring standard. Each stage re-evaluates the updated session; this is not exact instruction-level resumption. The runtime can mark a bounded response `solved` while unknown evidence remains, so the demonstration reports both the unknowns and the presence of the requested screening rather than treating `solved` as engineering completion.

Two additional examples are explicitly separate:

- The public single-defect intake adapter asks for missing fields and normalizes supplied measurements. Missing fields shrink from four to two to zero, but source/identity review stays open and scoring eligibility remains false.
- The extracted runtime rejects a candidate before commit, then a separate fault injection demonstrates rollback after an invalid update. Internal rollback does not reverse external side effects.

## Rule-pack update

`examples/fixtures/rules_v1.json` and `rules_v2.json` contain invented component/disease/scale-to-deduction mappings. The v2 main-girder deduction is 40 instead of 20. The new public `engineering_agent.rulepack` adapter validates the mapping, binds matched observations to their rule sources, and calls the existing `evaluate_scoring_snapshot` adapter and extracted scoring engine.

The resulting component score changes from 80 to 60, and the explicitly selected main-girder/bearing scope changes from 81.46 to 64.39. No whole-bridge grade is published. The unchanged observation context has the same input digest; the changed pack has a different rule digest. `snapshot_reuse_status` rejects the old snapshot under the new pack and accepts the recomputed snapshot's context match.

Try changing the v2 main-girder deduction to 35: its component score becomes 65 without changing the scoring code. A changed rule still invalidates reuse if its version label was accidentally left unchanged. Unmatched rules, invalid deductions, absent observation sources, and changed inputs have regression tests.

This is a bounded demonstration adapter, not production standards governance. Its hashes identify supplied content; they neither authenticate a source nor prove that a rule is valid. Reuse checking is an explicit function call, not a background invalidation service. Re-evaluating an old natural-language report requires passing its claims through the appropriate verifier again.

## Test the claims

Tests check final text, denied delivery, remaining requirements, actual scoring-engine calls, input sensitivity, stale-result rejection, invalid configurations, and absence of network calls in the in-process scenarios. They do not merely assert that a log contains “verified.” The separate CLI test checks that the full output is valid JSON.

The current examples are bridge-specific reference adapters. Reusing the architecture in another domain requires domain vocabulary, authoritative evidence, applicability rules, formulas, and validators; it is not achieved by changing only the title.
