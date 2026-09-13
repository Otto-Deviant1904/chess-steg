# Benchmark summary

- Games: 1000 (real Lichess corpus: `lichess_2013-01.pgn`, first 1000 standard games; encoding always starts from the standard position)
- Payload: 56 seeded-random bytes per game (480 bits incl. 32-bit header), identical across arms (matched pairs)
- Steering: beta=1, window W=10 plies
- Global seed: 20260913

## Per-arm metrics

| Metric | arm1 integer baseline | arm2 arithmetic plain | arm3 arithmetic steered |
|---|---|---|---|
| Mean BPM | 4.2312 | 4.7487 | 4.7662 |
| Std BPM | 0.1666 | 0.1343 | 0.1371 |
| Decode error rate | 0.000% | 0.000% | 0.000% |
| Mean plies used | 113.6 | 101.2 | 100.8 |
| Mean encode+decode CPU runtime (s/game) | 0.0910 | 0.0836 | 0.0838 |
| Capacity failure rate | 4.8% | 3.7% | 2.4% |

## Improvement

- arm3 vs arm1 (integer baseline): 12.64% BPM improvement
- arm3 vs arm2 (plain arithmetic): 0.37% BPM improvement

## Paired statistics (arm3 steered vs arm1 integer baseline)

- BPM: n=928, t=74.917, df=927, p=0.000e+00, Cohen's d=2.459
- Runtime: t=-18.378, p=1.462e-64, Cohen's d=-0.603

## Other paired comparisons

- BPM arm2 plain vs arm1 integer: p=0.000e+00, d=2.478
- BPM arm3 steered vs arm2 plain: p=8.777e-03, d=0.086
