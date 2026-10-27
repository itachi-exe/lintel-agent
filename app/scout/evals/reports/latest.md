# Scout eval report

Generated 2026-10-27T07:26:43+00:00 by `python3 -m app.scout.evals`, corpus v1, registry v1.

```
metric                 value  target   n    result   
---------------------  -----  -------  ---  ---------
intent_top1            0.981  >= 0.92  105  pass     
intent_top3_recall     1.000  >= 0.98  105  pass     
injection_precision    1.000  >= 0.95  27   pass     
injection_recall       1.000  >= 0.90  27   pass     
tool_precision         0.966  >= 0.88  29   pass     
clarification_rate     0.063  <= 0.15  126  pass     
override_frequency     0.000  <= 0.08  0    pass     
multi_intent_recall    1.000  >= 0.85  7    pass     
edge_status_accuracy   0.929  >= 0.90  14   pass     
intent_top1[area]      1.000  >= 0.80  21   pass     
intent_top1[building]  0.974  >= 0.80  77   pass     
intent_top1[owner]     1.000  >= 0.80  7    pass     
intent_top1_blind      0.900  info     30   not gated
holdout_overlap        0      == 0     194  pass     
```

194 examples (105 intent, 30 blind, 27 injection + 11 benign in that file, 7 multi-intent, 14 edge). Stage 1 scan 2.88 ms (budget 16). override_frequency is 0 by construction: the corpus has no override data. intent_top1_blind is a set written after tuning stopped and never tuned against.

Result: all gates pass

## Misses

- int-building.violations-02: 'whats broken in this building according to the city' -> clarify building.violations 0.52
- int-building.violations-02: tools expected ['socrata.direct'] got []
- int-building.owner-06: 'who is in charge of this property' -> clarify GENERAL 0.387
- edge-11: edge expected {'status': 'act', 'intent': 'building.owner'} got clarify GENERAL
