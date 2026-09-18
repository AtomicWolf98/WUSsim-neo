# WUSsim neo

`neo` is the clean standalone 6G DL-WUS Case-1 simulation project. It contains only the active source, project-local virtual-environment installer, frozen configuration, final results, and structured documentation. Earlier rounds and inactive experimental modules are excluded.

The detailed documentation is in Chinese at [doc/00_DOCUMENT_INDEX_ZH.md](doc/00_DOCUMENT_INDEX_ZH.md).

## Quick start on Windows

Use Python 3.11 or 3.12.

1. Run `setup.bat` to create `neo/.venv` and install the pinned dependency.
2. Run `run_cases_1_to_6.bat` for 96 auditable runs: FTP3/IM × Case 1-1..1-6 × eight paired seeds.
3. Run `run_theory_audit.bat` for the 64-seed nominal matrix, ten one-factor sensitivity slices, and 128 paired counterfactual runs used for gain decomposition.
4. Run `verify_project.bat`; the final line must be `[neo] PASS`.

Each run replaces its fixed result directory instead of accumulating round folders.

## Main finding

Under the frozen nominal assumptions, the 64-seed Case 1-6 saving versus Case 1-1 is 55.67% for FTP3 (95% CI 52.06–59.04%) and 53.66% for IM (51.34–55.76%), with 100% completion. These figures assume a 5 ms independent-WUS period, a two-symbol WUS occasion, an 8 ms post-data timer, 1% detection-error inputs, and the company-proposal EE measurement power model.

The counterfactual is essential: changing only to independent WUS while retaining the baseline 100 ms activity tail increases power by 36.60% for FTP3 and 122.97% for IM. Shortening the tail from 100 ms to 8 ms creates the dominant positive contribution; EE measurement adds 7.45 and 33.27 baseline-normalized percentage points for FTP3 and IM. A 4 ms WUS occasion or impaired detection can make sparse IM negative.

The project therefore supports bounded discussion of timer, measurement, detection, and monitoring-cost interactions. It does not establish a standard-agreed WUS saving.

## Key outputs

- `results/case1_to_6/summary.csv`: 12-row eight-seed matrix.
- `results/case1_to_6/raw/*.json`: 96 complete event, action, energy, and packet ledgers.
- `results/theory_audit/nominal_summary.csv`: 64-seed headline estimates.
- `results/theory_audit/sensitivity_summary.csv`: one-factor sensitivity results.
- `results/gain_decomposition/gain_decomposition.csv`: trigger/timer/EE waterfall.
- `doc/10_RESULTS_GAIN_DECOMPOSITION_ZH.md`: detailed interpretation.

## Scope

This is a single-UE connected-mode relative-power model with abstract detection and fixed-rate service. It has no calibrated PHY waveform/channel/BLER, complete Rel-19 protocol stack, multi-UE scheduler, gNB-energy model, or absolute battery model. The case axes come from meeting notes; many numerical values are company proposals or explicit project assumptions.
