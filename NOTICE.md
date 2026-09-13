# Third-party notices

## web/vendor/chess.js

- **Project:** chess.js
- **Author:** Jeff Hlywa (jhlywa@gmail.com)
- **License:** BSD 2-Clause
- **Why it is vendored:** the web app is a static, build-free, network-free
  page. Loading chess.js from a CDN would make the app fail offline and would
  send a request to a third party every time someone hides a message.

The file is vendored **unmodified**, with its original license header intact
(see the `@license` block near the end of `web/vendor/chess.js`). It is not
covered by this repository's MIT license.

To update it, replace the file with a release from
<https://github.com/jhlywa/chess.js> and re-run the interop test:

```bash
.venv/bin/python -m pytest test_interop.py -q
```

That test is what guarantees the vendored version stays bit-compatible with
`codec.py`, so a bad upgrade fails loudly instead of silently producing
undecodable carrier games.
