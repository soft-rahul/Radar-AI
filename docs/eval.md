# Analyzer accuracy against human labels

- Answers checked by a human: **30** (run 2, stratified across 3 engines × 3 languages, fixed seed). Method: the human marked each brand the analyzer found as right or wrong and wrote down any product or brand name they saw that was not highlighted.
- Brand mentions counted per (answer, brand) pair: 128 correct, 0 false alarms, 0 misses
- **Precision 100.0%** (of the brands the analyzer found, how many were really named)
- **Recall 100.0%** (upper bound: only misses the checker noticed are counted) (of the brands really named, how many the analyzer found)

## Misses
- none

## False alarms
- none

## Names the checker wrote in that are not in the study's brand list
Product names (e.g. Biozyme) belong to a listed brand and mean the alias list needs extending.
- none

## Coverage audit (brand list completeness)
After the human check, plain code searched the 30 checked answers for product-like phrases (<Name> Whey/Protein/Nutrition) that no listed alias matched, and for known names not on the list. Every listed brand was found wherever it appeared; the gaps were brands missing from the list.

- TrueBasics: 6 mentions not counted before the fix
- Ultimate Nutrition: 4 mentions not counted before the fix
- FuelOne: 4 mentions not counted before the fix
- OZiva: 1 mentions not counted before the fix

Recall against every brand actually named, before the fix: 128/143 = **89.5%**. Added the four brands to study/whey.json. They were found by the code audit, not marked by the human checker.
