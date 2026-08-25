.PHONY: setup init check test serve search

setup:
	python3 -m pip install -e .

init:
	python3 -m src.literature_agent init

check:
	python3 -m src.literature_agent check

test:
	python3 -m unittest discover -s tests -p 'test_*.py'

serve:
	python3 -m src.literature_agent serve

search:
	@test -n "$(QUERY)" || (echo '用法: make search QUERY="关键词"' >&2; exit 2)
	python3 -m src.literature_agent search "$(QUERY)"
