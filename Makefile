.PHONY: service test format env

service:
	uvicorn svc.main:app --reload

test:
	pytest tests/ -vv

format:
	ruff check svc tests --fix
	black svc tests
	isort .
	pylint svc tests

env:
	cp .env.example .env
