# Examples

## `example_carrier.pgn`

A complete carrier game produced by `codec.py`. Decode it with:

```python
from codec import TrueArithmeticCodec
print(TrueArithmeticCodec().decode_pgn(open("examples/example_carrier.pgn").read()).decode())
# The password is swordfish
```

or straight from the command line:

```bash
.venv/bin/python -c "
from codec import TrueArithmeticCodec
print(TrueArithmeticCodec().decode_pgn(open('examples/example_carrier.pgn').read()).decode())"
```

The decoder takes no arguments beyond the PGN: the codec, the steering mode,
`beta` and the window all travel in the headers. Passing `opts=None` is what a
real recipient does.

The same file decodes in the browser — open `web/index.html`, paste it into
**Reveal a message**, and press the button.
