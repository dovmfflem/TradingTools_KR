# Repository automation policy

2026-10-23: At the repository owner's request, the GitHub Actions push/PR CI
workflow was removed. Normal commits and pushes do not start Python matrix jobs.
Do not restore automatic CI without the owner's approval.

This does not remove library tests or downstream release verification. ARBI DESK
validates its pinned TradingTools commit in the dedicated Python 3.12.11 WSL
release environment, including package smoke tests. Older Python CI failures
must not be represented as a failure of that packaged runtime.
