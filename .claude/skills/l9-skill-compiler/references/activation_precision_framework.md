<!-- L9_META
l9_schema: 1
parent: l9-skill-compiler
layer: reference
owner: igor_beylin
status: active
version: 3.0.0
updated: 2026-06-15
/L9_META -->

# Activation Precision Framework

## Purpose

Make skills selective. A smart skill is as clear about when not to activate as when to activate.

## Required Model

```yaml
activation_model:
  strong_signals: []
  weak_signals: []
  reject_signals: []
  false_positive_risks: []
  specificity_score: 0_to_5
  false_positive_risk_score: 0_to_5
```

## Scoring

Specificity score:

- 5: concrete task phrases plus artifact/tool/file signals
- 4: concrete task phrases with clear scope
- 3: mostly clear but possible overlap
- 2: broad terms likely to over-trigger
- 1: generic domain words only
- 0: no usable activation model

False-positive risk score:

- 0: reject signals block common near-misses
- 1: low risk, reject signals present
- 2: moderate overlap risk
- 3: high overlap risk
- 4: likely to trigger incorrectly
- 5: no reject signals

## Exemplary Threshold

Pass requires:

```yaml
specificity_score: ">=4"
false_positive_risk_score: "<=1"
reject_signals_present: true
```

Documentation-only trigger quality does not pass. The compiler must show the signals and scores in `skill_intelligence_report.yaml`.
