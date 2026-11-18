# diagnose-pep668

A synthetic project whose one install step fails the way pip does on a Linux
distribution whose system Python is externally managed.

## Install

```bash
echo "error: externally-managed-environment" >&2 && false
```
