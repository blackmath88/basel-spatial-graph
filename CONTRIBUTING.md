# Contributing

Start with a reproducible problem or a concrete improvement for someone using the project. Small, focused changes are easiest to review.

## Report a problem

Use the [issue form](https://github.com/blackmath88/basel-spatial-graph/issues/new?template=problem.yml). Include the page or command, expected behaviour, what happened, and the revision or environment when known. For a data or evidence concern, include the source record and explain the uncertainty. Remove personal information and credentials from logs and screenshots.

For a larger feature, open an issue describing the user need before implementation. A proposal is not an approved change of scope.

## Verify a change

From `basel-spatial-graph-v0/`, install `requirements.txt` in a virtual environment and run `pytest`. Optional preparation and MCP dependencies enable additional tests; record any skips. Do not refresh or re-freeze the committed data as a side effect of a documentation change.

Run `python3 .github/check-public-docs.py` from the repository root to check local entry-document links and image paths. This check does not contact external services or verify anchors.

For documentation-only edits, check local links, commands against actual scripts, image captions and rendered readability. Run a documented example when its behaviour is affected. State what you checked and what you could not check; never report an unexecuted command as passing.

## Open a pull request

Explain the user-facing problem, what changed and the evidence for the result. Keep application behaviour, data refreshes, dependency upgrades and presentation changes in separate reviewable scopes. Follow existing repository instructions and the Weavr operating contract where applicable. Do not include generated output unless the repository explicitly versions it.

## Sensitive reports

Do not put credentials, personal data or exploitable security details into public issues. Use GitHub's private vulnerability reporting option if available. If it is unavailable, ask the owner for a private reporting route without posting the sensitive details. No response-time commitment is implied.
