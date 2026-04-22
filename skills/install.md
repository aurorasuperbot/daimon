# Install

```bash
pip install daimon
daimon --version
```

Verify the engine works without an identity:

```bash
daimon match tests/fixtures/sample_loadout_a.json tests/fixtures/sample_loadout_b.json --seed 0000000000000000000000000000000000000000000000000000000000000001
```

You should see deterministic output. Same seed = same result, every time.

If the install fails:
- Python 3.11+ required
- On Debian/Ubuntu you may need `python3-dev` for the `cryptography` build
- Re-run with `pip install -v daimon` to see what's failing
