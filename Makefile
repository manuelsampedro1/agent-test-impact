.PHONY: test lint build smoke

test:
	PYTHONPATH=src python3 -m unittest discover -s tests

lint:
	python3 -m py_compile src/agent_test_impact/*.py tests/*.py

build:
	python3 -m compileall -q src tests

smoke:
	PYTHONPATH=src python3 -m agent_test_impact examples/mixed-change.diff --min-score 0
	PYTHONPATH=src python3 -m agent_test_impact examples/mixed-change.diff --format json --fail-on-missing >/tmp/agent-test-impact-smoke.json || test $$? -eq 1
