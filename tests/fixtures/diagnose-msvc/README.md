# diagnose-msvc

A synthetic project whose one install step fails the way a native Python build does
on a Windows machine without the C++ build tools.

## Install

```bash
python -c "import sys; print('error: Microsoft Visual C++ 14.0 or greater is required'); sys.exit(1)"
```
