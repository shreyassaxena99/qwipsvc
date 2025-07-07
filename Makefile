.PHONY: service test format env

service:
	uvicorn svc.main:app --reload

test:
	docker-compose up --build test-runner

format:
	ruff check svc tests --fix
	black svc tests
	isort .
	pylint svc tests

env:
	cp .env.example .env
