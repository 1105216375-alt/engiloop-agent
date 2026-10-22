# Contributing

Contributions are provided under Apache-2.0 unless separately agreed. Confirm that you may contribute your code and test data. Do not copy private project records, proprietary standards corpora, employer logos or third-party code without an appropriate license.

Please include:

1. The input, intended behavior and evidence boundary.
2. A small synthetic positive example and a failing/insufficient-evidence example.
3. Assertions on final state or delivered text, not only intermediate log messages.
4. Clear provenance: extracted implementation, public adaptation, interface or unsupported feature.
5. Tests for any changes to candidate acceptance, rollback, memory approval or output release.

```bash
python -m pip install -e '.[dev]'
python -m pytest
python -m examples.all_mechanisms
python tools/check_release.py
```

Keep the default examples offline. Do not silently load a developer's configuration, previous state database or model credentials. Preserve reported values and computed values separately; never tune scores to achieve a desired narrative.
