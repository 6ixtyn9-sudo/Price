# Promotion Decision

Date: 2026-07-03

## Decision
No slice is currently promoted.

## Evidence summary

### XLF 1d
Slice:
`state_ext=stretched_up + state_slope=flat`

Status:
- survived
- positive excess vs best parent
- walk-forward pattern: 1111
- search-wide BH pass: False

Conclusion:
Top watchlist / monitored candidate only.
Not promotable under strict doctrine because it fails search-wide multiple-testing defense.

### XLE 1d
Slice:
`state_ext=stretched_down + state_slope=downtrend`

Status:
- survived
- positive excess vs best parent
- walk-forward pattern: 0110
- search-wide BH pass: True

Conclusion:
Interesting and statistically stronger on search-wide correction than XLF,
but not promotable because walk-forward is mixed/decaying rather than clean.

## Final classification
- Promoted slices: none
- Top watchlist candidate: XLF 1d `state_ext=stretched_up + state_slope=flat`
- Secondary watch candidate: XLE 1d `state_ext=stretched_down + state_slope=downtrend`

## Rule
A slice must not be called promoted unless it simultaneously clears:
- survived
- positive excess vs best parent
- strong walk-forward
- search-wide defensible significance

Current count of fully promotable slices: 0
