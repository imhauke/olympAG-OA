# documents

Put the `.docx` files you want to edit here. `uv run docx-agent` lists them in its menu,
together with the three synthetic samples.

Edited copies are saved to `documents/edited/` as `<name>.edited.docx` plus a
`<name>.edited.report.json`. Your originals are never modified, and earlier results are never
overwritten.

Everything in this folder except this README is ignored by git, so your documents stay local.
