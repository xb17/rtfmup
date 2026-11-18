# diagnose-xcode

A synthetic project whose one install step fails the way a build does on a Mac
without the Command Line Tools.

## Install

```bash
echo "xcrun: error: invalid active developer path (CommandLineTools), missing xcrun" >&2 && false
```
