---
name: template-sync
description: Bring the template's machinery (tools, tests, page templates, skills, workflows, the permission profile, the conformance page) into this space file by file, keeping any file this space changed. Use as the weekly routine or when a fix in the template must reach this space.
---

# template-sync: the template's machinery, file by file

Shape changes ship with their migration (vision section 16); this routine is how they, and
every smaller fix, reach an instance. A company's own content is never touched: the
registry, pages, `space.yaml`, `rules.md`, `identity/`, `.github/CODEOWNERS` stay as they are.

## When

- Scheduled: weekly (the `routines` block of `space.yaml`, default Monday early).
- By hand: when a fix in the template must reach this space now.

## Run

`python _meta/tools/template_sync.py <root>` (adds `--from owner/name` or `--from <path>`
when the template is not recorded yet; `--check` lists what would change and changes
nothing). The template is read through a shallow clone into a temp directory; nothing is
pushed. The sync script lands the copied files like any other edit.

## What the tool already did

Compared every machinery file with the version this space received last time
(`_meta/state/template-version.yaml`): a missing file was added, an unchanged-here file was
updated, a file this space changed was kept and listed as `kept`. Nothing was deleted. One
run line (`template-sync`) was appended.

## Judgment left to you

For each `kept` file: is the local change still wanted, or should the template's version
replace it? That is a question for the space owner: write it as one item in
`_inbound/needs-you/template-sync.yaml` (kind `question`, recommendation: keep or take) and
move on. Never overwrite a kept file yourself.

## Never

Never edit the template from here; never delete a file; never run git; never touch
`space.yaml`, `rules.md`, `identity/`, the registry or any page.

## Scheduled invocation

Work only in `<root>`. Run `python _meta/tools/template_sync.py <root>`. If it prints `kept`
files, write one Needs-you item per file to `_inbound/needs-you/template-sync.yaml` with your
recommendation. Report the summary line. Do not commit; the sync script lands the result.

## Behaviour scenarios

`_meta/tools/tests/test_template_sync.py`.
