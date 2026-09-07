.PHONY: test check

test:
	python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q skills tests
