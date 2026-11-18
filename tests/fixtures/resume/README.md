# resume

A synthetic project for `up --resume`. The second install step fails until a `.ok`
file exists in the repo.

## Install

```bash
python -c "print('step one done')"
python -c "import os, sys; sys.exit(0 if os.path.exists('.ok') else 1)"
```
