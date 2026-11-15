# e2e: datasette

- result: **FAILED**
- commit: `4b8cb5e14d4ab8ce0f23b766dffc0ef0dd8c6a83`
- host: windows, python 3.12.11
- duration: 10s, rtfmup exit code 1
- up line: never reached
- answers given: variant=- run=- component=-
- sandbox: npm global prefix and cache inside the scratch dir; stdin closed (high-risk steps declined)

## Steps (`.rtfmup/state.json`)

| step | status | exit |
|---|---|---|
| step-1 | ok | 0 |
| step-2 | not_run |  |
| step-3 | failed | 3 |
| step-4 | pending |  |
| step-5 | pending |  |

## Last 60 lines of output

```text
[1/4] Resolving target
[2/4] Analyzing
[3/4] Probing this host (read-only)
[4/4] Plan
rtfmup plan (windows x86_64, component root)

Steps (nothing has been run)                                                         
#  kind         risk    dir  command                              source             
1  install      medium  .    npm ci                               package-lock.json:1
2  install      medium  .    brew install datasette               README.md:33       
3  install      medium  .    pip install datasette                README.md:37       
4  run          low     .    datasette serve path/to/database.db  README.md:43       
5  healthcheck  low     .    Wait for http://localhost:8001       README.md:45       

Why some steps are not low risk
  step 1 (medium): downloads npm packages
  step 2 (medium): installs a toolchain
  step 3 (medium): downloads Python packages

Health check: http://localhost:8001

Gaps (rtfmup will not guess these)
  Steps for another OS (listed, not run)
    - `brew install datasette` needs a different OS than windows (README.md:33).

not run by rtfmup: brew install datasette
3 step(s) will run on this machine:
  1. npm ci  [medium risk: downloads npm packages]
  2. pip install datasette  [medium risk: downloads Python packages]
  3. datasette serve path/to/database.db
--yes: approved.
[1/3] install � npm ci

added 46 packages, and audited 47 packages in 4s

7 packages are looking for funding
  run `npm fund` for details

2 low severity vulnerabilities

To address all issues (including breaking changes), run:
  npm audit fix --force

Run `npm audit` for details.
npm notice
npm notice New major version of npm available! 11.20.0 -> 12.2.2
npm notice Changelog: https://github.com/npm/cli/releases/tag/v12.2.2
npm notice To update run: npm install -g npm@12.2.2
npm notice
done (exit 0) in 5.3s
[2/3] install � pip install datasette
ERROR: Could not find an activated virtualenv (required).
FAILED (exit 3) in 1.5s
rtfmup: error: pip install datasette: failed.
```
