# CHANGELOG

## 2026-05-26

### R1 — Fix hardcoded model strings in claude_service.py
**Files**: `claude_service.py`
**Rationale**: `generate_weekly_report()` (line 565) and `analyze_weak_points()` (line 603) hardcoded
`"claude-sonnet-4-6"` directly, bypassing module constants and making cost-control changes ineffective
for those functions (AUDIT §2, ARCHITECTURE §6d).
**Change**: Added `REPORT_MODEL = "claude-opus-4-7"` and `WEAK_POINT_MODEL = "claude-haiku-4-5-20251001"`
constants; replaced the two inline strings with the constants. Effective model values unchanged.
**Rollback**: Revert the two `model=` lines in `generate_weekly_report` and `analyze_weak_points`
back to `"claude-sonnet-4-6"`.
