---
name: progress
description: Report progress on long-running or background commands to the et-ay status line when ET_AY_PROGRESS is set.
user-invocable: false
---

When you start a long-running or background command (a build, a test suite, a migration, a batch over many files, a loop of uploads), check whether `ET_AY_PROGRESS` is set. If it is empty or unset, skip all of this.

If it is set, report progress by overwriting that file with one line each time something advances:

```bash
printf '%s\n' "3/7 Building docs" > "$ET_AY_PROGRESS"
```

The line takes one of three forms:

- `3/7 Building docs` when you know the number of steps
- `42% Uploading` when you know a percentage
- `Indexing` when the length is unknown

Only the first line is read, so overwrite with `>`, never append. Keep the label short. Put the writes inside the script or loop you run, so updates continue while the command runs in the background, for example:

```bash
total=${#files[@]}; i=0
for f in "${files[@]}"; do
  i=$((i + 1)); printf '%s\n' "$i/$total Converting" > "$ET_AY_PROGRESS"
  convert "$f"
done
```

When the job finishes, write the final count (`7/7 Building docs` or `100% Uploading`) so the status line shows it as done. The status line shows the time since the last write and flags the job if it stops updating.
